import json
from datetime import date

import pytest

from app.db import connect
from app.forecast import simulate
from app.truth import Facts, assess, norm_rev

TODAY = date(2026, 11, 21)


def put(conn, entity, path, *rows):
    conn.executemany(
        "INSERT INTO records (entity, source_path, source_sha256, row_no, data) VALUES (?, ?, 'sha', ?, ?)",
        [(entity, path, n, json.dumps(r)) for n, r in enumerate(rows)])


@pytest.fixture
def conn():
    """A small launch: P1 (priority) needs A, B and two M; P2 needs A and C."""
    c = connect(":memory:")
    put(c, "product", "products.csv", {"product_id": "P1", "launch_date": "2026-11-22"}, {"product_id": "P2", "launch_date": "2026-11-22"})
    put(c, "bom", "bom.csv",
        {"parent_id": "P1", "child_id": "A", "qty_per": 1}, {"parent_id": "P1", "child_id": "B", "qty_per": 1},
        {"parent_id": "P1", "child_id": "M", "qty_per": 2},
        {"parent_id": "P2", "child_id": "A", "qty_per": 1}, {"parent_id": "P2", "child_id": "C", "qty_per": 1})
    put(c, "component", "components.csv", {"component_id": "A"}, {"component_id": "B"}, {"component_id": "M"},
        {"component_id": "C", "description": "Sleeve blue (new mark)"})
    put(c, "bom_change", "bom_delta.csv", {"change_id": "ECN-1", "parent_id": "P2", "child_id": "C", "rev_new": "C",
                                           "use_up_old_rev": False, "effective_from": "2026-11-21", "reason": "new mark"})
    put(c, "stock", "stock.csv",
        {"snapshot_date": "2026-11-20", "material_id": "A", "batch": "100", "quantity": 1000},
        {"snapshot_date": "2026-11-20", "material_id": "B", "batch": "101", "quantity": 800},
        {"snapshot_date": "2026-11-20", "material_id": "B", "batch": "102", "quantity": 100},
        {"snapshot_date": "2026-11-20", "material_id": "C", "batch": "103", "quantity": 500},
        {"snapshot_date": "2026-11-20", "material_id": "C", "batch": "104", "quantity": 300},
        {"snapshot_date": "2026-11-13", "material_id": "A", "batch": "001", "quantity": 99999})
    put(c, "goods_receipt", "receipts.csv",
        {"gr_number": "100", "po_number": "9", "supplier_id": "S1", "material_id": "A", "qty_ordered": 1000, "qty_received": 1000, "delivery_note": "DN-0"},
        {"gr_number": "101", "po_number": "1", "supplier_id": "S1", "material_id": "B", "qty_ordered": 80, "qty_received": 800, "delivery_note": "DN-1", "remark": "over delivery accepted"},
        {"gr_number": "102", "po_number": "2", "supplier_id": "S1", "material_id": "B", "qty_ordered": 100, "qty_received": 100, "delivery_note": "DN-2"},
        {"gr_number": "103", "po_number": "3", "supplier_id": "S2", "material_id": "C", "qty_ordered": 500, "qty_received": 500, "delivery_note": "DN-3"},
        {"gr_number": "104", "po_number": "4", "supplier_id": "S1", "material_id": "C", "qty_ordered": 300, "qty_received": 300, "delivery_note": "DN-4"})
    put(c, "purchase_order_line", "pos.csv",
        {"po_number": "1", "material_id": "B", "quantity": 80, "status": "delivered"},
        {"po_number": "3", "material_id": "C", "quantity": 500, "status": "delivered"},
        {"po_number": "4", "material_id": "C", "quantity": 300, "status": "delivered"},
        {"po_number": "5", "material_id": "A", "quantity": 400, "status": "open"},
        {"po_number": "6", "material_id": "C", "quantity": 1000, "status": "open"})
    put(c, "doc_line", "receiving/scan_DN-1.jpg", {"document_no": "DN-1", "material_id": "B", "qty_printed": 800, "qty_handwritten": 80, "qty_final": 80})
    put(c, "doc_line", "receiving/scan_DN-4.jpg", {"document_no": "DN-4", "material_id": "C", "qty_final": 300})
    put(c, "doc_line", "suppliers/S2_x/oc_3.pdf", {"supplier_id": "S2", "po_numbers": ["3"], "material_id": "X-77",
                                                   "description": "Sleeve blue (new", "revision": "rev. B (sleeve_revB.pdf)", "qty_final": 500})
    put(c, "doc_line", "suppliers/S1_x/oc_4.pdf", {"supplier_id": "S1", "po_numbers": ["4"], "material_id": "C", "revision": "C", "qty_final": 300})
    put(c, "doc_line", "suppliers/S1_x/advice_AWB-1.pdf", {"supplier_id": "S1", "po_numbers": ["5"], "material_id": "A",
                                                           "qty_ordered": 400, "qty_final": 250, "remark": "Balance 150"})
    put(c, "doc_hold", "receiving/inspection_102.pdf", {"document_no": "102", "material_id": "B", "reason": "Lot failed AQL.",
                                                        "release_condition": "Concession signed by Quality.", "quote": "Lot does not meet AQL."})
    put(c, "route", "routes.csv", {"origin": "FRA", "destination": "DC", "transit_days": 1}, {"origin": "PORT", "destination": "DC", "transit_days": 1})
    put(c, "shipment", "shipments.csv",
        {"shipment_id": "S-1", "reference": "AWB-1", "supplier_id": "S1", "po_numbers": "5", "material_ids": "A", "destination": "FRA",
         "dest_site": "DC", "eta": "2026-11-21", "status": "booked", "updated_at": "2026-11-20T16:00:00+01:00"},
        {"shipment_id": "S-2", "reference": "CONT-2", "supplier_id": "S1", "po_numbers": "6", "material_ids": "C", "destination": "PORT",
         "dest_site": "DC", "eta": "2026-11-19", "status": "arrived_port", "updated_at": "2026-11-20T10:00:00+01:00", "note": "available for pick-up"})
    put(c, "doc_shipment", "tracker.xlsx", {"shipment_id": "S-2", "status": "in transit", "comment": "call terminal"})
    put(c, "packout", "packout.csv", {"date": "2026-11-19", "product_id": "P1", "units_packed": 500})
    put(c, "supplier_component", "suppliers/S1_x/components.csv", {"component_id": "M", "note": "fitted by supplier"})
    put(c, "doc_pack_status", "status_old.xlsx", {"as_of": "2026-11-20T18:00:00", "product_id": "P1", "still_to_pack": 999, "rate_per_hour": 10})
    put(c, "doc_pack_status", "status.xlsx",
        {"as_of": "2026-11-21T13:00:00", "product_id": "P1", "packed": 0, "still_to_pack": 200, "rate_per_hour": 10},
        {"as_of": "2026-11-21T13:00:00", "product_id": "P2", "packed": 0, "still_to_pack": 600, "rate_per_hour": 100})
    put(c, "doc_shift", "plan.xlsx", {"as_of": "2026-11-20T18:00:00", "day": "Saturday", "start": "06:00", "end": "22:00"},
        {"as_of": "2026-11-20T18:00:00", "day": "Sunday", "start": "06:00", "end": "20:00"},
        {"as_of": "2026-11-20T18:00:00", "day": "Saturday/Sunday", "start": None, "end": None})
    c.commit()
    return c


