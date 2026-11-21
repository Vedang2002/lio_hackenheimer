"""Read documents (scans, PDFs, spreadsheets, Word, text) into typed facts with a source.

Half of what Soony knows sits in paper somebody scanned. A model records what each
document says, never what it implies; code normalises the result and stores every
line and statement with the path it came from, so a finding can always be traced
back to a page.
"""

import base64
import hashlib
import io
import json
import logging
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from typing import Callable
from xml.etree import ElementTree

from app import llm

log = logging.getLogger(__name__)

IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
MAX_TEXT_CHARS = 60_000
MAX_SHEET_ROWS = 400
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

DOC_TYPES = ["delivery_note", "order_confirmation", "shipping_advice", "packing_list", "inspection_report",
             "supplier_notice", "offer", "price_list",
             "contract", "policy", "org_chart", "memo", "launch_plan", "status_report", "shipment_tracker",
             "notes", "news", "other"]
FACT_KINDS = ["availability", "capacity", "offer", "price", "deadline", "delay", "quality", "release",
              "approval_rule", "role", "priority", "obligation", "penalty", "bank_details", "risk", "other"]

_num = {"type": ["number", "null"]}
_str = {"type": ["string", "null"]}
_date = {"type": ["string", "null"], "description": "ISO 8601 date or datetime"}

DOC_SCHEMA = {
    "type": "object",
    "required": ["doc_type", "language", "summary", "po_numbers", "lines", "facts"],
    "properties": {
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "language": {"type": "string", "description": "ISO 639-1 code of the main language"},
        "issuer": {**_str, "description": "Company or person who wrote the document"},
        "supplier_id": {**_str, "description": "Supplier id such as SUP-0010, only if the document states it"},
        "doc_date": {**_date, "description": "Date the document was issued"},
        "document_no": {**_str, "description": "The document's own number"},
        "po_numbers": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string", "description": "What this document says, in English, at most two sentences"},
        "as_of": {"type": ["string", "null"], "description": "For plans and status reports: the moment the figures "
                  "describe, as an ISO 8601 datetime in local time without a UTC offset. Resolve a weekday or "
                  "'today' with the first-seen date."},
        "lines": {"type": "array", "description": "Item lines for purchased parts: delivery notes, confirmations, "
                  "price lists, parts tables. Never finished products.", "items": {
            "type": "object", "required": ["material_id"], "properties": {
                "material_id": _str, "description": _str,
                "qty_ordered": {**_str, "description": "Quantity ordered, where the document states it separately "
                                                       "from what was confirmed, shipped or delivered"},
                "qty_printed": {**_str, "description": "Quantity confirmed, shipped or delivered as printed or typed, character for character, "
                                                       "with its separators, e.g. '80.000' or '8 000'"},
                "qty_handwritten": {**_str, "description": "Handwritten quantity that corrects the printed one, "
                                                           "character for character"},
                "qty_final": {**_str, "description": "Quantity the document finally states, character for character"},
                "unit_price": _num, "currency": _str,
                "date": {**_date, "description": "Confirmed, delivery or validity date of this line"},
                "revision": {**_str, "description": "Drawing or artwork revision stated for this item"},
                "lot": _str, "remark": _str}}},
        "shipments": {"type": "array", "description": "Rows of a shipment list", "items": {
            "type": "object", "required": ["shipment_id"], "properties": {
                "shipment_id": _str, "reference": {**_str, "description": "Container or AWB number"},
                "supplier": _str, "po_numbers": {"type": "array", "items": {"type": "string"}},
                "mode": _str, "etd": _date, "eta": _date, "status": _str, "comment": _str}}},
        "pack_status": {"type": "array", "description": "For plans and status reports: one entry per finished "
                        "product (edition) with all its pack-out figures. Use this, not lines, for finished "
                        "products.", "items": {
            "type": "object", "required": ["product_id"], "properties": {
                "product_id": _str, "day_one_demand": _num, "packed": _num, "still_to_pack": _num,
                "rate_per_hour": _num, "line_status": _str}}},
        "shifts": {"type": "array", "description": "Working windows of the packing lines", "items": {
            "type": "object", "required": ["day"], "properties": {
                "day": {"type": "string", "description": "ISO date if it can be told, else the weekday"},
                "start": {**_str, "description": "HH:MM"}, "end": {**_str, "description": "HH:MM"}, "lines": _str}}},
        "holds": {"type": "array", "description": "Stock or lots the document says failed inspection, are blocked, "
                  "or may be used only under a condition", "items": {
            "type": "object", "required": ["material_id", "reason"], "properties": {
                "material_id": _str, "gr_number": {**_str, "description": "Goods receipt number of the lot"},
                "lot": _str, "quantity": _num, "reason": {"type": "string"},
                "release_condition": {**_str, "description": "What has to happen before the lot may be used"},
                "quote": {**_str, "description": "The passage, word for word"}}}},
        "facts": {"type": "array", "description": "Every other statement that someone could act on", "items": {
            "type": "object", "required": ["kind", "subject", "statement", "quote"], "properties": {
                "kind": {"type": "string", "enum": FACT_KINDS},
                "subject": {"type": "string", "description": "Id or name of the material, supplier, person or topic"},
                "statement": {"type": "string", "description": "The statement in plain English"},
                "quantity": _num, "unit": _str, "amount": _num, "currency": _str,
                "valid_from": _date, "valid_to": _date,
                "quote": {"type": "string", "description": "The passage, word for word, in the original language"}}}},
        "handwritten_or_stamped": {**_str, "description": "Handwriting and stamps on a scan, as they read"},
    },
}

