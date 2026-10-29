from pathlib import Path

from uarb_agent.models import DownloadedDocument, FetchResult, MatterMetadata
from uarb_agent.packager import Package
from uarb_agent.reply import (
    compose_clarification,
    compose_reply,
    describe_counts,
    describe_download,
    describe_matter,
    greeting_name,
)
from uarb_agent.request_parser import DocumentRequest


def example_result(available_counts=None, docs=10):
    counts = available_counts or {
        "Exhibits": 13,
        "Key Documents": 5,
        "Other Documents": 21,
        "Transcripts": 0,
        "Recordings": 0,
    }
    metadata = MatterMetadata(
        matter_number="M12205",
        title="Halifax Regional Water Commission - Windsor Street Exchange Redevelopment Project - $69,270,000",
        status="Closed",
        type="Water",
        category="Capital Expenditure",
        date_received="04/07/2025",
        final_date="10/23/2025",
        outcome="Directions given",
        counts=counts,
    )
    documents = [
        DownloadedDocument(doc_no=str(100000 + i), title=f"Letter {i}", date="05/01/2025", files=[Path(f"{i}.pdf")])
        for i in range(docs)
    ]
    return FetchResult(metadata=metadata, document_type="Other Documents", documents=documents)


def test_matches_challenge_example():
    result = example_result()
    package = Package(Path("M12205.zip"), included=result.documents)
    assert describe_matter(result) == (
        "M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange "
        "Redevelopment Project - $69,270,000. It relates to Capital Expenditure within the "
        "Water category. The matter had an initial filing on April 7, 2025 and a final filing "
        "on October 23, 2025. Its status is Closed, with the outcome: Directions given."
    )
    assert describe_counts(result) == (
        "I found 13 Exhibits, 5 Key Documents, 21 Other Documents, and no Transcripts or "
        "Recordings (39 documents in total)."
    )
    assert describe_download(result, package) == (
        "I downloaded 10 out of the 21 Other Documents and am attaching them as a ZIP here."
    )
    body = compose_reply(result, package, "Sam")
    assert body.startswith("Hi Sam,\n\n")
    assert "Letter 0" not in body
    assert body.endswith("as a ZIP here.\n\nBest regards,\nSenpilot Agent\n")


def test_catch_all_category_is_named():
    result = example_result()
    result.metadata.type = "Municipal Boundaries"
    result.metadata.category = "Other"
    assert "It relates to Other within the Municipal Boundaries category." in describe_matter(result)


def test_all_documents_and_singular_wording():
    result = example_result(
        {"Exhibits": 1, "Key Documents": 0, "Other Documents": 1, "Transcripts": 0, "Recordings": 0}, docs=1
    )
    package = Package(Path("x.zip"), included=result.documents)
    assert describe_counts(result) == (
        "I found 1 Exhibit, 1 Other Document, and no Key Documents, Transcripts, or Recordings "
        "(2 documents in total)."
    )
    assert describe_download(result, package) == (
        "I downloaded the only Other Document and am attaching it as a ZIP here."
    )


def test_nothing_to_attach():
    result = example_result(
        {"Exhibits": 3, "Key Documents": 0, "Other Documents": 0, "Transcripts": 0, "Recordings": 0}, docs=0
    )
    assert describe_download(result, Package(None)) == (
        "Since there are no Other Documents in this matter, there's nothing to attach."
    )


def test_failures_and_size_limit_are_reported():
    result = example_result(docs=3)
    result.failures = ["100099 - Broken"]
    result.too_large = ["100050 - Big Report"]
    package = Package(Path("x.zip"), included=result.documents[:2], omitted=result.documents[2:])
    assert describe_download(result, package, size_limit_mb=18) == (
        "I downloaded 2 out of the 21 Other Documents and am attaching them as a ZIP here. "
        "Note: 2 more were over the 18 MB email limit (100050 - Big Report; 100002 - Letter 2); "
        "1 couldn't be downloaded from the site (100099 - Broken). "
        "They're still listed under M12205 at https://uarb.novascotia.ca/fmi/webd/UARB15."
    )


def test_email_full():
    result = example_result(docs=7)
    result.email_full = True
    package = Package(Path("x.zip"), included=result.documents)
    assert describe_download(result, package, size_limit_mb=18) == (
        "I downloaded 7 out of the 21 Other Documents and am attaching them as a ZIP here. "
        "That's as many as fit in one email."
    )


def test_everything_too_large():
    result = example_result(docs=0)
    result.document_type = "Recordings"
    result.metadata.counts["Recordings"] = 23
    result.too_large = ["Monday", "Monday Evening Session", "Tuesday"]
    assert describe_download(result, Package(None), size_limit_mb=18) == (
        "Unfortunately I couldn't attach any Recordings: the first 3 I tried are each over the "
        "18 MB email limit (Monday; Monday Evening Session; Tuesday), so there's no ZIP this time. "
        "They're still listed under M12205 at https://uarb.novascotia.ca/fmi/webd/UARB15."
    )


def test_clarification_mentions_what_is_missing():
    text = compose_clarification(DocumentRequest(matter_number="M12205"), "there")
    assert "matter M12205" in text
    assert "which type of documents you need" in text
    assert "matter number (it starts" not in text


def test_greeting_name():
    assert greeting_name("Jordan Smith") == "Jordan"
    assert greeting_name('"Lee"') == "Lee"
    assert greeting_name("") == "there"
    assert greeting_name("ops-team@example.com") == "there"
