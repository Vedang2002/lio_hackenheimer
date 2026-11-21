"""Will every copy be packed before the trucks leave, and which part stops a line first?

Plain arithmetic over the assessment: no model is involved, so the same inputs
always give the same answer and every number can be explained.
"""

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.truth import Assessment, Facts, _day, _num, load

ARRIVAL_HOUR = 12  # assumed time of day a shipment reaches the dock when nobody has confirmed one
STEP = timedelta(hours=1)


def _moment(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    except ValueError:
        return None


def _clock(value) -> time | None:
    try:
        return time.fromisoformat(str(value).strip()[:5])
    except ValueError:
        return None


def pack_status(conn: sqlite3.Connection) -> tuple[datetime | None, dict[str, dict]]:
    """The newest pack-out figures any document states, per product."""
    latest: dict[str, dict] = {}
    for row in load(conn, "doc_pack_status"):
        at = _moment(row.get("as_of"))
        if at and row.get("product_id") and row.get("still_to_pack") is not None and row.get("rate_per_hour"):
            if row["product_id"] not in latest or at > latest[row["product_id"]]["_at"]:
                latest[row["product_id"]] = {**row, "_at": at}
    return max((r["_at"] for r in latest.values()), default=None), latest


def newer_count(conn: sqlite3.Connection, after: datetime, products: set[str]) -> tuple[datetime, dict[str, float]] | None:
    """A later document may state what is packed and what is on the books without repeating rates and demand.
    Returns the newest such count: quantities per product and per part."""
    by_source: dict[str, tuple[datetime, dict[str, float]]] = {}
    for line in load(conn, "doc_line"):
        at = _moment(line.get("as_of"))
        if at and at > after and line.get("material_id") and line.get("qty_final") is not None:
            by_source.setdefault(line["_source"], (at, {}))[1][line["material_id"]] = _num(line["qty_final"])
    counts = [c for c in by_source.values() if products & set(c[1])]
    return max(counts, key=lambda c: c[0]) if counts else None


def shift_windows(conn: sqlite3.Connection, launch: date) -> list[tuple[datetime, datetime]]:
    """Working windows of the packing lines from the newest document that states them."""
    rows = [r for r in load(conn, "doc_shift") if _clock(r.get("start")) and _clock(r.get("end"))]
    if not rows:
        return []
    newest = max(str(r.get("as_of") or r.get("doc_date") or "") for r in rows)
    windows = []
    for r in rows:
        if str(r.get("as_of") or r.get("doc_date") or "") != newest:
            continue
        day = _day(r.get("day"))
        if not day:  # a weekday name: the one in the week that ends on launch day
            name = str(r.get("day")).strip().casefold()
            day = next((launch - timedelta(days=n) for n in range(7)
                        if (launch - timedelta(days=n)).strftime("%A").casefold() == name), None)
        if day:
            windows.append((datetime.combine(day, _clock(r["start"])), datetime.combine(day, _clock(r["end"]))))
    return sorted(windows)


def simulate(conn: sqlite3.Connection, assessment: Assessment, priority: list[str] | None = None,
             trust_erp: bool = False, with_actions: bool = False, covered: frozenset[str] = frozenset()) -> dict:
    """Pack hour by hour until the deadline.

    trust_erp     ignore the findings and count stock and inbound as the ERP shows them
    with_actions  also count inbound goods that only arrive if somebody acts (e.g. a container to collect)
    covered       parts assumed to be solved, to see what solving them is worth
    """
    facts = Facts(conn)
    start, status = pack_status(conn)
    if not start:
        return {"ready": False, "reason": "No document states how many copies are still to pack."}
    book: dict[str, float] = {}
    count = newer_count(conn, start, set(status))
    if count:
        start, book = count
        for p in status:
            if p in book:
                packed_more = book[p] - _num(status[p].get("packed"))
                status[p] = {**status[p], "packed": book[p], "still_to_pack": max(0, _num(status[p]["still_to_pack"]) - packed_more)}
    launch = max((_day(facts.products.get(p, {}).get("launch_date")) or start.date() for p in status), default=start.date())
    windows = shift_windows(conn, launch)
    if not windows:
        return {"ready": False, "reason": "No document states the working hours of the packing lines."}
    deadline = max(end for _, end in windows)

    ranked = [p for p in (priority or []) if p in status]
    order = ranked + [p for p in status if p not in ranked]
    remaining = {p: _num(status[p]["still_to_pack"]) for p in order}
    rate = {p: _num(status[p]["rate_per_hour"]) for p in order}
    bom = defaultdict(dict)
    for line in facts.bom:
        valid = (_day(line.get("valid_from")) or date.min) <= launch <= (_day(line.get("valid_to")) or date.max)
        if line["parent_id"] in remaining and valid and line["child_id"] not in assessment.untracked | covered:
            bom[line["parent_id"]][line["child_id"]] = _num(line.get("qty_per")) or 1
    parts = {c for children in bom.values() for c in children}

    # Stock: the snapshot, minus what has been packed since it was taken.
    finished = {s["material_id"]: _num(s.get("quantity")) for s in facts.stock}
    used = defaultdict(float)
    for p in order:
        packed_since = max(0, _num(status[p].get("packed")) - finished.get(p, _num(status[p].get("packed"))))
        for child, per in bom[p].items():
            used[child] += packed_since * per
    erp, usable = defaultdict(float), defaultdict(float)
    for b in assessment.batches:
        erp[b.material_id] += b.erp_qty
        usable[b.material_id] += b.erp_qty if trust_erp else b.usable_qty
    on_books = {c: max(0, erp[c] - used[c]) for c in parts}
    available = {c: max(0, usable[c] - used[c]) for c in parts}
    if book:
        # A count newer than the snapshot is the starting point. What the findings say cannot be used is still unusable.
        on_books = {c: book.get(c, on_books[c]) for c in parts}
        available = {c: max(0, on_books[c] - (0 if trust_erp else erp[c] - usable[c])) for c in parts}
    at_start = dict(available)

    # Inbound: when each shipment can be counted on.
    arrivals, counted, at_risk, confirmed = [], defaultdict(float), defaultdict(float), defaultdict(float)
    # What suppliers have committed to by mail: the newest word per case and part replaces what the ERP expects.
    promised = [] if trust_erp else conn.execute(
        """SELECT * FROM commitments c WHERE message_id = (
               SELECT MAX(message_id) FROM commitments WHERE case_key = c.case_key AND material_id = c.material_id)""").fetchall()
    superseded = {(c["material_id"], c["shipment_ref"]) for c in promised if c["shipment_ref"]}
    for c in promised:
        when = _moment(c["dock_at"])
        if c["material_id"] not in parts or not when:
            continue
        if not c["firm"] and not with_actions:
            at_risk[c["material_id"]] += c["quantity"]
        elif when < deadline:
            arrivals.append((max(start, when), c["material_id"], c["quantity"], f"commitment {c['id']}"))
            counted[c["material_id"]] += c["quantity"]
            confirmed[c["material_id"]] += c["quantity"]
    for i in assessment.inbound:
        qty = i.erp_qty if trust_erp else i.credible_qty
        dock = _day(i.dock_date)
        if i.material_id not in parts or not qty or not dock:
            continue
        if {(i.material_id, i.shipment_id), (i.material_id, i.reference)} & superseded:
            continue
        when = max(start, datetime.combine(dock, time(ARRIVAL_HOUR)))
        if i.needs_action and not trust_erp:
            if not with_actions:
                at_risk[i.material_id] += qty
                continue
            when = max(when, datetime.combine(start.date() + timedelta(days=i.onward_days), time(ARRIVAL_HOUR)))
        if when < deadline:
            arrivals.append((when, i.material_id, qty, i.shipment_id))
            counted[i.material_id] += qty
    arrivals.sort()

    need = defaultdict(float)
    for p in order:
        for child, per in bom[p].items():
            need[child] += remaining[p] * per
    to_pack, stops, finish, hours = dict(remaining), {}, {}, 0.0
    timeline = [{"at": start.isoformat(timespec="minutes"), **{p: 0 for p in order}}]
    for begin, end in windows:
        slot = max(begin, start)
        if slot < end and timeline[-1]["at"] != slot.isoformat(timespec="minutes"):
            timeline.append({**timeline[-1], "at": slot.isoformat(timespec="minutes")})  # nothing is packed overnight
        while slot < end:
            length = min(STEP, end - slot)
            share = length / STEP
            hours += share
            while arrivals and arrivals[0][0] <= slot:
                _, material, qty, _ = arrivals.pop(0)
                available[material] += qty
            for n, p in enumerate(order):
                want = min(rate[p] * share, remaining[p])
                if want <= 0:
                    continue
                # A product further up the priority list keeps first call on every shared part.
                ahead = [q for q in order[:n] if q in ranked]
                free = {c: max(0, available[c] - sum(remaining[q] * bom[q].get(c, 0) for q in ahead)) for c in bom[p]}
                limiting = min(bom[p], key=lambda c: free[c] / bom[p][c], default=None)
                possible = free[limiting] // bom[p][limiting] if limiting else want
                done = min(want, possible)
                if done < want and p not in stops:
                    stops[p] = {"at": (slot + length * (done / (rate[p] * share))).isoformat(timespec="minutes"),
                                "material_id": limiting}
                for child, per in bom[p].items():
                    available[child] -= done * per
                remaining[p] -= done
                if remaining[p] <= 0 and p not in finish:
                    finish[p] = (slot + length * (done / (rate[p] * share))).isoformat(timespec="minutes")
            slot += length
            timeline.append({"at": slot.isoformat(timespec="minutes"), **{p: to_pack[p] - remaining[p] for p in order}})

    products = [{
        "product_id": p, "name": facts.products.get(p, {}).get("name"),
        "day_one_demand": _num(status[p].get("day_one_demand")), "packed": _num(status[p].get("packed")),
        "to_pack": to_pack[p], "packed_by_deadline": to_pack[p] - remaining[p], "short": remaining[p],
        "rate_per_hour": rate[p], "hours_needed": round(to_pack[p] / rate[p], 1) if rate[p] else None,
        "first_stop": stops.get(p), "finished_at": finish.get(p),
    } for p in order]
    components = [{
        "material_id": c, "description": facts.components.get(c, {}).get("description"),
        "need": need[c], "erp_stock": on_books[c], "usable_stock": at_start[c],
        "inbound_counted": counted[c], "inbound_needing_action": at_risk[c], "confirmed_by_supplier": confirmed[c],
        "gap": max(0, need[c] - at_start[c] - counted[c]),
        "used_in": [p for p in order if c in bom[p]],
    } for c in sorted(parts)]
    return {
        "ready": True, "as_of": start.isoformat(timespec="minutes"), "deadline": deadline.isoformat(timespec="minutes"),
        "hours_available": round(hours, 1), "priority": ranked, "products": products, "components": components,
        "will_make_it": all(p["short"] <= 0 for p in products), "timeline": timeline,
        "assumptions": [f"Unconfirmed deliveries are counted from {ARRIVAL_HOUR}:00 on their dock date.",
                        "Usable stock is consumed before stock that is blocked.",
                        "Parts shared between editions are reserved for the edition with priority."],
    }
