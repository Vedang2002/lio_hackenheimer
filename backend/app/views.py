"""What the interface shows: one page on what can stop a line, and the case behind each item."""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from app.agent import priority_order
from app.forecast import simulate
from app.outbox import parse_when
from app.pipeline import DAILY_BUDGET_USD, INFORMATIONAL
from app.truth import Facts, assess


def _messages(conn: sqlite3.Connection, case_key: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM messages WHERE case_key = ? ORDER BY id", (case_key,)).fetchall()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: (parse_when(r["sent_at"] or r["created_at"]) or epoch, r["id"]))
    return [{**{k: r[k] for k in ("id", "direction", "status", "counterpart_name", "counterpart_email", "language",
                                  "subject", "body", "body_english", "purpose", "created_at", "sent_at", "commits",
                                  "sent_by", "held_because", "approved_by")},
             "reading": json.loads(r["reading"]) if r["reading"] else None} for r in rows]


def case_state(messages: list[dict], needs_human: str | None = None) -> str:
    """Where a case stands, told from its correspondence rather than stored."""
    mail = [m for m in messages if m["status"] in ("sent", "received") and m["sent_by"] not in ("auto", "other")]
    if needs_human:
        return "needs_human"
    if any(m["status"] == "draft" and (m["commits"] != "nothing" or m["held_because"]) for m in messages):
        return "needs_approval"
    if mail and mail[-1]["direction"] == "in":
        return "supplier_answered"
    if mail:
        return "waiting_for_supplier"
    return "proposed"


def _case(conn: sqlite3.Connection, row: sqlite3.Row, facts: Facts) -> dict:
    decision = json.loads(row["decision"]) if row["decision"] else {}
    messages = _messages(conn, row["key"])
    options = decision.get("options") or []
    pick = decision.get("recommended_option", 0)
    spend = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS usd, COUNT(*) AS calls FROM llm_usage WHERE purpose = ?",
                         (f"case:{row['material_id']}",)).fetchone()
    return {
        "key": row["key"], "material_id": row["material_id"],
        "description": (facts.components.get(row["material_id"]) or {}).get("description"),
        "state": case_state(messages, row["needs_human"]), "needs_human": row["needs_human"],
        "updated_at": row["updated_at"], "cost_usd": round(spend["usd"], 2), "card": decision.get("card"),
        "summary": decision.get("summary"), "why": decision.get("why"),
        "recommended": options[pick] if 0 <= pick < len(options) else None,
        "options": options, "recommended_option": pick, "ruled_out": decision.get("ruled_out") or [],
        "approval": decision.get("approval"), "open_questions": decision.get("open_questions") or [],
        "checks": decision.get("checks") or [], "evidence": decision.get("evidence") or [],
        "trace": json.loads(row["trace"]) if row["trace"] else [], "messages": messages,
    }


def overview(conn: sqlite3.Connection, now: datetime) -> dict:
    facts = Facts(conn)
    assessment = assess(conn, now.date())
    priority = priority_order(conn)
    evidence = simulate(conn, assessment, priority["order"])
    erp = simulate(conn, assessment, priority["order"], trust_erp=True)
    acted = simulate(conn, assessment, priority["order"], with_actions=True)
    short = {c["material_id"] for c in evidence.get("components", []) if c["gap"] > 0 or c["inbound_needing_action"] > 0}

    findings = []
    for f in assessment.findings:
        level = "info" if f.kind in INFORMATIONAL else "stops_line" if f.material_id in short else "watch"
        findings.append({**asdict(f), "level": level,
                         "description": (facts.components.get(f.material_id) or {}).get("description")})
    findings.sort(key=lambda f: ("stops_line", "watch", "info").index(f["level"]))

    cases = [_case(conn, r, facts) for r in conn.execute("SELECT * FROM cases ORDER BY key")]
    # What each fix is worth on its own. A line needs every part, so fixes do not simply add up.
    base = {p["product_id"]: p["short"] for p in evidence.get("products", [])}
    open_parts = frozenset(c["material_id"] for c in cases)
    for case in cases:
        alone = simulate(conn, assessment, priority["order"], covered=frozenset({case["material_id"]}))
        after = {p["product_id"]: p for p in alone.get("products", [])}
        case["saves_alone"] = sum(base[p] - after[p]["short"] for p in base if p in after)
        blockers = {after[p]["first_stop"]["material_id"] for p in after if after[p]["short"] > 0 and after[p]["first_stop"]}
        case["then_limited_by"] = sorted((facts.components.get(b) or {}).get("description") or b
                                         for b in blockers & (open_parts - {case["material_id"]}))
    solved = simulate(conn, assessment, priority["order"], covered=open_parts)
    plan = {"short_now": sum(base.values()),
            "short_if_all_solved": sum(p["short"] for p in solved.get("products", [])) if solved.get("ready") else None}
    last_sync = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
    drops = conn.execute("SELECT ts, path, kind FROM file_events WHERE baseline = 0 ORDER BY id DESC LIMIT 30").fetchall()
    review = conn.execute(
        """SELECT path, detail FROM ingest_log l WHERE status = 'needs_review'
           AND id = (SELECT MAX(id) FROM ingest_log WHERE path = l.path)""").fetchall()
    return {
        "now": now.isoformat(timespec="minutes"),
        "forecast": evidence, "erp_forecast": erp, "forecast_if_acted": acted, "priority": priority,
        "findings": findings, "cases": cases, "plan": plan,
        "drops": [dict(d) for d in drops], "last_sync": dict(last_sync) if last_sync else None,
        "files": conn.execute("SELECT COUNT(*) FROM files WHERE removed_at IS NULL").fetchone()[0],
        "documents_read": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "needs_review": [dict(r) for r in review],
        "spend": spend(conn, now),
        "audit": [dict(r) for r in conn.execute("SELECT ts, actor, action, case_key, detail FROM audit ORDER BY id DESC LIMIT 60")],
        "activity": activity(conn, facts),
        "foreign_mail": [dict(r) for r in conn.execute(
            "SELECT counterpart_name, subject, sent_at FROM messages WHERE direction = 'out' AND sent_by = 'other' ORDER BY id")],
        "agent_work": {
            "sent_by_agents": conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent' AND sent_by = 'agent'").fetchone()[0],
            "approved_by_people": conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent' AND COALESCE(sent_by, 'human') = 'human'").fetchone()[0],
            "replies": conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'received' AND COALESCE(sent_by, '') != 'auto'").fetchone()[0],
            "findings": sum(f["level"] != "info" for f in findings),
        },
    }