def by_kind(assessment):
    return {f.kind: f for f in assessment.findings}


def usable(assessment):
    return {b.batch: b.usable_qty for b in assessment.batches}


@pytest.mark.parametrize("text, rev", [("rev. B (GH6_sleeve_revB.pdf)", "B"), ("C", "C"), ("Revision c", "C"), ("b", "B"), ("", None), (None, None), ("blue", None)])
def test_revisions_are_read_from_however_suppliers_write_them(text, rev):
    assert norm_rev(text) == rev


def test_only_the_latest_stock_snapshot_counts(conn):
    assert "001" not in usable(assess(conn, TODAY))


def test_receipt_booked_above_the_delivery_note_is_cut_back_to_the_paper(conn):
    a = assess(conn, TODAY)
    f = by_kind(a)["receipt_mismatch"]
    assert (f.units, f.data["batch"], usable(a)["101"]) == (720, "101", 80)
    assert [e["path"] for e in f.evidence] == ["receiving/scan_DN-1.jpg", "receipts.csv"]
    assert len([x for x in a.findings if x.kind == "receipt_mismatch"]) == 1  # DN-4 agrees with its receipt


def test_lot_on_hold_is_not_usable_even_though_the_erp_shows_it_free(conn):
    a = assess(conn, TODAY)
    f = by_kind(a)["quality_hold"]
    assert (f.units, f.needs_action, usable(a)["102"]) == (100, True, 0)
    assert "Concession signed by Quality." in f.detail and ".." not in f.detail


