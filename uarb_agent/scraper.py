"""Browser automation for the UARB public documents database.

The site is a FileMaker WebDirect app (Vaadin under the hood): there is no
document API, element ids are generated per layout, and every click is a round
trip to the server. So this module drives a real browser, finds elements by
their visible text and position, and waits on the UI state the user would see.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .models import DOCUMENT_TYPES, DownloadedDocument, FetchResult, MatterMetadata

log = logging.getLogger(__name__)

UARB_URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"

# Stop trying further rows after this many failed downloads in one request.
_MAX_FAILURES = 5
# If this many files in a row are over the size budget and none has fit yet,
# assume the tab is all large media (hearing recordings run ~150 MB) and stop.
_MAX_OVERSIZED_BEFORE_GIVING_UP = 3

_TAB_RE = re.compile(r"^(%s)\s*-\s*(\d+)$" % "|".join(re.escape(t) for t in DOCUMENT_TYPES))
_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
# File-type cells: ".pdf" on the document tabs, "Pdf"/"PDF" on Transcripts.
_FILE_TYPE_RE = re.compile(
    r"^\.?(pdf|docx?|xlsx?|pptx?|csv|txt|rtf|zip|mp3|mp4|m4a|wav|wma|wmv|avi|mov)$", re.I
)

# Header label on the matter page -> the metadata fields stacked under it.
_HEADER_FIELDS = {
    "matter no status": ("matter_number", "status"),
    "title - description": ("title",),
    "type category": ("type", "category"),
    "date received": ("date_received",),
    "decision date": ("final_date",),
    "date final submissions": ("final_date",),
    "outcome": ("outcome",),
}

_SNAPSHOT_JS = """() => {
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const info = e => {
    const r = e.getBoundingClientRect();
    return {text: (e.innerText || '').replace(/\\s+/g, ' ').trim(), x: r.x, y: r.y, w: r.width, h: r.height};
  };
  const pick = sel => [...document.querySelectorAll(sel)].filter(visible).map(info);
  return {labels: pick('.v-label'), fields: pick('.fm-textarea'), buttons: pick('button')};
}"""

_MARK_GRIDS_STALE_JS = """() => document.querySelectorAll('table[aria-rowcount]')
  .forEach(t => t.setAttribute('data-uarb-stale', t.getAttribute('aria-rowcount')))"""

# Resolves to the new grid's row count once a grid that was not marked stale
# (or whose row count changed) has rendered data rows.
_FRESH_GRID_JS = """() => {
  const grid = [...document.querySelectorAll('table[aria-rowcount]')].find(t =>
    t.getAttribute('data-uarb-stale') !== t.getAttribute('aria-rowcount'));
  if (!grid || !grid.querySelector('tr.v-grid-row-has-data')) return false;
  return grid.getAttribute('aria-rowcount');
}"""

_ROWS_READY_JS = """() => [...document.querySelectorAll('tr.v-grid-row-has-data')].some(tr =>
  [...tr.querySelectorAll('button')].some(b => /go get it/i.test(b.innerText || '')) &&
  [...tr.querySelectorAll('.fm-textarea')].some(f => (f.innerText || '').trim()))"""

_ROWS_JS = """() => [...document.querySelectorAll('tr.v-grid-row-has-data')].map((tr, index) => {
  const row = tr.getBoundingClientRect();
  const fields = [...tr.querySelectorAll('.fm-textarea')].map(f => {
    const r = f.getBoundingClientRect();
    return {text: (f.innerText || '').replace(/\\s+/g, ' ').trim(), x: r.x - row.x, y: r.y - row.y, w: r.width};
  }).filter(f => f.w > 0 && f.text);
  const hasButton = [...tr.querySelectorAll('button')].some(b => /go get it/i.test(b.innerText || ''));
  return {index, top: row.y, height: row.height, fields, hasButton};
})"""


class ScraperError(RuntimeError):
    """The UARB site did not behave as expected."""


class MatterNotFoundError(ScraperError):
    """The site reported that no matter matches the number."""


class FileTooLargeError(ScraperError):
    """A download was stopped because it would not fit in the size budget."""


class UARBScraper:
    """Fetches matter metadata and documents. Use as a context manager."""

    def __init__(self, headless: bool = True, timeout_ms: int = 60_000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    def __enter__(self) -> UARBScraper:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        # A tall viewport makes the document grid render more rows at once.
        context = self._browser.new_context(
            viewport={"width": 1400, "height": 1600}, accept_downloads=True
        )
        context.set_default_timeout(self.timeout_ms)
        self._page = context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("UARBScraper must be used as a context manager")
        return self._page

    # -- public API ---------------------------------------------------------

    def fetch(
        self,
        matter_number: str,
        document_type: str,
        dest_dir: Path,
        limit: int = 10,
        max_bytes: int | None = None,
    ) -> FetchResult:
        """Open a matter, read its metadata, and download up to `limit` documents.

        With `max_bytes`, downloads stop once they would exceed that total, and
        documents that don't fit are reported in `result.too_large`.
        """
        if document_type not in DOCUMENT_TYPES:
            raise ValueError(f"Unknown document type: {document_type!r}")
        metadata = self.open_matter(matter_number)
        result = FetchResult(metadata=metadata, document_type=document_type)
        if result.available == 0 or limit <= 0:
            return result
        listed = self.open_tab(document_type)
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._download_documents(result, dest_dir, min(limit, listed), listed, max_bytes)
        return result

    def open_matter(self, matter_number: str) -> MatterMetadata:
        """Search for a matter via "Go Directly to Matter" and return its metadata."""
        page = self.page
        # The site has occasionally answered "No Records Found" for a matter
        # that exists, so confirm a miss with a second search before trusting it.
        # The retry runs on the search page the dialog's OK button returns to:
        # reloading while that dialog is open breaks the WebDirect session.
        for attempt in range(2):
            if self._search(matter_number, reload=attempt == 0):
                break
            log.info("Site reported no records for %s (attempt %d)", matter_number, attempt + 1)
        else:
            raise MatterNotFoundError(f"No matter found for {matter_number}")

        # Fields can fill in a moment after the tabs appear.
        metadata = self._read_metadata(matter_number)
        for _ in range(10):
            if metadata.title and metadata.date_received:
                break
            page.wait_for_timeout(500)
            metadata = self._read_metadata(matter_number)
        return metadata

    def open_tab(self, document_type: str) -> int:
        """Switch to a document-type tab, wait for its list, and return its row count."""
        page = self.page
        # Each tab is its own layout and the old list stays on screen until the
        # new one arrives, so mark the current grid and wait for a different one.
        page.evaluate(_MARK_GRIDS_STALE_JS)
        tab = page.locator("button", has_text=re.compile(rf"^{re.escape(document_type)}\s*-\s*\d+$"))
        tab.first.click()
        try:
            rows = int(page.wait_for_function(_FRESH_GRID_JS).json_value())
        except PlaywrightTimeoutError:
            # Possible if two tabs share a layout and have equal counts.
            log.warning("No new list appeared for %s; using the grid on screen", document_type)
            counts = page.evaluate(
                "() => [...document.querySelectorAll('table[aria-rowcount]')].map(t => +t.getAttribute('aria-rowcount'))"
            )
            if not counts:
                raise ScraperError(f"The {document_type} tab did not show a document list")
            rows = max(counts)
        page.evaluate(
            "() => document.querySelectorAll('[data-uarb-stale]').forEach(t => t.removeAttribute('data-uarb-stale'))"
        )
        if rows:
            # Rows appear before their cells are filled in.
            page.wait_for_function(_ROWS_READY_JS)
        log.info("%s tab lists %d documents", document_type, rows)
        return rows

    # -- matter page --------------------------------------------------------

    def _search(self, matter_number: str, reload: bool = True) -> bool:
        """Run a "Go Directly to Matter" search; False if the site found nothing."""
        page = self.page
        if reload:
            log.info("Opening %s on %s", matter_number, UARB_URL)
            # WebDirect keeps background traffic going, so "networkidle" is not
            # a reliable signal here; wait for the UI elements instead.
            page.goto(UARB_URL, wait_until="domcontentloaded")

        field = page.locator(".fm-textarea", has=page.locator(".placeholder", has_text="eg M01234"))
        field.wait_for(state="visible")
        self._type_into(field, matter_number)
        self._button_beside(field, "Search").click()

        tabs = page.locator("button", has_text=_TAB_RE)
        not_found = page.locator(".v-window", has_text="No Records Found")
        tabs.or_(not_found).first.wait_for(state="visible")
        if not not_found.is_visible():
            return True
        # "Click OK to return to Search page." The site shows this dialog twice
        # in a row, so keep confirming until no new one appears.
        for _ in range(4):
            try:
                not_found.first.wait_for(state="visible", timeout=3_000)
            except PlaywrightTimeoutError:
                break
            dialog = not_found.first.element_handle()
            not_found.first.locator(".v-button", has_text=re.compile(r"^\s*OK\s*$")).first.click()
            dialog.wait_for_element_state("hidden")
        return False

    def _type_into(self, field: Locator, text: str) -> None:
        # WebDirect turns the field into a contenteditable only after the click
        # reaches the server; typing before that is silently dropped.
        page = self.page
        field.click()
        page.wait_for_function(
            "() => document.activeElement && document.activeElement.isContentEditable"
        )
        # Replace anything left from a previous search.
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.type(text, delay=30)
        typed = page.evaluate("() => document.activeElement.innerText.trim()")
        if typed != text:
            raise ScraperError(f"Matter search box contains {typed!r} instead of {text!r}")

    def _button_beside(self, field: Locator, label: str) -> Locator:
        """The page has several "Search" buttons; pick the one on the field's row."""
        box = field.bounding_box()
        if box is None:
            raise ScraperError("Matter search box is not visible")
        centre = box["y"] + box["height"] / 2
        buttons = self.page.locator("button", has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))
        for i in range(buttons.count()):
            b = buttons.nth(i).bounding_box()
            if b and abs(b["y"] + b["height"] / 2 - centre) < b["height"]:
                return buttons.nth(i)
        raise ScraperError(f"Could not find the {label!r} button next to the matter search box")

    def _read_metadata(self, matter_number: str) -> MatterMetadata:
        snap = self.page.evaluate(_SNAPSHOT_JS)
        metadata = MatterMetadata(matter_number=matter_number)

        tab_top = None
        for button in snap["buttons"]:
            m = _TAB_RE.match(button["text"])
            if m:
                metadata.counts[m.group(1)] = int(m.group(2))
                tab_top = button["y"] if tab_top is None else min(tab_top, button["y"])
        missing = [t for t in DOCUMENT_TYPES if t not in metadata.counts]
        if missing:
            raise ScraperError(f"Matter page is missing tabs: {', '.join(missing)}")

        headers = [
            (label, _HEADER_FIELDS[key])
            for label in snap["labels"]
            if (key := label["text"].lower()) in _HEADER_FIELDS
        ]
        if not headers:
            log.warning("Matter header labels not found; metadata will be incomplete")
            return metadata
        header_bottom = max(h["y"] + h["h"] for h, _ in headers)
        header_texts = {h["text"] for h, _ in headers}

        # Each value sits under the header column it overlaps most; columns
        # with two fields (e.g. Type / Category) are ordered top to bottom.
        columns: dict[int, list[dict[str, Any]]] = {}
        for value in snap["labels"] + snap["fields"]:
            if not value["text"] or value["text"] in header_texts:
                continue
            if value["y"] < header_bottom - 2 or (tab_top is not None and value["y"] >= tab_top):
                continue
            best, best_overlap = None, 0.0
            for i, (h, _) in enumerate(headers):
                overlap = min(value["x"] + value["w"], h["x"] + h["w"]) - max(value["x"], h["x"])
                if overlap > best_overlap:
                    best, best_overlap = i, overlap
            if best is not None:
                columns.setdefault(best, []).append(value)

        for i, values in columns.items():
            names = headers[i][1]
            values.sort(key=lambda v: (v["y"], v["x"]))
            for name, value in zip(names, values):
                setattr(metadata, name, value["text"])
        # The search box already holds the canonical number; prefer the page's
        # copy only if it looks like a matter number.
        if not re.fullmatch(r"M\d{5}", metadata.matter_number):
            metadata.matter_number = matter_number
        return metadata

    # -- document tab -------------------------------------------------------

    def _visible_rows(self) -> list[dict[str, Any]]:
        """Rendered rows, top to bottom, read once the grid has stopped changing.

        The grid recycles its <tr> elements while it re-renders (after a tab
        loads or a dialog closes), so a row picked mid-render can turn into a
        different document by the time it is clicked.
        """
        previous = None
        for _ in range(12):
            raw = self.page.evaluate(_ROWS_JS)
            snapshot = [(r["index"], round(r["top"]), [f["text"] for f in r["fields"]]) for r in raw]
            if snapshot == previous:
                break
            previous = snapshot
            self.page.wait_for_timeout(250)
        rows = [r for r in raw if r["hasButton"] and r["fields"]]
        for row in rows:
            row.update(_describe_row(row["fields"]))
        return sorted(rows, key=lambda r: r["top"])

    def _download_documents(
        self,
        result: FetchResult,
        dest_dir: Path,
        wanted: int,
        listed: int,
        max_bytes: int | None,
    ) -> None:
        """Work down the list until `wanted` documents are handled or it runs out.

        A document is handled once it is downloaded or found too large for the
        size budget. A row that errors doesn't count; the next row replaces it.
        """
        page = self.page
        rows = page.locator("tr.v-grid-row-has-data")
        seen: set[str] = set()
        attempts: dict[str, int] = {}
        used_bytes = 0
        stalled_scrolls = 0
        while len(result.documents) + len(result.too_large) < wanted and len(seen) < listed:
            if len(result.failures) >= _MAX_FAILURES:
                log.warning("Giving up after %d failed downloads", len(result.failures))
                break
            if not result.documents and len(result.too_large) >= _MAX_OVERSIZED_BEFORE_GIVING_UP:
                log.warning("The first %d documents are all too large; stopping", len(result.too_large))
                break
            visible = self._visible_rows()
            pending = [r for r in visible if r["key"] not in seen]
            if not pending:
                # The grid is virtualised: scroll to make it render more rows.
                if stalled_scrolls >= 3:
                    log.warning("Stopped after %d rows; no more rows rendered", len(seen))
                    break
                stalled_scrolls += 1
                grid = page.locator(".v-grid-tablewrapper").first
                grid.hover()
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(1500)
                continue
            stalled_scrolls = 0
            row = pending[0]
            key = row["key"]
            attempts[key] = attempts.get(key, 0) + 1
            label = " - ".join(p for p in (row["doc_no"], row["title"]) if p) or row["date"]
            done = len(result.documents) + len(result.too_large)
            log.info("Downloading %d/%d: %s", done + 1, wanted, label)
            budget = None if max_bytes is None else max_bytes - used_bytes
            neighbours = {r["doc_no"] for r in visible if r["doc_no"] and r["key"] != key}
            try:
                files = self._go_get_it(
                    rows.nth(row["index"]), dest_dir, budget, row["doc_no"], neighbours
                )
            except FileTooLargeError as exc:
                log.info("Skipping %s: %s", label, exc)
                seen.add(key)
                result.too_large.append(label)
                self._dismiss_dialogs()
                continue
            except (PlaywrightError, ScraperError, OSError) as exc:
                self._dismiss_dialogs()
                reason = str(exc).splitlines()[0]
                if attempts[key] < 2:
                    # The site occasionally ignores a click. Leaving the row
                    # unseen retries it after a fresh scan of the grid.
                    log.info("Retrying %s after: %s", label, reason)
                    continue
                log.warning("Download failed for %s: %s", label, reason)
                seen.add(key)
                result.failures.append(label)
                continue
            seen.add(key)
            doc = DownloadedDocument(
                doc_no=row["doc_no"], title=row["title"], date=row["date"], files=files
            )
            used_bytes += doc.size_bytes
            result.documents.append(doc)

    def _go_get_it(
        self,
        row: Locator,
        dest_dir: Path,
        max_bytes: int | None,
        doc_no: str = "",
        neighbours: set[str] | None = None,
    ) -> list[Path]:
        """Click GO GET IT on a row and download every file its dialog offers.

        On the document tabs the dialog names files after their doc number, so
        a dialog offering a neighbouring row's file means the click landed on
        the wrong row; that raises instead of saving the wrong document.
        """
        page = self.page
        row.locator("button", has_text=re.compile(r"go get it", re.I)).first.click()
        dialog = page.locator(".v-window").first
        dialog.wait_for(state="visible", timeout=30_000)
        if "Export Field to File" in dialog.inner_text():
            # Transcripts and recordings ask for a filename first. The suggested
            # name has spaces and commas ("...September 12, 2022.pdf"), which
            # makes the download button silently do nothing, so make it URL-safe.
            name_box = dialog.locator("input.v-textfield").first
            name_box.fill(_url_safe_filename(name_box.input_value()))
            dialog.locator(".v-button", has_text=re.compile(r"^\s*OK\s*$")).first.click()
            dialog = page.locator(".v-window", has_text="Download Files").first
            dialog.wait_for(state="visible")
        if "Download Files" not in dialog.inner_text():
            raise ScraperError(f"Unexpected dialog: {dialog.inner_text().strip()[:200]}")

        buttons = dialog.locator(".fm-download-button")
        try:
            buttons.first.wait_for(state="visible", timeout=10_000)
        except PlaywrightTimeoutError:
            raise ScraperError("Download dialog listed no files") from None
        captions = [Path(buttons.nth(i).inner_text().strip()).stem for i in range(buttons.count())]
        if doc_no and doc_no not in captions and set(captions) & (neighbours or set()):
            raise ScraperError(f"Clicked the wrong row: expected {doc_no}, dialog offered {captions}")
        files: list[Path] = []
        try:
            for i in range(buttons.count()):
                with page.expect_download() as info:
                    buttons.nth(i).click()
                download = info.value
                # The browser can't cap a download's size and the server sends
                # no Content-Length, so cancel the browser's copy and stream the
                # same session URL ourselves, stopping once it outgrows the budget.
                download.cancel()
                target = _unique_path(dest_dir / _safe_filename(download.suggested_filename))
                budget = None if max_bytes is None else max_bytes - sum(f.stat().st_size for f in files)
                self._stream(download.url, target, budget)
                log.debug("Saved %s (%d bytes) from %s", target.name, target.stat().st_size, download.url)
                files.append(target)
        except BaseException:
            for f in files:
                f.unlink(missing_ok=True)
            raise
        dialog.locator(".v-button", has_text="Close").first.click()
        dialog.wait_for(state="detached")
        return files

    def _stream(self, url: str, target: Path, max_bytes: int | None) -> None:
        """Download `url` with the browser session's cookies into `target`."""
        page = self.page
        cookies = page.context.cookies(url)
        request = urllib.request.Request(
            url,
            headers={
                "Cookie": "; ".join(f"{c['name']}={c['value']}" for c in cookies),
                "User-Agent": page.evaluate("() => navigator.userAgent"),
            },
        )
        written = 0
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_ms / 1000) as response:
                with target.open("wb") as out:
                    while chunk := response.read(1 << 16):
                        written += len(chunk)
                        if max_bytes is not None and written > max_bytes:
                            raise FileTooLargeError(
                                f"{target.name} is over the {max(max_bytes, 0) / 2**20:.1f} MB left in the budget"
                            )
                        out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    def _dismiss_dialogs(self) -> None:
        """Close any modal left open by a failed download so the next row is clickable."""
        page = self.page
        for _ in range(3):
            dialog = page.locator(".v-window").first
            if not dialog.is_visible():
                return
            button = dialog.locator(".v-button", has_text=re.compile(r"^(Close|OK|Cancel)$"))
            try:
                button.first.click(timeout=5_000)
                dialog.wait_for(state="detached", timeout=10_000)
            except PlaywrightTimeoutError:
                page.keyboard.press("Escape")