PROMPT = (
    "You read documents from the shared drive of a games publisher's procurement team: scans from the receiving "
    "dock, supplier paperwork in several languages, contracts, policies, plans and memos. Record what the document "
    "says and nothing it does not say. Copy ids, numbers and dates exactly. Where a printed quantity was struck out "
    "and corrected by hand, record both. Write summaries and statements in English and quote the original wording. "
    "Leave out sections that do not apply. Numbers written with a dot or space as thousands separator are whole "
    "numbers. When a document says a lot failed inspection, is blocked, or may be used only under a condition, "
    "record that under holds as well, with the goods receipt or lot number it names."
)

QUANTITY_KEYS = ("qty_ordered", "qty_printed", "qty_handwritten", "qty_final")
_GROUPED = re.compile("^[0-9]{1,3}([.,][0-9]{3})+$")
_DECIMAL = re.compile("^[0-9]+[.,][0-9]+$")


def parse_quantity(value) -> int | float | None:
    """Read a quantity the way it was written. Paperwork groups thousands with dots, commas or spaces;
    leaving that to a model turns 65.000 pieces into 65."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    s = "".join(ch for ch in value if not ch.isspace() and ch not in "'’")
    if s.isdigit():
        return int(s)
    if _GROUPED.match(s):
        return int(s.replace(".", "").replace(",", ""))
    if _DECIMAL.match(s):
        return float(s.replace(",", "."))
    return None


# A reading is only reused while the instructions it was made with are unchanged.
READER_VERSION = hashlib.sha256((PROMPT + json.dumps(DOC_SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]


def _sheet_text(raw: bytes) -> str:
    from openpyxl import load_workbook

    out = []
    for ws in load_workbook(io.BytesIO(raw), data_only=True, read_only=True).worksheets:
        out.append(f"[sheet: {ws.title}]")
        for n, row in enumerate(ws.iter_rows(values_only=True)):
            if n >= MAX_SHEET_ROWS:
                out.append("... (more rows)")
                break
            cells = ["" if c is None else str(c) for c in row]
            if any(cells):
                out.append(" | ".join(cells))
    return "\n".join(out)


def _word_text(raw: bytes) -> str:
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml"))
    paragraphs = ("".join(t.text or "" for t in p.iter(WORD_NS + "t")) for p in root.iter(WORD_NS + "p"))
    return "\n".join(p for p in paragraphs if p.strip())


def to_content(path: str, raw: bytes) -> list[dict]:
    """Present a file to the model in the form that loses the least."""
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in IMAGE_TYPES:
        data = base64.b64encode(raw).decode()
        return [{"type": "image_url", "image_url": {"url": f"data:{IMAGE_TYPES[ext]};base64,{data}"}}]
    if ext == "pdf":
        data = base64.b64encode(raw).decode()
        return [{"type": "file", "file": {"filename": path.rsplit("/", 1)[-1],
                                          "file_data": f"data:application/pdf;base64,{data}"}}]
    if ext in ("xlsx", "xlsm"):
        text = _sheet_text(raw)
    elif ext == "docx":
        text = _word_text(raw)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError(f"cannot read files of type .{ext}") from None
    return [{"type": "text", "text": text[:MAX_TEXT_CHARS]}]


def read_document(path: str, raw: bytes, first_seen: str) -> dict:
    context = f"File: {path}\nFirst seen on the shared drive: {first_seen}"
    content = [{"type": "text", "text": f"{PROMPT}\n\n{context}"}, *to_content(path, raw)]
    return llm.extract([{"role": "user", "content": content}], "record_document",
                       "Record what the document says.", DOC_SCHEMA)


def _iso(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def normalise(doc: dict, path: str) -> tuple[dict, list[str]]:
    """Bring a reading into a dependable shape; report what had to be dropped."""
    notes = []
    doc = {**doc}
    if doc.get("doc_type") not in DOC_TYPES:
        notes.append(f"unknown doc_type {doc.get('doc_type')!r}")
        doc["doc_type"] = "other"
    folder_supplier = re.match(r"suppliers/([^/_]+)_", path)
    if folder_supplier and not doc.get("supplier_id"):
        doc["supplier_id"] = folder_supplier.group(1)

    def clean_dates(item: dict, keys: tuple[str, ...], where: str) -> dict:
        item = {**item}
        for k in keys:
            if item.get(k) is not None:
                parsed = _iso(item[k])
                if parsed is None:
                    notes.append(f"{where}: {k} {item[k]!r} is not a date")
                # Dates without a time keep their short form.
                item[k] = parsed[:10] if parsed and len(str(item[k]).strip()) <= 10 else parsed
        return item

    doc = clean_dates(doc, ("doc_date", "as_of"), "document")
    for section, keys in (("lines", ("date",)), ("shipments", ("etd", "eta")), ("pack_status", ()),
                          ("shifts", ()), ("holds", ()), ("facts", ("valid_from", "valid_to"))):
        items = doc.get(section) or []
        doc[section] = [clean_dates(i, keys, section) for i in items if isinstance(i, dict)]
    for line in doc["lines"]:
        for k in QUANTITY_KEYS:
            if isinstance(line.get(k), str):
                line[f"{k}_text"] = line[k]
                line[k] = parse_quantity(line[k])
                if line[k] is None and line[f"{k}_text"].strip():
                    notes.append(f"line {line.get('material_id')}: {k} {line[f'{k}_text']!r} is not a quantity")
        printed, written, final = line.get("qty_printed"), line.get("qty_handwritten"), line.get("qty_final")
        # What the paper finally says is decided here, not by the model: a correction by hand wins.
        expected = written if written is not None else printed
        if expected is not None:
            line["qty_final"] = expected
            if final is not None and final != expected:
                notes.append(f"line {line.get('material_id')}: final quantity {final} disagrees with {expected}")
    return doc, notes


SECTIONS = {"lines": "doc_line", "shipments": "doc_shipment", "pack_status": "doc_pack_status",
            "shifts": "doc_shift", "holds": "doc_hold", "facts": "doc_fact"}
CONTEXT_KEYS = ("doc_type", "supplier_id", "doc_date", "document_no", "po_numbers", "as_of")


class DocumentIngest:
    def __init__(self, conn: sqlite3.Connection, reader: Callable[[str, bytes, str], dict] = read_document):
        self.conn = conn
        self.reader = reader

    def cached(self, sha256: str) -> dict | None:
        row = self.conn.execute(
            "SELECT data FROM extractions WHERE sha256 = ? AND version = ?", (sha256, READER_VERSION)).fetchone()
        return json.loads(row["data"]) if row else None

    def read(self, path: str, sha256: str, raw: bytes, first_seen: str) -> dict:
        """The slow part: safe to run in a worker thread, touches no database."""
        return self.reader(path, raw, first_seen)

    def store(self, path: str, sha256: str, reading: dict) -> tuple[str, str, int, str]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.conn.execute("INSERT OR REPLACE INTO extractions (sha256, version, data, created_at) VALUES (?, ?, ?, ?)",
                          (sha256, READER_VERSION, json.dumps(reading, ensure_ascii=False), now))
        doc, notes = normalise(reading, path)
        context = {k: doc.get(k) for k in CONTEXT_KEYS}
        self.conn.execute("DELETE FROM records WHERE source_path = ?", (path,))
        rows = []
        for section, entity in SECTIONS.items():
            for n, item in enumerate(doc[section], start=1):
                rows.append((entity, path, sha256, n, json.dumps({**context, **item}, ensure_ascii=False)))
        self.conn.executemany(
            "INSERT INTO records (entity, source_path, source_sha256, row_no, data) VALUES (?, ?, ?, ?, ?)", rows)
        self.conn.execute(
            """INSERT OR REPLACE INTO documents (path, sha256, doc_type, language, supplier_id, doc_date, summary, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (path, sha256, doc["doc_type"], doc.get("language"), doc.get("supplier_id"), doc.get("doc_date"),
             doc.get("summary"), json.dumps(doc, ensure_ascii=False)),
        )
        empty = not rows and not (doc.get("summary") or "").strip()
        status = "needs_review" if empty or any("disagrees" in n for n in notes) else "ok"
        detail = "; ".join((["nothing could be read"] if empty else []) + notes)
        self.conn.execute(
            "INSERT INTO ingest_log (ts, path, sha256, status, entity, rows, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now, path, sha256, status, doc["doc_type"], len(rows), detail),
        )
        self.conn.commit()
        return status, doc["doc_type"], len(rows), detail