def test_supplier_part_number_is_resolved_and_the_old_revision_blocked(conn):
    a = assess(conn, TODAY)
    assert Facts(conn).aliases == {"X-77": "C"}
    f = by_kind(a)["revision_blocked"]
    assert (f.material_id, f.units, f.data["revision"], f.data["required"]) == ("C", 500, "B", "C")
    assert usable(a)["103"] == 0 and usable(a)["104"] == 300
    assert {e["path"] for e in f.evidence} == {"bom_delta.csv", "suppliers/S2_x/oc_3.pdf"}


def test_revision_change_that_is_not_yet_effective_blocks_nothing(conn):
    assert usable(assess(conn, date(2026, 11, 20)))["103"] == 500


def test_short_shipment_counts_only_what_the_supplier_shipped(conn):
    a = assess(conn, TODAY)
    air = next(i for i in a.inbound if i.shipment_id == "S-1")
    assert (air.erp_qty, air.credible_qty, air.dock_date) == (400, 250, "2026-11-22")
    assert by_kind(a)["short_shipment"].units == 150


def test_goods_at_the_port_need_someone_to_act(conn):
    a = assess(conn, TODAY)
    sea = next(i for i in a.inbound if i.shipment_id == "S-2")
    assert sea.needs_action and sea.onward_days == 1
    assert by_kind(a)["awaiting_pickup"].needs_action
    assert "in transit" in by_kind(a)["tracker_out_of_date"].title


def test_part_never_bought_or_stocked_is_not_treated_as_a_shortage(conn):
    a = assess(conn, TODAY)
    assert a.untracked == {"M"}
    assert "fitted by supplier" in by_kind(a)["not_stocked_separately"].detail


def test_the_erp_view_says_fine_and_the_evidence_says_not(conn):
    a = assess(conn, TODAY)
    erp = simulate(conn, a, ["P1", "P2"], trust_erp=True)
    assert erp["will_make_it"] and (erp["as_of"], erp["deadline"], erp["hours_available"]) == ("2026-11-21T13:00", "2026-11-22T20:00", 23.0)

    real = {p["product_id"]: p for p in simulate(conn, a, ["P1", "P2"])["products"]}
    assert real["P1"]["short"] == 120 and real["P1"]["first_stop"] == {"at": "2026-11-21T21:00", "material_id": "B"}
    assert real["P2"]["short"] == 300 and real["P2"]["first_stop"]["material_id"] == "C"

    acted = {p["product_id"]: p for p in simulate(conn, a, ["P1", "P2"], with_actions=True)["products"]}
    assert acted["P2"]["short"] == 0 and acted["P1"]["short"] == 120


