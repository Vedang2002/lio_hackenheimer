import io
import json
import zipfile

import pytest
from openpyxl import Workbook

from app.db import connect
from app.documents import DocumentIngest, normalise, parse_quantity, to_content
from app.ingest import ingest_pending

DELIVERY_NOTE = {
    "doc_type": "delivery_note", "language": "pl", "summary": "Delivery note for PO 4500318768.",
    "doc_date": "2026-11-16", "document_no": "WZ-261116-473", "po_numbers": ["4500318768"],
    "lines": [{"material_id": "CMP-GAM-008", "qty_printed": 80000, "qty_handwritten": 8000, "qty_final": 8000}],
    "facts": [],
}


@pytest.fixture
def conn():
    return connect(":memory:")


def records(conn, entity):
    return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM records WHERE entity = ? ORDER BY id", (entity,))]


def test_lines_are_stored_with_the_document_they_came_from(conn):
    status, doc_type, rows, _ = DocumentIngest(conn).store("soony/receiving/DC-HAM/scan.jpg", "sha", DELIVERY_NOTE)
    assert (status, doc_type, rows) == ("ok", "delivery_note", 1)
    (line,) = records(conn, "doc_line")
    assert line["qty_printed"] == 80000 and line["qty_final"] == 8000
    assert (line["document_no"], line["po_numbers"], line["doc_date"]) == ("WZ-261116-473", ["4500318768"], "2026-11-16")
    source = conn.execute("SELECT source_path FROM records").fetchone()["source_path"]
    assert source == "soony/receiving/DC-HAM/scan.jpg"


def test_final_quantity_defaults_to_the_handwritten_correction():
    reading = {**DELIVERY_NOTE, "lines": [{"material_id": "X", "qty_printed": 80000, "qty_handwritten": 8000}]}
    doc, notes = normalise(reading, "scan.jpg")
    assert doc["lines"][0]["qty_final"] == 8000 and notes == []


@pytest.mark.parametrize("written, expected", [
    ("80.000", 80000), ("8 000", 8000), ("64,000", 64000), ("1.250.000", 1250000), ("10.100", 10100),
    ("122000", 122000), ("12,5", 12.5), ("0.75", 0.75), (5600, 5600), ("ca. 500", None), ("", None), (None, None),
])
def test_quantities_are_read_as_written(written, expected):
    assert parse_quantity(written) == expected


def test_quantities_keep_their_original_wording():
    reading = {**DELIVERY_NOTE, "lines": [{"material_id": "X", "qty_printed": "80.000", "qty_handwritten": "8 000", "qty_final": "8 000"}]}
    doc, notes = normalise(reading, "scan.jpg")
    line = doc["lines"][0]
    assert (line["qty_printed"], line["qty_handwritten"], line["qty_final"]) == (80000, 8000, 8000)
    assert line["qty_printed_text"] == "80.000" and notes == []


def test_a_reading_that_contradicts_itself_needs_review(conn):
    reading = {**DELIVERY_NOTE, "lines": [{"material_id": "X", "qty_printed": 80000, "qty_handwritten": 8000, "qty_final": 80000}]}
    status, _, _, detail = DocumentIngest(conn).store("scan.jpg", "sha", reading)
    assert status == "needs_review" and "disagrees" in detail
    assert records(conn, "doc_line")[0]["qty_final"] == 8000  # the handwritten correction wins


def test_an_empty_reading_needs_review(conn):
    status, _, _, detail = DocumentIngest(conn).store("blank.png", "sha", {"doc_type": "other", "summary": " "})
    assert (status, detail) == ("needs_review", "nothing could be read")


def test_unusable_dates_and_types_are_dropped_with_a_note():
    reading = {"doc_type": "telegram", "summary": "s", "as_of": "Saturday 13:00",
               "facts": [{"kind": "deadline", "subject": "x", "statement": "y", "quote": "z", "valid_to": "2026-11-21T09:00:00"}]}
    doc, notes = normalise(reading, "a.pdf")
    assert doc["doc_type"] == "other" and doc["as_of"] is None
    assert doc["facts"][0]["valid_to"] == "2026-11-21T09:00:00"
    assert any("telegram" in n for n in notes) and any("Saturday 13:00" in n for n in notes)


def test_supplier_comes_from_the_folder_when_the_document_does_not_say():
    doc, _ = normalise({"doc_type": "offer", "summary": "s"}, "suppliers/SUP-0028_Karton_Nowak/notices/offer.pdf")
    assert doc["supplier_id"] == "SUP-0028"


def test_rereading_a_file_replaces_its_records(conn):
    ingest = DocumentIngest(conn)
    ingest.store("scan.jpg", "1", DELIVERY_NOTE)
    ingest.store("scan.jpg", "2", {**DELIVERY_NOTE, "lines": []})
    assert records(conn, "doc_line") == []


def test_each_file_type_reaches_the_model_in_a_readable_form():
    assert to_content("a/scan.JPG", b"x")[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert to_content("a/oc.pdf", b"x")[0]["file"]["filename"] == "oc.pdf"
    assert to_content("notes.md", "Grüße".encode())[0]["text"] == "Grüße"

    wb, buf = Workbook(), io.BytesIO()
    wb.active.title = "Status"
    wb.active.append(["Edition", "Packed"])
    wb.active.append(["Standard", "554.000"])
    wb.save(buf)
    assert to_content("s.xlsx", buf.getvalue())[0]["text"] == "[sheet: Status]\nEdition | Packed\nStandard | 554.000"

    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    xml = f"<w:document {ns}><w:body><w:p><w:r><w:t>Collector's </w:t></w:r><w:r><w:t>first.</w:t></w:r></w:p></w:body></w:document>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    assert to_content("memo.docx", buf.getvalue())[0]["text"] == "Collector's first."

    with pytest.raises(ValueError, match="cannot read"):
        to_content("blob.bin", b"\xff\xfe\x00\x81")


def test_pipeline_reads_once_reuses_the_reading_and_retries_failures(conn, tmp_path, monkeypatch):
    (tmp_path / "blobs").mkdir()
    for sha in ("s1", "s2"):
        (tmp_path / "blobs" / sha).write_bytes(b"x")
    conn.execute("INSERT INTO files VALUES ('a/note.jpg', 's1', 1, 't0', 't0', NULL)")
    conn.execute("INSERT INTO files VALUES ('a/bad.pdf', 's2', 1, 't0', 't0', NULL)")
    calls = []

    def reader(path, raw, first_seen):
        calls.append(path)
        if path == "a/bad.pdf" and len(calls) <= 2:
            raise RuntimeError("model unavailable")
        return DELIVERY_NOTE

    monkeypatch.setattr("app.documents.read_document", reader)
    monkeypatch.setattr("app.ingest.DocumentIngest", lambda c: DocumentIngest(c, reader))

    first = dict(ingest_pending(conn, tmp_path))
    assert first["a/note.jpg"].status == "ok" and first["a/bad.pdf"].status == "failed"
    second = dict(ingest_pending(conn, tmp_path))
    assert list(second) == ["a/bad.pdf"] and second["a/bad.pdf"].status == "ok"
    assert ingest_pending(conn, tmp_path) == []
    assert calls.count("a/note.jpg") == 1
