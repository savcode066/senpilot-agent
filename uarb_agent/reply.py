"""Compose the agent's email replies.

Replies are built from a template rather than generated, so every count and
date in them comes straight from the site.
"""

from __future__ import annotations

from datetime import date

from .models import DOCUMENT_TYPES, UARB_URL, FetchResult
from .packager import Package
from .request_parser import DocumentRequest

SIGNATURE = "Best regards,\nSenpilot Agent"

_SINGULAR = {
    "Exhibits": "Exhibit",
    "Key Documents": "Key Document",
    "Other Documents": "Other Document",
    "Transcripts": "Transcript",
    "Recordings": "Recording",
}


def greeting_name(display_name: str) -> str:
    first = display_name.strip().split(" ")[0].strip("\"'") if display_name else ""
    return first if first.isalpha() else "there"


def join_words(items: list[str], conjunction: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


def count_phrase(n: int, plural: str) -> str:
    return f"{n} {_SINGULAR.get(plural, plural.removesuffix('s')) if n == 1 else plural}"


def format_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def describe_matter(result: FetchResult) -> str:
    md = result.metadata
    sentences = []
    if md.title:
        article = "" if md.title.lower().startswith("the ") else "the "
        sentences.append(f"{md.matter_number} is about {article}{md.title}.")
    else:
        sentences.append(f"Here's what I found for {md.matter_number}.")
    category = md.category
    if category and md.type:
        sentences.append(f"It relates to {category} within the {md.type} category.")
    elif category or md.type:
        sentences.append(f"It falls under the {category or md.type} category.")
    initial, final = md.initial_filing, md.final_filing
    if initial and final:
        sentences.append(
            f"The matter had an initial filing on {format_date(initial)} "
            f"and a final filing on {format_date(final)}."
        )
    elif initial:
        sentences.append(
            f"The matter had an initial filing on {format_date(initial)} and no final filing yet."
        )
    if md.status and md.outcome:
        sentences.append(f"Its status is {md.status}, with the outcome: {md.outcome}.")
    elif md.status:
        sentences.append(f"Its status is {md.status}.")
    return " ".join(sentences)


def describe_counts(result: FetchResult) -> str:
    counts = result.metadata.counts
    found = [count_phrase(counts[t], t) for t in DOCUMENT_TYPES if counts.get(t)]
    missing = [t for t in DOCUMENT_TYPES if not counts.get(t)]
    parts = found + (["no " + join_words(missing, "or")] if missing else [])
    total = result.metadata.total_documents
    return f"I found {join_words(parts)} ({count_phrase(total, 'Documents').lower()} in total)."


def describe_download(
    result: FetchResult, package: Package, size_limit_mb: float | None = None
) -> str:
    doc_type, available = result.document_type, result.available
    attached = len(package.included)
    # Too large either while downloading (scraper) or once zipped (packager).
    too_large = result.too_large + [_label(d.doc_no, d.title, "") for d in package.omitted]
    too_big = f"over the {size_limit_mb:g} MB email limit" if size_limit_mb else "too large to email"
    if available == 0:
        return f"Since there are no {doc_type} in this matter, there's nothing to attach."
    if attached == 0:
        if too_large:
            tried = (
                "the first one I tried is"
                if len(too_large) == 1
                else f"the first {len(too_large)} I tried are each"
            )
            return (
                f"Unfortunately I couldn't attach any {doc_type}: {tried} {too_big} "
                f"({'; '.join(too_large)}), so there's no ZIP this time. "
                f"{_still_listed(result, len(too_large))}"
            )
        return (
            f"Unfortunately I couldn't download any of the {count_phrase(available, doc_type)} "
            f"from the UARB site ({UARB_URL}), so there's no ZIP this time. Please try again later."
        )

    it_them = "it" if attached == 1 else "them"
    if attached == available:
        what = (
            f"the only {_SINGULAR[doc_type]}" if attached == 1 else f"all {available} {doc_type}"
        )
    else:
        what = f"{attached} out of the {available} {doc_type}"
    sentence = f"I downloaded {what} and am attaching {it_them} as a ZIP here."
    if result.email_full:
        sentence += " That's as many as fit in one email."

    notes = []
    if too_large:
        was = "was" if len(too_large) == 1 else "were"
        notes.append(f"{len(too_large)} more {was} {too_big} ({'; '.join(too_large)})")
    if result.failures:
        notes.append(
            f"{len(result.failures)} couldn't be downloaded from the site ({'; '.join(result.failures)})"
        )
    if notes:
        left_out = len(too_large) + len(result.failures)
        sentence += f" Note: {'; '.join(notes)}. {_still_listed(result, left_out)}"
    return sentence


def _still_listed(result: FetchResult, n: int) -> str:
    """Where documents left out of the email can still be found."""
    it = "It's" if n == 1 else "They're"
    return f"{it} still listed under {result.metadata.matter_number} at {UARB_URL}."


def _label(doc_no: str, title: str, date_text: str) -> str:
    text = " - ".join(p for p in (doc_no, title) if p) or "Untitled"
    return f"{text} ({date_text})" if date_text else text


def compose_reply(
    result: FetchResult, package: Package, name: str, size_limit_mb: float | None = None
) -> str:
    paragraphs = [
        f"Hi {name},",
        describe_matter(result),
        f"{describe_counts(result)} {describe_download(result, package, size_limit_mb)}",
        SIGNATURE,
    ]
    return "\n\n".join(paragraphs) + "\n"


def compose_clarification(request: DocumentRequest, name: str) -> str:
    missing = []
    if not request.matter_number:
        missing.append('the matter number (it starts with "M" and has five digits, e.g. M12205)')
    if not request.document_type:
        missing.append(f"which type of documents you need ({join_words(list(DOCUMENT_TYPES), 'or')})")
    understood = []
    if request.matter_number:
        understood.append(f"matter {request.matter_number}")
    if request.document_type:
        understood.append(request.document_type)
    intro = f"I understood you're asking about {' and '.join(understood)}, but I" if understood else "I"
    return (
        f"Hi {name},\n\n"
        f"{intro} couldn't tell {join_words(missing)} from your email. "
        "Could you reply with both? For example: \"Can you give me Other Documents files "
        "from M12205?\"\n\n"
        f"{SIGNATURE}\n"
    )


def compose_not_found(matter_number: str, name: str) -> str:
    return (
        f"Hi {name},\n\n"
        f"I searched the UARB public documents database ({UARB_URL}) for {matter_number}, "
        "but no matter with that number exists. Could you double-check the number and send it again?\n\n"
        f"{SIGNATURE}\n"
    )


def compose_error(name: str, matter_number: str | None = None) -> str:
    about = f" while fetching {matter_number}" if matter_number else ""
    return (
        f"Hi {name},\n\n"
        f"Sorry, I ran into a problem with the UARB website ({UARB_URL}){about} and couldn't complete "
        "your request. Please try again in a little while.\n\n"
        f"{SIGNATURE}\n"
    )
