"""What the ERP says against what the paperwork says.

Every rule here is generic and works on canonical records; none knows a part,
a supplier or a number in advance. The output is a list of findings, each with
the files that prove it, plus the stock and inbound quantities that can really
be counted on.
"""

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from app.documents import parse_quantity

STALE_HOURS = 24
MIN_PREFIX = 8
_REV = re.compile(r"(?i)\brev(?:ision)?\.?\s*([A-Z0-9]{1,2})\b")


@dataclass
class Finding:
    key: str
    kind: str
    material_id: str | None
    title: str
    detail: str
    units: int = 0
    needs_action: bool = False
    evidence: list[dict] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class Batch:
    material_id: str
    batch: str
    erp_qty: float
    usable_qty: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Inbound:
    shipment_id: str
    material_id: str
    erp_qty: float
    credible_qty: float
    dock_date: str | None
    status: str
    needs_action: bool = False
    onward_days: int = 0
    reference: str | None = None
    reasons: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    findings: list[Finding]
    batches: list[Batch]
    inbound: list[Inbound]
    untracked: set[str]

    def to_dict(self) -> dict:
        return {"findings": [asdict(f) for f in self.findings], "batches": [asdict(b) for b in self.batches],
                "inbound": [asdict(i) for i in self.inbound], "untracked": sorted(self.untracked)}


def load(conn: sqlite3.Connection, entity: str) -> list[dict]:
    rows = conn.execute("SELECT source_path, data FROM records WHERE entity = ? ORDER BY id", (entity,))
    return [{**json.loads(r["data"]), "_source": r["source_path"]} for r in rows]


def norm_rev(text) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    m = _REV.search(text)
    if m:
        return m.group(1).upper()
    return text.strip().upper() if len(text.strip()) <= 2 else None


def split_ids(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in re.split(r"[|,;\s]+", str(value or "")) if p.strip()]


def _num(value) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _prefix(a: str, b: str) -> int:
    a, b = a.casefold(), b.casefold()
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


class Facts:
    """Canonical records, loaded once and cross-referenced."""

    def __init__(self, conn: sqlite3.Connection):
        self.components = {c["component_id"]: c for c in load(conn, "component")}
        for change in load(conn, "component_change"):
            if change["component_id"] in self.components and change.get("field"):
                self.components[change["component_id"]][change["field"]] = change.get("new")
        self.products = {p["product_id"]: p for p in load(conn, "product")}
        self.bom = load(conn, "bom")
        self.bom_changes = load(conn, "bom_change")
        self.po_lines = load(conn, "purchase_order_line")
        self.receipts = load(conn, "goods_receipt")
        self.shipments = load(conn, "shipment")
        self.tracker = load(conn, "doc_shipment")
        self.routes = load(conn, "route")
        self.packout = load(conn, "packout")
        self.holds = load(conn, "doc_hold")
        self.supplier_components = load(conn, "supplier_component")
        stock = load(conn, "stock")
        latest = max((s.get("snapshot_date") or "" for s in stock), default="")
        self.stock = [s for s in stock if (s.get("snapshot_date") or "") == latest]
        self.snapshot_date = latest or None
        self.po_by_number = defaultdict(list)
        for line in self.po_lines:
            self.po_by_number[str(line["po_number"])].append(line)
        self.receipt_by_number = {str(g["gr_number"]): g for g in self.receipts}
        self.doc_lines = self._resolve(load(conn, "doc_line"))

    def _resolve(self, lines: list[dict]) -> list[dict]:
        """Suppliers use their own part numbers. Work out which component each one is from the
        orders the document refers to and from the description, then reuse what was learned."""
        alias: dict[str, str] = {}
        for line in lines:
            raw = line.get("material_id")
            if not raw or raw in self.components:
                continue
            on_orders = {p["material_id"] for po in split_ids(line.get("po_numbers")) for p in self.po_by_number.get(po, [])}
            candidates = [c for c in on_orders if c in self.components]
            description = line.get("description") or ""
            if description and candidates:
                best = max(candidates, key=lambda c: _prefix(description, self.components[c].get("description") or ""))
                if _prefix(description, self.components[best].get("description") or "") >= MIN_PREFIX:
                    alias.setdefault(raw, best)
            elif len(candidates) == 1:
                alias.setdefault(raw, candidates[0])
        for line in lines:
            raw = line.get("material_id")
            if raw in alias:
                line["supplier_part"], line["material_id"] = raw, alias[raw]
        self.aliases = alias
        return lines

    def revision_of(self, material_id: str, po_number: str, supplier_id: str | None) -> tuple[str | None, str, list[dict]]:
        """Which revision the paperwork states for this material on this order."""
        stated = [l for l in self.doc_lines if l.get("material_id") == material_id and norm_rev(l.get("revision"))]
        for basis, lines in (
            ("stated for this order", [l for l in stated if str(po_number) in split_ids(l.get("po_numbers"))]),
            ("stated in the supplier's other paperwork", [l for l in stated if supplier_id and l.get("supplier_id") == supplier_id]),
        ):
            revs = {norm_rev(l["revision"]) for l in lines}
            if len(revs) == 1:
                return revs.pop(), basis, lines
            if len(revs) > 1:
                return None, "the paperwork names more than one revision", lines
        return None, "no paperwork states a revision", []


