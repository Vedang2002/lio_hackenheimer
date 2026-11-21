"""The canonical vocabulary every tabular file is mapped onto.

Soony's exports rename columns, switch delimiters and change number and date
formats between files and over time. Downstream code only ever sees these names.
Fields marked with * identify a row; a mapping without them is rejected.
"""

ENTITIES: dict[str, dict] = {
    "product": {
        "about": "A finished product (an edition of a game) packed at the distribution center.",
        "fields": {"product_id*": "e.g. FG-...", "name": "", "lifecycle": "", "launch_date": "date",
                   "day_one_qty": "copies needed on launch day", "weekly_plan_qty": "planned units per week",
                   "std_cost": "standard cost per copy", "valid_from": "date"},
    },
    "bom": {
        "about": "Bill of materials: which component goes into which product, and how many per copy.",
        "fields": {"parent_id*": "product", "child_id*": "component", "qty_per*": "quantity per copy",
                   "valid_from": "date", "valid_to": "date"},
    },
    "bom_change": {
        "about": "An engineering change to a bill of materials line, e.g. a new required revision of a part.",
        "fields": {"change_id": "", "parent_id*": "product", "child_id*": "component", "action": "",
                   "qty_per_old": "", "qty_per_new": "", "rev_old": "", "rev_new": "",
                   "use_up_old_rev": "bool: may stock of the old revision still be packed",
                   "effective_from": "date", "reason": ""},
    },
    "component": {
        "about": "Master data of a purchased part.",
        "fields": {"component_id*": "e.g. CMP-...", "description": "", "category": "", "uom": "", "abc_class": "",
                   "critical": "bool", "drawing_rev": "released drawing or artwork revision"},
    },
    "component_change": {
        "about": "A change log of component master data: one changed field per row.",
        "fields": {"component_id*": "", "field*": "name of the changed attribute", "old": "", "new*": "",
                   "changed_by": ""},
    },
    "supplier": {
        "about": "A supplier's profile.",
        "fields": {"supplier_id*": "e.g. SUP-0010", "name": "", "legal_name": "", "category": "", "country": "",
                   "city": "", "currency": "", "payment_terms": "", "incoterm": "", "supplier_since": "date",
                   "status": "", "qualification_status": "", "contact_name": "", "contact_email": "", "phone": "",
                   "preferred_language": "", "notes": ""},
    },
    "supplier_component": {
        "about": "What one supplier can deliver: a component with price, lead time and share of volume.",
        "fields": {"supplier_id": "only if the file has such a column", "component_id*": "", "description": "",
                   "share": "share of volume, 0..1", "unit_price": "", "currency": "", "lead_time_days": "",
                   "moq": "minimum order quantity", "primary": "bool", "note": ""},
    },
    "purchase_order_line": {
        "about": "One line of a purchase order from the ERP.",
        "fields": {"po_number*": "", "po_line": "", "supplier_id": "", "material_id*": "", "plant": "",
                   "quantity*": "", "unit_price": "", "currency": "", "order_date": "date",
                   "requested_date": "date", "promised_date": "date confirmed by the supplier",
                   "actual_delivery_date": "date", "status": "", "buyer": "", "incoterm": "", "note": ""},
    },
    "goods_receipt": {
        "about": "A goods receipt booked at the receiving dock.",
        "fields": {"gr_number*": "", "gr_date": "date", "po_number": "", "po_line": "", "supplier_id": "",
                   "material_id*": "", "plant": "", "qty_ordered": "", "qty_received*": "",
                   "delivery_note": "number of the supplier's delivery note", "inspector": "", "remark": ""},
    },
    "stock": {
        "about": "Stock on hand per material and batch at a point in time.",
        "fields": {"snapshot_date": "date", "site": "", "material_id*": "", "batch": "", "quantity*": "",
                   "uom": "", "safety_stock": "", "weeks_of_cover": "", "last_count": "date of last physical count"},
    },
    "shipment": {
        "about": "An inbound shipment that has not been received yet.",
        "fields": {"shipment_id*": "", "carrier_id": "", "mode": "sea, air or road", "reference": "container or AWB",
                   "supplier_id": "", "po_numbers": "", "material_ids": "", "route_id": "", "origin": "",
                   "destination": "", "dest_site": "", "etd": "date", "eta": "date", "status": "",
                   "via": "transhipment point", "last_lat": "", "last_lon": "",
                   "updated_at": "datetime of the last status update", "note": "free-text comment"},
    },
    "packout": {
        "about": "Units packed per day, product and line.",
        "fields": {"date*": "date", "site": "", "product_id*": "", "line": "", "units_packed*": ""},
    },
    "route": {
        "about": "A transport lane with its usual transit time.",
        "fields": {"route_id*": "", "origin": "", "destination": "", "mode": "", "carrier_id": "",
                   "transit_days": "", "via": ""},
    },
    "location": {
        "about": "A place in the network: a port, an airport, a terminal or a site.",
        "fields": {"code*": "locode, airport code or site id", "name": "", "country": "", "city": "",
                   "lat": "", "lon": "", "kind": "e.g. sea, air, road, dc"},
    },
    "carrier": {
        "about": "A logistics provider.",
        "fields": {"carrier_id*": "", "name": "", "city": "", "country": "", "modes": ""},
    },
}


def field_names(entity: str) -> list[str]:
    return [f.rstrip("*") for f in ENTITIES[entity]["fields"]]


def required_fields(entity: str) -> list[str]:
    return [f[:-1] for f in ENTITIES[entity]["fields"] if f.endswith("*")]