def test_shared_parts_are_kept_for_the_product_with_priority(conn):
    conn.execute("UPDATE records SET data = json_set(data, '$.quantity', 500) WHERE entity = 'stock' AND json_extract(data, '$.batch') = '100'")
    conn.execute("UPDATE records SET data = json_set(data, '$.quantity', 9000) WHERE entity = 'stock' AND json_extract(data, '$.batch') = '104'")
    conn.execute("DELETE FROM records WHERE entity IN ('doc_line', 'shipment')")
    a = assess(conn, TODAY)
    ranked = {p["product_id"]: p for p in simulate(conn, a, ["P1", "P2"], trust_erp=True)["products"]}
    assert (ranked["P1"]["short"], ranked["P2"]["short"]) == (0, 300)  # 500 A: 200 kept for P1, 300 left for P2
    unranked = {p["product_id"]: p for p in simulate(conn, a, [], trust_erp=True)["products"]}
    assert unranked["P1"]["short"] > 0  # without a priority, P2's faster lines eat the shared part


def commit(conn, message_id, material, qty, dock_at, firm, shipment_ref=None):
    conn.execute("INSERT INTO commitments (message_id, case_key, material_id, quantity, dock_at, firm, shipment_ref, created_at) "
                 "VALUES (?, 'case', ?, ?, ?, ?, ?, 't')", (message_id, material, qty, dock_at, int(firm), shipment_ref))


def short(conn, **kw):
    return {p["product_id"]: p["short"] for p in simulate(conn, assess(conn, TODAY), ["P1", "P2"], **kw)["products"]}


def test_firm_supplier_commitment_closes_the_gap_and_a_conditional_one_does_not(conn):
    commit(conn, 1, "B", 120, "2026-11-22T07:00:00", firm=False)
    assert short(conn)["P1"] == 120
    assert short(conn, with_actions=True)["P1"] == 0
    assert short(conn, trust_erp=True)["P1"] == 0  # the ERP view never sees mail
    commit(conn, 2, "B", 120, "2026-11-22T07:00:00", firm=True)  # the supplier's newer word replaces the older
    assert short(conn)["P1"] == 0
    row = next(c for c in simulate(conn, assess(conn, TODAY), ["P1", "P2"])["components"] if c["material_id"] == "B")
    assert (row["confirmed_by_supplier"], row["inbound_needing_action"]) == (120, 0)


def test_commitment_that_arrives_after_the_line_has_idled_too_long_saves_fewer_copies(conn):
    commit(conn, 1, "B", 120, "2026-11-22T19:00:00", firm=True)
    assert short(conn)["P1"] == 110  # one hour left at 10 an hour


def test_confirmed_existing_shipment_is_not_counted_twice(conn):
    commit(conn, 1, "A", 250, "2026-11-22T09:00:00", firm=True, shipment_ref="AWB-1")
    row = next(c for c in simulate(conn, assess(conn, TODAY), ["P1", "P2"])["components"] if c["material_id"] == "A")
    assert row["inbound_counted"] == 250


def test_a_case_is_reworked_when_its_facts_change_but_not_more_than_twice_an_hour(conn):
    from datetime import datetime, timedelta, timezone
    from app.pipeline import MAX_RUNS_PER_HOUR, due_cases

    now = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    due = due_cases(conn, now)
    assert "B" in due

    def worked(basis, minutes_ago):
        log = json.dumps([(now - timedelta(minutes=m)).isoformat(timespec="seconds") for m in minutes_ago])
        conn.execute("DELETE FROM cases")
        conn.execute("INSERT INTO cases (key, material_id, status, basis, runs_log, created_at, updated_at) "
                     "VALUES ('part:B', 'B', 'proposed', ?, ?, 't', 't')", (basis, log))

    worked(due["B"], [5])
    assert "B" not in due_cases(conn, now)                       # nothing changed
    worked("older facts", [50])
    assert "B" in due_cases(conn, now)                           # the facts changed
    worked("older facts", [50, 20, 10][:MAX_RUNS_PER_HOUR])
    assert "B" not in due_cases(conn, now)                       # they changed, but the hour's allowance is used up
    worked("older facts", [130, 90])
    assert "B" in due_cases(conn, now)