def _describe_row(fields: list[dict[str, Any]]) -> dict[str, str]:
    """Work out doc number, title and date from a row's fields by position.

    Rows have two lines. The document tabs put the doc number (e.g. 102674 or
    H-1) at the top left with the security level below it; Transcripts and
    Recordings have no doc number and put the date there instead.
    """
    top = min(f["y"] for f in fields)
    first_line = sorted((f for f in fields if f["y"] - top < 15), key=lambda f: f["x"])
    date = next((f["text"] for f in fields if _DATE_RE.match(f["text"])), "")
    doc_no = ""
    if first_line and first_line[0]["x"] < 60 and not _DATE_RE.match(first_line[0]["text"]):
        doc_no = first_line[0]["text"]
    title = " - ".join(
        f["text"]
        for f in first_line
        if f["x"] >= 60 and not _DATE_RE.match(f["text"]) and not _FILE_TYPE_RE.match(f["text"])
    )
    key = doc_no or f"{date}|{title}"
    return {"doc_no": doc_no, "title": title, "date": date, "key": key}


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "document"


def _url_safe_filename(name: str) -> str:
    stem, dot, ext = name.strip().rpartition(".")
    if not dot or not re.fullmatch(r"[A-Za-z0-9]{1,6}", ext):
        stem, ext = name.strip(), ""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.") or "document"
    return f"{stem}.{ext}" if ext else stem


def _unique_path(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate
