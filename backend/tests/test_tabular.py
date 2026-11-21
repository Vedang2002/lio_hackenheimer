import json

import pytest

from app.db import connect
from app.tabular import TabularIngest, read_table

LEGACY = (
    "EBELN;EBELP;LIFNR;MATNR;MENGE;NETPR;BEDAT\r\n"
    "4500318401;00010;19;CMP-GAM-003;44000;1,5200;08/12/2024\r\n"
    "4500318402;00010;10;CMP-GAM-002;20000;5,4000;08/19/2024\r\n"
).encode("cp1252")

LEGACY_MAPPING = {
    "entity": "purchase_order_line",
    "columns": [
        {"column": "EBELN", "field": "po_number", "kind": "text"},
        {"column": "LIFNR", "field": "supplier_id", "kind": "text", "template": "SUP-{:0>4}"},
        {"column": "MATNR", "field": "material_id", "kind": "text"},
        {"column": "MENGE", "field": "quantity", "kind": "number"},
        {"column": "NETPR", "field": "unit_price", "kind": "number", "decimal_comma": True},
        {"column": "BEDAT", "field": "order_date", "kind": "date", "date_format": "%m/%d/%Y"},
    ],
    "comment": "",
}


class Mapper:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, 0

    def __call__(self, path, header, rows):
        self.calls += 1
        return self.mapping


@pytest.fixture
def conn():
    return connect(":memory:")


def records(conn, entity=None):
    q = "SELECT entity, data FROM records" + (" WHERE entity = ?" if entity else "") + " ORDER BY id"
    return [json.loads(r["data"]) for r in conn.execute(q, (entity,) if entity else ())]


def test_reads_semicolons_and_skips_blank_lines():
    header, rows, delimiter = read_table(b"a;b\n1;2\n\n;\n3;\n")
    assert (header, delimiter) == (["a", "b"], ";")
    assert rows == [{"a": "1", "b": "2"}, {"a": "3", "b": None}]


def test_legacy_export_becomes_canonical_records(conn):
    result = TabularIngest(conn, Mapper(LEGACY_MAPPING)).ingest("soony/erp/legacy.csv", "sha", LEGACY)
    assert (result.status, result.entity, result.rows) == ("ok", "purchase_order_line", 2)
    assert records(conn)[0] == {
        "po_number": "4500318401", "supplier_id": "SUP-0019", "material_id": "CMP-GAM-003",
        "quantity": 44000, "unit_price": 1.52, "order_date": "2024-08-12", "_extra": {"EBELP": "00010"},
    }


def test_same_header_reuses_the_mapping_and_a_renamed_column_asks_again(conn):
    mapper = Mapper(LEGACY_MAPPING)
    ingest = TabularIngest(conn, mapper)
    ingest.ingest("a.csv", "1", LEGACY)
    ingest.ingest("b.csv", "2", LEGACY)
    assert mapper.calls == 1
    renamed = LEGACY.replace(b"MENGE", b"QTY")
    mapper.mapping = {**LEGACY_MAPPING, "columns": [
        {**c, "column": "QTY"} if c["column"] == "MENGE" else c for c in LEGACY_MAPPING["columns"]]}
    assert ingest.ingest("a.csv", "3", renamed).status == "ok"
    assert mapper.calls == 2


def test_reingesting_a_file_replaces_its_records(conn):
    ingest = TabularIngest(conn, Mapper(LEGACY_MAPPING))
    ingest.ingest("a.csv", "1", LEGACY)
    ingest.ingest("a.csv", "2", LEGACY.replace(b"44000", b"45000"))
    assert [r["quantity"] for r in records(conn)] == [45000, 20000]


def test_mapping_without_a_required_field_is_rejected_and_rows_stay_out_of_canonical_data(conn):
    broken = {**LEGACY_MAPPING, "columns": [c for c in LEGACY_MAPPING["columns"] if c["field"] != "quantity"]}
    result = TabularIngest(conn, Mapper(broken)).ingest("a.csv", "1", LEGACY)
    assert result.status == "needs_review"
    assert "quantity" in result.detail
    assert records(conn, "purchase_order_line") == []
    assert records(conn, "other")[0]["MENGE"] == "44000"
    assert conn.execute("SELECT COUNT(*) FROM mappings").fetchone()[0] == 0


def test_invented_columns_and_fields_are_dropped_with_a_note(conn):
    mapping = {**LEGACY_MAPPING, "columns": LEGACY_MAPPING["columns"] + [
        {"column": "WERKS", "field": "plant", "kind": "text"},
        {"column": "EBELP", "field": "colour", "kind": "text"},
    ]}
    result = TabularIngest(conn, Mapper(mapping)).ingest("a.csv", "1", LEGACY)
    assert result.status == "ok"
    assert "'WERKS' is not in the file" in result.detail and "'colour' is not a field" in result.detail
    assert "plant" not in records(conn)[0]


def test_unreadable_values_are_kept_raw_and_flagged(conn):
    raw = LEGACY.replace(b"20000;5,4000", b"n/a;5,4000")
    result = TabularIngest(conn, Mapper(LEGACY_MAPPING)).ingest("a.csv", "1", raw)
    assert result.status == "needs_review"  # 1 of 2 rows is above the tolerated share
    bad = records(conn)[1]
    assert bad["quantity"] == "n/a"
    assert bad["_issues"] == ["quantity: could not read 'n/a' as number"]


def test_supplier_is_taken_from_the_folder_when_the_file_has_no_such_column(conn):
    raw = b"component_id,unit_price,primary\nCMP-GAM-010,1037.0,N\n"
    mapping = {"entity": "supplier_component", "comment": "", "columns": [
        {"column": "component_id", "field": "component_id", "kind": "text"},
        {"column": "unit_price", "field": "unit_price", "kind": "number"},
        {"column": "primary", "field": "primary", "kind": "bool"},
    ]}
    TabularIngest(conn, Mapper(mapping)).ingest("suppliers/SUP-0018_Pune_Diecast_Works/components.csv", "1", raw)
    assert records(conn) == [{"component_id": "CMP-GAM-010", "unit_price": 1037, "primary": False, "supplier_id": "SUP-0018"}]


def test_unknown_files_are_kept_as_they_are(conn):
    result = TabularIngest(conn, Mapper({"entity": "other", "columns": [], "comment": "ports"})).ingest(
        "soony/master/ports.csv", "1", b"locode,name\nDEHAM,Hamburg\n")
    assert (result.status, result.entity) == ("ok", "other")
    assert records(conn) == [{"locode": "DEHAM", "name": "Hamburg"}]