def test_solving_one_part_is_only_worth_what_the_other_parts_allow(conn):
    a = assess(conn, TODAY)
    short = lambda **kw: {p["product_id"]: p["short"] for p in simulate(conn, a, ["P1", "P2"], **kw)["products"]}
    assert short() == {"P1": 120, "P2": 300}
    assert short(covered=frozenset({"B"})) == {"P1": 0, "P2": 300}       # B only holds back P1
    assert short(covered=frozenset({"B", "C"})) == {"P1": 0, "P2": 0}


def test_a_newer_count_moves_the_forecast_forward_and_received_goods_are_not_counted_twice(conn):
    put(conn, "doc_line", "stock_update.xlsx",
        {"as_of": "2026-11-21T20:00:00", "material_id": "P1", "qty_final": 70},      # 70 packed since 13:00
        {"as_of": "2026-11-21T20:00:00", "material_id": "B", "qty_final": 830},      # 900 on the books minus 70 used
        {"as_of": "2026-11-21T20:00:00", "material_id": "A", "qty_final": 1180})     # includes 250 received today
    put(conn, "goods_receipt", "receipts_update.csv",
        {"gr_number": "200", "gr_date": "2026-11-21", "po_number": "5", "material_id": "A", "qty_ordered": 400, "qty_received": 250})
    conn.execute("UPDATE records SET data = json_set(data, '$.etd', '2026-11-20') WHERE entity = 'shipment'")
    a = assess(conn, TODAY)
    air = next(i for i in a.inbound if i.shipment_id == "S-1")
    assert (air.erp_qty, air.credible_qty) == (150, 0)             # shipped 250 of 400, all 250 are in
    f = simulate(conn, a, ["P1", "P2"])
    p1 = next(p for p in f["products"] if p["product_id"] == "P1")
    assert (f["as_of"], p1["packed"], p1["to_pack"]) == ("2026-11-21T20:00", 70, 130)
    b = next(c for c in f["components"] if c["material_id"] == "B")
    assert (b["erp_stock"], b["usable_stock"], b["gap"]) == (830, 10, 120)   # 720 overbooked and 100 on hold stay out
    assert next(c for c in f["components"] if c["material_id"] == "A")["inbound_counted"] == 0


def test_pieces_of_another_material_in_a_receipt_are_not_usable(conn):
    conn.execute("UPDATE records SET data = json_set(data, '$.remark', '1 pallet holds A (40 pcs); booked per delivery note') "
                 "WHERE entity = 'goods_receipt' AND json_extract(data, '$.gr_number') = '104'")
    a = assess(conn, TODAY)
    f = by_kind(a)["wrong_material"]
    assert (f.material_id, f.units, f.needs_action, f.data["really"], usable(a)["104"]) == ("C", 40, True, "A", 260)


def test_stock_received_after_a_conditional_release_ran_out_is_flagged(conn):
    put(conn, "supplier", "suppliers/S2_x/profile.csv",
        {"supplier_id": "S2", "name": "Second Source", "qualification_status": "conditional", "supplier_since": "2026-08-01"})
    put(conn, "doc_fact", "policy.pdf", {"doc_type": "policy", "statement": "Conditional releases are limited to 90 days from the date of release."})
    conn.execute("UPDATE records SET data = json_set(data, '$.gr_date', '2026-11-17') WHERE entity = 'goods_receipt' "
                 "AND json_extract(data, '$.gr_number') = '103'")
    f = by_kind(assess(conn, TODAY))["qualification_expired"]
    assert (f.material_id, f.units, f.data["expired"], f.needs_action) == ("C", 500, "2026-10-30", True)
    assert "qualification_expired" not in by_kind(assess(conn, date(2026, 10, 1)))


def test_forecast_says_what_is_missing_when_documents_are_missing(conn):
    conn.execute("DELETE FROM records WHERE entity = 'doc_shift'")
    assert simulate(conn, assess(conn, TODAY))["ready"] is False
