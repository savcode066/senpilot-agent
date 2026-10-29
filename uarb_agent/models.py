"""Plain data containers shared across the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DOCUMENT_TYPES: tuple[str, ...] = (
    "Exhibits",
    "Key Documents",
    "Other Documents",
    "Transcripts",
    "Recordings",
)


def parse_us_date(value: str) -> date | None:
    """Parse the MM/DD/YYYY dates the UARB site displays."""
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except (ValueError, AttributeError):
        return None


@dataclass
class MatterMetadata:
    matter_number: str
    title: str = ""
    status: str = ""
    type: str = ""
    category: str = ""
    date_received: str = ""
    # Shown as "Decision Date" on the matter page and "Date Final Submissions"
    # on the document tabs; the challenge calls it the final filing date.
    final_date: str = ""
    outcome: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_documents(self) -> int:
        return sum(self.counts.values())

    @property
    def initial_filing(self) -> date | None:
        return parse_us_date(self.date_received)

    @property
    def final_filing(self) -> date | None:
        return parse_us_date(self.final_date)


@dataclass
class DownloadedDocument:
    doc_no: str
    title: str
    date: str
    files: list[Path] = field(default_factory=list)

    @property
    def size_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.files)


@dataclass
class FetchResult:
    metadata: MatterMetadata
    document_type: str
    documents: list[DownloadedDocument] = field(default_factory=list)
    # Labels of documents that errored, and of ones too large to email.
    failures: list[str] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)

    @property
    def available(self) -> int:
        return self.metadata.counts.get(self.document_type, 0)
