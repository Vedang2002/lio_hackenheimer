"""Turn any CSV export into canonical records.

A model proposes how a file's columns map onto the canonical vocabulary; code
validates the proposal and applies it. The proposal is cached per header, so a
renamed column or a new delimiter triggers a fresh mapping and nothing else.
"""

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

from app import llm
from app.entities import ENTITIES, field_names, required_fields

log = logging.getLogger(__name__)

KINDS = ("text", "number", "date", "datetime", "bool")
DELIMITERS = (",", ";", "\t", "|")
MAX_ISSUE_SHARE = 0.2
TRUE, FALSE = {"y", "yes", "true", "1", "x", "j", "ja"}, {"n", "no", "false", "0", "nein"}

MAPPING_SCHEMA = {
    "type": "object",
    "required": ["entity", "columns", "comment"],
    "properties": {
        "entity": {"type": "string", "enum": [*ENTITIES, "other"],
                   "description": "'other' if the file is none of the known entities."},
        "columns": {"type": "array", "items": {
            "type": "object",
            "required": ["column", "field", "kind"],
            "properties": {
                "column": {"type": "string", "description": "Column name exactly as in the file."},
                "field": {"type": "string", "description": "Canonical field of the chosen entity."},
                "kind": {"type": "string", "enum": list(KINDS)},
                "date_format": {"type": ["string", "null"],
                                "description": "strptime format, only when dates are not ISO 8601."},
                "decimal_comma": {"type": "boolean",
                                  "description": "True when comma is the decimal separator and dot groups thousands."},
                "template": {"type": ["string", "null"],
                             "description": "Python format string applied to the raw value when an ID has to be "
                                            "brought into the form used in the rest of the data, e.g. 'SUP-{:0>4}'."},
                "value_map": {"type": ["array", "null"], "items": {
                    "type": "object", "required": ["from", "to"],
                    "properties": {"from": {"type": "string"}, "to": {"type": "string"}}},
                    "description": "Only for codes whose meaning is evident from the data."},
            }}},
        "comment": {"type": "string", "description": "Anything unusual about this file, in one sentence."},
    },
}


@dataclass(frozen=True)
class IngestResult:
    status: str
    entity: str | None
    rows: int
    detail: str