def activity(conn: sqlite3.Connection, facts: Facts, limit: int = 40) -> list[dict]:
    """What happened, newest first, in one list: files arriving, mail going out and coming in, cases handed over."""
    name = lambda key: (facts.components.get((key or "").removeprefix("part:")) or {}).get("description") or key
    items = []
    for r in conn.execute("SELECT run_id, MIN(ts) AS ts, COUNT(*) AS n, GROUP_CONCAT(path, '|') AS paths FROM file_events "
                          "WHERE baseline = 0 GROUP BY run_id"):
        files = [p.rsplit("/", 1)[-1] for p in r["paths"].split("|")]
        items.append({"at": r["ts"], "kind": "files", "case_key": None,
                      "text": f"{r['n']} new or changed file{'s' if r['n'] > 1 else ''} arrived and {'were' if r['n'] > 1 else 'was'} read",
                      "detail": ", ".join(files[:6]) + ("…" if len(files) > 6 else "")})
    for m in conn.execute("SELECT * FROM messages WHERE status IN ('sent', 'received')"):
        if m["status"] == "received":
            reading = json.loads(m["reading"]) if m["reading"] else {}
            said = "sent an automatic reply about" if m["sent_by"] == "auto" else "answered about"
            items.append({"at": m["sent_at"], "kind": "reply", "case_key": m["case_key"],
                          "text": f"{m['counterpart_name']} {said} {name(m['case_key'])}", "detail": reading.get("summary")})
        else:
            who = ("The agent asked" if m["sent_by"] == "agent" else "Something other than the agents wrote to"
                   if m["sent_by"] == "other" else "You approved a message to")
            items.append({"at": m["sent_at"], "kind": "sent_by_agent" if m["sent_by"] == "agent" else "sent_by_human",
                          "case_key": m["case_key"], "text": f"{who} {m['counterpart_name']} about {name(m['case_key'])}",
                          "detail": m["subject"]})
    for c in conn.execute("SELECT key, needs_human, updated_at FROM cases WHERE needs_human IS NOT NULL"):
        items.append({"at": c["updated_at"], "kind": "handover", "case_key": c["key"],
                      "text": f"The agent handed {name(c['key'])} to a person", "detail": c["needs_human"]})
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=lambda i: parse_when(i["at"]) or epoch, reverse=True)
    for i in items:
        moment = parse_when(i["at"])
        i["at"] = moment.isoformat(timespec="seconds") if moment else None
    return items[:limit]


def spend(conn: sqlite3.Connection, now: datetime) -> dict:
    """What the agents have cost today, by kind of work."""
    start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    kinds = {"reading files": "purpose IN ('record_document', 'map_columns')", "working cases": "purpose LIKE 'case:%'",
             "reading replies": "purpose = 'reply'", "translations and checks": "purpose IN ('translate', 'guard', 'set_priority')"}
    by_kind = {name: conn.execute(f"SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM llm_usage WHERE ts >= ? AND {where}",
                                  (start,)).fetchone() for name, where in kinds.items()}
    return {"today_usd": round(sum(v[0] for v in by_kind.values()), 2), "budget_usd": DAILY_BUDGET_USD,
            "by_kind": [{"kind": k, "usd": round(v[0], 2), "calls": v[1]} for k, v in by_kind.items()]}