def _evidence(path: str, note: str) -> dict:
    return {"path": path, "note": note}


def assess(conn: sqlite3.Connection, today: date | None = None) -> Assessment:
    f = Facts(conn)
    findings: list[Finding] = []
    batches = {str(s.get("batch") or f"{s['material_id']}/none"): Batch(
        s["material_id"], str(s.get("batch") or ""), _num(s.get("quantity")), _num(s.get("quantity"))) for s in f.stock}

    snapshot = _day(f.snapshot_date)
    for g in f.receipts:  # received after the snapshot: on site, although the snapshot does not show it yet
        if snapshot and (_day(g.get("gr_date")) or date.min) > snapshot and str(g["gr_number"]) not in batches:
            batches[str(g["gr_number"])] = Batch(g["material_id"], str(g["gr_number"]), _num(g.get("qty_received")),
                                                 _num(g.get("qty_received")))

    # 1. Receipts booked with a different quantity than the delivery note states.
    booked, paper, scans = defaultdict(list), defaultdict(float), defaultdict(set)
    for g in f.receipts:
        if g.get("delivery_note"):
            booked[(g["delivery_note"], g["material_id"])].append(g)
    notes = {dn for dn, _ in booked}
    for line in f.doc_lines:
        key = (line.get("document_no"), line.get("material_id"))
        if key[0] in notes and line.get("qty_final") is not None and line["_source"] not in scans[key]:
            scans[key].add(line["_source"])
        if key[0] in notes and line.get("qty_final") is not None:
            paper[key] += _num(line["qty_final"])
    for key, receipts in booked.items():
        if key not in paper:
            continue
        total = sum(_num(g.get("qty_received")) for g in receipts)
        delta = total - paper[key]
        if not delta:
            continue
        culprit = max(receipts, key=lambda g: abs(_num(g.get("qty_received")) - _num(g.get("qty_ordered"))))
        batch = batches.get(str(culprit["gr_number"]))
        if batch and delta > 0:
            batch.usable_qty = max(0, batch.usable_qty - delta)
            batch.reasons.append(f"booked {total:,.0f}, delivery note says {paper[key]:,.0f}")
        findings.append(Finding(
            key=f"receipt_mismatch:{culprit['gr_number']}", kind="receipt_mismatch", material_id=key[1],
            title=f"Goods receipt {culprit['gr_number']} books {total:,.0f}, the delivery note says {paper[key]:,.0f}",
            detail=f"Delivery note {key[0]} states {paper[key]:,.0f} pieces; the order line was for "
                   f"{_num(culprit.get('qty_ordered')):,.0f}. Receiving booked {total:,.0f}"
                   + (f" with the remark '{culprit['remark']}'." if culprit.get("remark") else "."),
            units=int(delta if batch else 0),
            evidence=[_evidence(p, "delivery note") for p in sorted(scans[key])] + [_evidence(culprit["_source"], f"goods receipt {culprit['gr_number']}")],
            data={"batch": str(culprit["gr_number"]), "booked": total, "paper": paper[key], "ordered": _num(culprit.get("qty_ordered"))},
        ))

    # 1b. Receipts whose remark says part of the delivery is a different material.
    for g in f.receipts:
        remark, batch = str(g.get("remark") or ""), batches.get(str(g["gr_number"]))
        others = [cid for cid in f.components if cid != g["material_id"] and cid in remark]
        if not others or not batch:
            continue
        stated = re.search("([0-9][0-9., ]*) *(pcs|pieces|pc|szt|stk)", remark, re.IGNORECASE)
        wrong = parse_quantity(stated.group(1).strip()) if stated else None
        if wrong:
            batch.usable_qty = max(0, batch.usable_qty - wrong)
            batch.reasons.append(f"{wrong:,.0f} pieces are {others[0]}, not {g['material_id']}")
        findings.append(Finding(
            key=f"wrong_material:{g['gr_number']}", kind="wrong_material", material_id=g["material_id"],
            title=(f"{wrong:,.0f} pieces booked as {g['material_id']} are really {others[0]}" if wrong
                   else f"Part of goods receipt {g['gr_number']} booked as {g['material_id']} is really {others[0]}"),
            detail=f"Receiving noted on goods receipt {g['gr_number']}: '{remark}'. The wrong pieces have to be set aside "
                   "so they are not packed, and the supplier owes a correction.",
            units=int(wrong or 0), needs_action=True, evidence=[_evidence(g["_source"], f"goods receipt {g['gr_number']}")],
            data={"batch": str(g["gr_number"]), "really": others[0]},
        ))

    # 2. Lots a document puts on hold while the ERP shows them as free stock.
    for hold in f.holds:
        ids = [str(v) for v in (hold.get("gr_number"), hold.get("lot"), hold.get("document_no")) if v]
        batch = next((batches[i] for i in ids if i in batches and batches[i].material_id == hold.get("material_id")), None)
        if not batch or not batch.usable_qty:
            continue
        blocked, batch.usable_qty = batch.usable_qty, 0
        batch.reasons.append(f"on hold: {hold.get('reason')}")
        findings.append(Finding(
            key=f"quality_hold:{batch.batch}", kind="quality_hold", material_id=batch.material_id,
            title=f"{blocked:,.0f} pieces of {batch.material_id} are booked as free stock but are on hold",
            detail=f"{hold.get('reason')} Release condition: {(hold.get('release_condition') or 'not stated').rstrip('.')}.",
            units=int(blocked), needs_action=True, evidence=[_evidence(hold["_source"], hold.get("quote") or "hold")],
            data={"batch": batch.batch, "release_condition": hold.get("release_condition")},
        ))

    # 3. Stock and inbound goods made to a revision that may no longer be packed.
    today = today or date.today()
    required = {}
    for change in f.bom_changes:
        effective = _day(change.get("effective_from"))
        if change.get("rev_new") and change.get("use_up_old_rev") is False and (not effective or effective <= today):
            required[change["child_id"]] = (norm_rev(str(change["rev_new"])) or str(change["rev_new"]), change)
    for batch in batches.values():
        if batch.material_id not in required or not batch.usable_qty:
            continue
        rev_needed, change = required[batch.material_id]
        receipt = f.receipt_by_number.get(batch.batch, {})
        rev, basis, lines = f.revision_of(batch.material_id, receipt.get("po_number", ""), receipt.get("supplier_id"))
        proof = [_evidence(l["_source"], l.get("remark") or f"revision {l.get('revision')}") for l in lines[:3]]
        rule = _evidence(change["_source"], change.get("reason") or f"revision {rev_needed} required")
        if rev and rev != rev_needed:
            blocked, batch.usable_qty = batch.usable_qty, 0
            batch.reasons.append(f"revision {rev}, only {rev_needed} may be packed")
            findings.append(Finding(
                key=f"revision_blocked:{batch.batch}", kind="revision_blocked", material_id=batch.material_id,
                title=f"{blocked:,.0f} pieces of {batch.material_id} in stock are revision {rev}; only {rev_needed} may be packed",
                detail=f"Batch {batch.batch} from {receipt.get('supplier_id')} is revision {rev} ({basis}). "
                       f"Change {change.get('change_id') or ''}: {change.get('reason') or ''}".strip(),
                units=int(blocked), evidence=[rule, *proof], data={"batch": batch.batch, "revision": rev, "required": rev_needed},
            ))
        elif not rev:
            findings.append(Finding(
                key=f"revision_unverified:{batch.batch}", kind="revision_unverified", material_id=batch.material_id,
                title=f"Revision of {batch.usable_qty:,.0f} pieces of {batch.material_id} is not documented",
                detail=f"Only revision {rev_needed} may be packed; for batch {batch.batch} {basis}.",
                needs_action=True, evidence=[rule, *proof], data={"batch": batch.batch, "required": rev_needed},
            ))

    # 3b. Stock received from a supplier after its conditional release ran out.
    doc_facts = load(conn, "doc_fact")
    limit = next((int(m.group(1)) for x in doc_facts if x.get("doc_type") == "policy"
                  and "conditional" in str(x.get("statement")).lower()
                  and (m := re.search("([0-9]+) days", str(x.get("statement"))))), None)
    for sup in load(conn, "supplier"):
        if str(sup.get("qualification_status") or "").lower() != "conditional":
            continue
        expiry = basis = None
        for x in doc_facts:  # the supplier's own file may state the date outright
            said = re.search("until ([0-9]{4}-[0-9]{2}-[0-9]{2})", str(x.get("statement")))
            if x.get("supplier_id") == sup["supplier_id"] and said and "qualif" in str(x.get("statement")).lower():
                expiry, basis, source = _day(said.group(1)), "stated in the supplier's file", x["_source"]
        if not expiry and limit and _day(sup.get("supplier_since")):
            expiry, source = _day(sup["supplier_since"]) + timedelta(days=limit), sup["_source"]
            basis = f"{limit} days after the release on {sup['supplier_since']}, as the approval policy allows"
        if not expiry or expiry >= today:
            continue
        late = defaultdict(float)
        for b in batches.values():
            g = f.receipt_by_number.get(b.batch, {})
            if g.get("supplier_id") == sup["supplier_id"] and (_day(g.get("gr_date")) or date.min) > expiry:
                late[b.material_id] += b.erp_qty
        for material, qty in late.items():
            findings.append(Finding(
                key=f"qualification_expired:{sup['supplier_id']}:{material}", kind="qualification_expired", material_id=material,
                title=f"{qty:,.0f} pieces of {material} came from {sup.get('name')} after its conditional release expired on {expiry.isoformat()}",
                detail=f"The release ended on {expiry.isoformat()} ({basis}). Goods received after that date need the release "
                       "extended, or an exception, before they are packed.",
                units=int(qty), needs_action=True, evidence=[_evidence(source, "supplier qualification")],
                data={"supplier_id": sup["supplier_id"], "expired": expiry.isoformat()},
            ))

    # 4. Inbound shipments: what will really be on the dock, and when.
    onward = {(r.get("origin"), r.get("destination")): _num(r.get("transit_days")) for r in f.routes}
    updates = [s["updated_at"] for s in f.shipments if s.get("updated_at")]
    newest = max((datetime.fromisoformat(u) for u in updates), default=None)
    tracker = {t.get("shipment_id"): t for t in f.tracker if t.get("shipment_id")}
    inbound: list[Inbound] = []
    arrived: list[tuple[Inbound, float]] = []
    for s in f.shipments:
        eta, status = _day(s.get("eta")), str(s.get("status") or "").lower()
        site = s.get("dest_site") or s.get("destination")
        last_leg = int(onward.get((s.get("destination"), site), 0))
        dock = eta + timedelta(days=last_leg) if eta else None
        pos = split_ids(s.get("po_numbers"))
        for material in split_ids(s.get("material_ids")):
            open_lines = [p for po in pos for p in f.po_by_number.get(po, []) if p["material_id"] == material
                          and str(p.get("status") or "").lower() not in ("delivered", "cancelled")]
            qty = sum(_num(p.get("quantity")) for p in open_lines)
            # What has been booked in from this shipment since it left is no longer on its way.
            left = _day(s.get("etd"))
            received = sum(_num(g.get("qty_received")) for g in f.receipts if g["material_id"] == material
                           and str(g.get("po_number")) in pos and left and (_day(g.get("gr_date")) or date.min) >= left)
            item = Inbound(s["shipment_id"], material, max(0, qty - received), qty, dock.isoformat() if dock else None, status,
                           onward_days=last_leg, reference=s.get("reference"))
            inbound.append(item)
            arrived.append((item, received))

            short = [l for l in f.doc_lines if l.get("material_id") == material and l.get("qty_ordered") is not None
                     and l.get("qty_final") is not None and _num(l["qty_final"]) < _num(l["qty_ordered"])
                     and set(split_ids(l.get("po_numbers"))) & set(pos)
                     and (s.get("reference") or "\0") in json.dumps(l, ensure_ascii=False) + l["_source"]]
            if short:
                advice = short[-1]
                item.credible_qty = min(qty, _num(advice["qty_final"]))
                item.reasons.append(f"supplier shipped {item.credible_qty:,.0f} of {_num(advice['qty_ordered']):,.0f}")
                findings.append(Finding(
                    key=f"short_shipment:{s['shipment_id']}:{material}", kind="short_shipment", material_id=material,
                    title=f"Only {item.credible_qty:,.0f} of {qty:,.0f} pieces of {material} are on shipment {s['shipment_id']}",
                    detail=f"The supplier's advice for {s.get('reference')} states {_num(advice['qty_final']):,.0f} shipped "
                           f"against {_num(advice['qty_ordered']):,.0f} ordered. {advice.get('remark') or ''}".strip(),
                    units=int(qty - item.credible_qty),
                    evidence=[_evidence(advice["_source"], "shipping advice"), _evidence(s["_source"], f"shipment {s['shipment_id']}")],
                    data={"shipment_id": s["shipment_id"], "reference": s.get("reference")},
                ))

            if material in required and pos:
                rev_needed, change = required[material]
                rev, basis, lines = f.revision_of(material, pos[0], s.get("supplier_id"))
                if rev and rev != rev_needed:
                    item.credible_qty = 0
                    item.reasons.append(f"revision {rev}, only {rev_needed} may be packed")

            if status == "arrived_port" and site != s.get("destination"):
                item.needs_action = True
                item.reasons.append("at the port, not collected")
                findings.append(Finding(
                    key=f"awaiting_pickup:{s['shipment_id']}:{material}", kind="awaiting_pickup", material_id=material,
                    title=f"{qty:,.0f} pieces of {material} are at {s.get('destination')} and have not been collected",
                    detail=f"Shipment {s['shipment_id']} ({s.get('reference')}) arrived on {s.get('eta')}. "
                           f"ERP note: {s.get('note') or 'none'}. Nothing reaches {site} until a truck and a dock slot are booked.",
                    units=int(qty), needs_action=True, evidence=[_evidence(s["_source"], f"shipment {s['shipment_id']}")],
                    data={"shipment_id": s["shipment_id"], "reference": s.get("reference")},
                ))

        seen = tracker.get(s["shipment_id"])
        if seen and str(seen.get("status") or "").lower().replace(" ", "_") != status:
            findings.append(Finding(
                key=f"tracker_out_of_date:{s['shipment_id']}", kind="tracker_out_of_date", material_id=None,
                title=f"The tracker spreadsheet shows {s['shipment_id']} as '{seen.get('status')}', the ERP as '{status}'",
                detail=f"Tracker comment: {seen.get('comment') or 'none'}.",
                evidence=[_evidence(seen["_source"], "tracker row"), _evidence(s["_source"], "ERP row")],
                data={"shipment_id": s["shipment_id"]},
            ))
        if newest and s.get("updated_at") and status not in ("booked",) and \
                newest - datetime.fromisoformat(s["updated_at"]) > timedelta(hours=STALE_HOURS):
            findings.append(Finding(
                key=f"no_news:{s['shipment_id']}", kind="no_news", material_id=None,
                title=f"No status update on {s['shipment_id']} since {s['updated_at'][:16].replace('T', ' ')}",
                detail=f"Other shipments in the same export were updated up to {newest.isoformat()[:16].replace('T', ' ')}.",
                needs_action=True, evidence=[_evidence(s["_source"], f"shipment {s['shipment_id']}")],
                data={"shipment_id": s["shipment_id"]},
            ))

    for item, received in arrived:
        if received:
            item.credible_qty = max(0, item.credible_qty - received)
            item.reasons.append(f"{received:,.0f} already received")

    # 5. Parts on the bill of materials that were never bought, received or stocked on their own.
    packed = {p["product_id"] for p in f.packout if _num(p.get("units_packed")) > 0}
    handled = {p["material_id"] for p in f.po_lines} | {g["material_id"] for g in f.receipts}
    in_stock = {b.material_id for b in batches.values() if b.erp_qty > 0}
    untracked = set()
    for line in f.bom:
        child = line["child_id"]
        if line["parent_id"] in packed and child not in handled and child not in in_stock and child not in untracked:
            untracked.add(child)
            note = next((sc for sc in f.supplier_components if sc.get("component_id") == child and sc.get("note")), None)
            findings.append(Finding(
                key=f"not_stocked_separately:{child}", kind="not_stocked_separately", material_id=child,
                title=f"{child} shows no stock, but it has never been bought or received on its own",
                detail=f"{line['parent_id']} has been packed for weeks without a single order, receipt or stock entry for "
                       f"this part, so it cannot be what stops the line."
                       + (f" Supplier note: {note['note']}." if note else ""),
                evidence=[_evidence(line["_source"], "bill of materials")] + ([_evidence(note["_source"], note["note"])] if note else []),
            ))

    return Assessment(findings, list(batches.values()), inbound, untracked)