def read_table(raw: bytes) -> tuple[list[str], list[dict[str, str | None]], str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    first = text.split("\n", 1)[0]
    delimiter = max(DELIMITERS, key=first.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [h.strip() for h in next(reader, [])]
    rows = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        values = [v.strip() or None for v in values] + [None] * (len(header) - len(values))
        rows.append(dict(zip(header, values)))
    return header, rows, delimiter


CATALOG_VERSION = hashlib.sha256(json.dumps(ENTITIES, sort_keys=True).encode()).hexdigest()[:12]


def signature(header: list[str], delimiter: str) -> str:
    # The vocabulary is part of the key: when it changes, every file is mapped afresh.
    return hashlib.sha256(json.dumps([CATALOG_VERSION, delimiter, header]).encode()).hexdigest()


def propose_mapping(path: str, header: list[str], rows: list[dict]) -> dict:
    catalog = {name: {"about": e["about"], "fields": e["fields"]} for name, e in ENTITIES.items()}
    prompt = (
        "You prepare a customer's data export for analysis. Decide which canonical entity the file holds and "
        "map its columns onto that entity's fields. Choose an entity only when the file clearly is that kind of "
        "data; fields marked * identify a row, so a file that has no column for them is a different entity. If "
        "nothing fits, answer 'other'. Map a column only when its meaning matches and never map two columns to "
        "one field; columns you leave out are kept as they are. Look at the sample values to decide number and "
        "date formats.\n\n"
        f"Canonical entities:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
        f"File: {path}\nColumns: {json.dumps(header, ensure_ascii=False)}\n"
        f"Sample rows:\n{json.dumps(rows[:6], ensure_ascii=False)}"
    )
    return llm.extract([{"role": "user", "content": prompt}], "map_columns",
                       "Record how this file maps onto the canonical schema.", MAPPING_SCHEMA)


def validate_mapping(mapping: dict, header: list[str]) -> tuple[dict | None, list[str]]:
    """Keep what is usable from a proposed mapping. Returns None when it cannot be used at all."""
    entity, problems = mapping.get("entity"), []
    if entity == "other":
        return {"entity": "other", "columns": [], "comment": mapping.get("comment", "")}, problems
    if entity not in ENTITIES:
        return None, [f"unknown entity {entity!r}"]
    columns, seen = [], set()
    for c in mapping.get("columns", []):
        column, field = c.get("column"), c.get("field")
        if column not in header:
            problems.append(f"column {column!r} is not in the file")
        elif field not in field_names(entity):
            problems.append(f"{field!r} is not a field of {entity}")
        elif field in seen:
            problems.append(f"{field!r} mapped twice; kept the first")
        else:
            c = {**c, "kind": c.get("kind") if c.get("kind") in KINDS else "text"}
            if c.get("template"):
                try:
                    c["template"].format("1")
                except (IndexError, KeyError, ValueError):
                    problems.append(f"unusable template {c['template']!r} for {field!r}")
                    c["template"] = None
            seen.add(field)
            columns.append(c)
    missing = [f for f in required_fields(entity) if f not in seen]
    if missing:
        return None, problems + [f"no column for required field(s) {', '.join(missing)}"]
    return {"entity": entity, "columns": columns, "comment": mapping.get("comment", "")}, problems


def convert(value: str, spec: dict):
    """Read one cell as the kind the mapping says it is. Raises ValueError when it is not."""
    kind = spec["kind"]
    if kind == "number":
        s = value.replace(" ", "").replace("\u00a0", "")
        s = s.replace(".", "").replace(",", ".") if spec.get("decimal_comma") else s.replace(",", "")
        n = float(s)
        return int(n) if n.is_integer() else n
    if kind in ("date", "datetime"):
        fmt = spec.get("date_format")
        parsed = datetime.strptime(value, fmt) if fmt else datetime.fromisoformat(value)
        return parsed.date().isoformat() if kind == "date" else parsed.isoformat()
    if kind == "bool":
        if value.lower() in TRUE | FALSE:
            return value.lower() in TRUE
        raise ValueError(value)
    if spec.get("template"):
        value = spec["template"].format(value)
    for pair in spec.get("value_map") or []:
        if pair["from"] == value:
            return pair["to"]
    return value


def apply_mapping(mapping: dict, rows: list[dict]) -> list[dict]:
    if mapping["entity"] == "other":
        return [dict(r) for r in rows]
    records, mapped = [], {spec["column"] for spec in mapping["columns"]}
    for row in rows:
        record, issues = {}, []
        for spec in mapping["columns"]:
            value = row.get(spec["column"])
            if value is None:
                record[spec["field"]] = None
                continue
            try:
                record[spec["field"]] = convert(value, spec)
            except (ValueError, TypeError):
                record[spec["field"]] = value
                issues.append(f"{spec['field']}: could not read {value!r} as {spec['kind']}")
        extra = {k: v for k, v in row.items() if k not in mapped and v is not None}
        if extra:
            record["_extra"] = extra
        if issues:
            record["_issues"] = issues
        records.append(record)
    return records


class TabularIngest:
    def __init__(self, conn: sqlite3.Connection, mapper: Callable[[str, list[str], list[dict]], dict] = propose_mapping):
        self.conn = conn
        self.mapper = mapper

    def _mapping(self, path: str, header: list[str], rows: list[dict], sig: str) -> tuple[dict | None, list[str]]:
        cached = self.conn.execute("SELECT mapping FROM mappings WHERE signature = ?", (sig,)).fetchone()
        if cached:
            return json.loads(cached["mapping"]), []
        mapping, problems = validate_mapping(self.mapper(path, header, rows), header)
        if mapping:
            self.conn.execute(
                "INSERT INTO mappings (signature, example_path, mapping, created_at) VALUES (?, ?, ?, ?)",
                (sig, path, json.dumps(mapping, ensure_ascii=False), _now()),
            )
        return mapping, problems

    def ingest(self, path: str, sha256: str, raw: bytes) -> IngestResult:
        header, rows, delimiter = read_table(raw)
        mapping, problems = self._mapping(path, header, rows, signature(header, delimiter))
        status = "ok"
        if mapping is None:
            # Keep the rows readable for agents and people, but do not let them into the canonical data.
            mapping, status = {"entity": "other", "columns": []}, "needs_review"
        records = apply_mapping(mapping, rows)
        entity = mapping["entity"]

        folder_supplier = re.match(r"suppliers/([^/_]+)_", path)
        if folder_supplier and entity in ENTITIES and "supplier_id" in field_names(entity):
            for r in records:
                r["supplier_id"] = r.get("supplier_id") or folder_supplier.group(1)

        with_issues = sum("_issues" in r for r in records)
        if records and with_issues / len(records) > MAX_ISSUE_SHARE:
            status = "needs_review"
            problems.append(f"{with_issues} of {len(records)} rows have values that could not be read")
        elif with_issues:
            problems.append(f"{with_issues} row(s) have values that could not be read")

        self.conn.execute("DELETE FROM records WHERE source_path = ?", (path,))
        self.conn.executemany(
            "INSERT INTO records (entity, source_path, source_sha256, row_no, data) VALUES (?, ?, ?, ?, ?)",
            [(entity, path, sha256, i, json.dumps(r, ensure_ascii=False)) for i, r in enumerate(records, start=2)],
        )
        result = IngestResult(status, entity, len(records), "; ".join(problems))
        self.conn.execute(
            "INSERT INTO ingest_log (ts, path, sha256, status, entity, rows, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), path, sha256, result.status, result.entity, result.rows, result.detail),
        )
        self.conn.commit()
        if status != "ok":
            log.warning("%s needs review: %s", path, result.detail)
        return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
