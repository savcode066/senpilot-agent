from uarb_agent.scraper import _describe_row, _url_safe_filename


def field(text, x, y, w=100):
    return {"text": text, "x": x, "y": y, "w": w}


def test_other_documents_row():
    row = _describe_row([
        field("102674", 6, 3, 111),
        field("Board Order", 122, 3, 1049),
        field(".pdf", 1183, 3, 98),
        field("07/08/2026", 1286, 3, 101),
        field("Public", 6, 35, 111),
    ])
    assert row == {"doc_no": "102674", "title": "Board Order", "date": "07/08/2026", "key": "102674"}


def test_exhibit_row_with_confidential_security():
    row = _describe_row([
        field("General Rate Application - Direct Evidence - Board Confidential", 122, 3, 1049),
        field("01/27/2022", 1286, 3, 101),
        field("Board Only", 6, 35, 111),
        field("N-2(BC)", 6, 3, 111),
        field(".pdf", 1183, 3, 98),
    ])
    assert row["doc_no"] == "N-2(BC)"
    assert row["title"] == "General Rate Application - Direct Evidence - Board Confidential"


def test_transcript_row_has_no_doc_number():
    row = _describe_row([
        field("09/12/2022", 6, 3, 104),
        field("September 12, 2022", 115, 3, 254),
        field("Evening Session", 369, 3, 254),
        field("Pdf", 623, 3, 254),
        field("Public", 6, 35, 104),
    ])
    assert row["doc_no"] == ""
    assert row["title"] == "September 12, 2022 - Evening Session"
    assert row["date"] == "09/12/2022"
    assert row["key"] == "09/12/2022|September 12, 2022 - Evening Session"


def test_recording_row():
    row = _describe_row([
        field("09/12/2022", 6, 3, 104),
        field("M10431 – NS Power 2022 GRA - Monday", 115, 3, 1056),
    ])
    assert row["doc_no"] == ""
    assert row["title"] == "M10431 – NS Power 2022 GRA - Monday"


def test_url_safe_filename_keeps_extension():
    assert _url_safe_filename("NSUARB-M10431-September 12, 2022.pdf") == "NSUARB-M10431-September_12_2022.pdf"
    assert _url_safe_filename("NSUARB-M10431-September 12, 2022 - EVENING SESSION.pdf") == (
        "NSUARB-M10431-September_12_2022_-_EVENING_SESSION.pdf"
    )
    assert _url_safe_filename("") == "document"
    assert _url_safe_filename("Hearing, Day 1") == "Hearing_Day_1"
