from types import SimpleNamespace

import pytest

from uarb_agent.request_parser import (
    DocumentRequest,
    normalise_document_type,
    parse_request,
    parse_with_rules,
)


@pytest.mark.parametrize(
    ("text", "matter", "doc_type"),
    [
        ("Hi Agent, Can you give me Other Documents files from M12205? Thanks!", "M12205", "Other Documents"),
        ("Please send the key docs for m12383", "M12383", "Key Documents"),
        ("Exhibits for M-10431 please", "M10431", "Exhibits"),
        ("I need the hearing transcripts, matter M 10431", "M10431", "Transcripts"),
        ("Could I get the audio recordings for M10431?", "M10431", "Recordings"),
        ("Transcripts and exhibits for M10431", "M10431", "Transcripts"),
        ("Can you send me documents for M12205?", "M12205", None),
        ("Other Documents please", None, "Other Documents"),
        ("Matter 12205 has a M123 typo", None, None),
    ],
)
def test_rules(text, matter, doc_type):
    assert parse_with_rules(text) == DocumentRequest(matter_number=matter, document_type=doc_type)


def test_normalise_document_type():
    assert normalise_document_type("other documents") == "Other Documents"
    assert normalise_document_type("key docs") == "Key Documents"
    assert normalise_document_type("recording") == "Recordings"
    assert normalise_document_type("hearings") is None


class FakeClient:
    """Stands in for anthropic.Anthropic; returns a canned parse."""

    def __init__(self, parsed, stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason)

        def parse(**kwargs):
            self.calls.append(kwargs)
            return response

        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_claude_result_is_used():
    client = FakeClient(DocumentRequest(matter_number="M12205", document_type="Recordings"))
    result = parse_request("Send me the hearing audio for m-12205", client)
    assert result == DocumentRequest(matter_number="M12205", document_type="Recordings")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is DocumentRequest
    assert call["fallbacks"] == "default"


def test_claude_matter_number_must_appear_in_email():
    client = FakeClient(DocumentRequest(matter_number="M99999", document_type="Exhibits"))
    result = parse_request("Exhibits for M12205 please", client)
    assert result.matter_number == "M12205"


def test_rules_fill_gaps_and_refusals_fall_back():
    client = FakeClient(DocumentRequest(matter_number=None, document_type=None))
    assert parse_request("Exhibits for M12205", client) == DocumentRequest(
        matter_number="M12205", document_type="Exhibits"
    )
    refused = FakeClient(None, stop_reason="refusal")
    assert parse_request("Exhibits for M12205", refused).document_type == "Exhibits"
