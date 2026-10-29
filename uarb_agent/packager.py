"""Bundle downloaded documents into a ZIP that fits in an email."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .models import DownloadedDocument


@dataclass
class Package:
    path: Path | None
    included: list[DownloadedDocument] = field(default_factory=list)
    # Downloaded but left out because the ZIP would exceed the size limit.
    omitted: list[DownloadedDocument] = field(default_factory=list)


def build_zip(documents: list[DownloadedDocument], zip_path: Path, max_bytes: int) -> Package:
    """Add documents in order while they fit under `max_bytes`."""
    included: list[DownloadedDocument] = []
    omitted: list[DownloadedDocument] = []
    total = 0
    for doc in documents:
        if total + doc.size_bytes <= max_bytes:
            included.append(doc)
            total += doc.size_bytes
        else:
            omitted.append(doc)

    # Compression rarely helps PDFs and adds headers, so check the real size.
    while included:
        _write_zip(included, zip_path)
        if zip_path.stat().st_size <= max_bytes:
            return Package(zip_path, included, omitted)
        omitted.insert(0, included.pop())
    zip_path.unlink(missing_ok=True)
    return Package(None, [], omitted)


def _write_zip(documents: list[DownloadedDocument], zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            for i, file in enumerate(doc.files):
                name = archive_name(doc, file, index=i if len(doc.files) > 1 else None)
                zf.write(file, _dedupe(name, used))


def archive_name(doc: DownloadedDocument, file: Path, index: int | None = None) -> str:
    """'102674 - Board Order.pdf' when there is a doc number, else the site's filename."""
    if not doc.doc_no:
        return file.name
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", doc.title).strip(" .")[:80].rstrip(" .")
    stem = f"{doc.doc_no} - {title}" if title else doc.doc_no
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)
    if index is not None:
        stem = f"{stem} ({index + 1})"
    return f"{stem}{file.suffix}"


def _dedupe(name: str, used: set[str]) -> str:
    candidate, n = name, 2
    stem, dot, ext = name.rpartition(".")
    while candidate.lower() in used:
        candidate = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
        n += 1
    used.add(candidate.lower())
    return candidate
