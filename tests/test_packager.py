import zipfile

from uarb_agent.models import DownloadedDocument
from uarb_agent.packager import archive_name, build_zip


def make_doc(tmp_path, doc_no, title, size, name=None):
    path = tmp_path / (name or f"{doc_no or title}.pdf")
    path.write_bytes(bytes(range(256)) * (size // 256))
    return DownloadedDocument(doc_no=doc_no, title=title, date="01/01/2025", files=[path])


def test_zip_contains_descriptively_named_files(tmp_path):
    docs = [make_doc(tmp_path, "102674", "Board Order", 2048), make_doc(tmp_path, "H-1", "Application: Part 1/2", 2048)]
    package = build_zip(docs, tmp_path / "out.zip", max_bytes=10_000_000)
    assert package.included == docs and package.omitted == []
    with zipfile.ZipFile(package.path) as zf:
        assert zf.namelist() == ["102674 - Board Order.pdf", "H-1 - Application_ Part 1_2.pdf"]


def test_documents_over_the_size_limit_are_left_out(tmp_path):
    docs = [make_doc(tmp_path, str(i), f"Doc {i}", 40_000) for i in range(3)]
    package = build_zip(docs, tmp_path / "out.zip", max_bytes=100_000)
    assert [d.doc_no for d in package.included] == ["0", "1"]
    assert [d.doc_no for d in package.omitted] == ["2"]
    assert package.path.stat().st_size <= 100_000


def test_no_zip_when_nothing_fits(tmp_path):
    package = build_zip([make_doc(tmp_path, "1", "Huge", 50_000)], tmp_path / "out.zip", max_bytes=1_000)
    assert package.path is None
    assert not (tmp_path / "out.zip").exists()


def test_archive_name_without_doc_number_keeps_site_filename(tmp_path):
    doc = make_doc(tmp_path, "", "September 12, 2022", 256, name="NSUARB-M10431-September_12_2022.pdf")
    assert archive_name(doc, doc.files[0]) == "NSUARB-M10431-September_12_2022.pdf"
