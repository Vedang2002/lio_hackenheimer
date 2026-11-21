"""What the agents may send on their own, and what goes to a person.

The line follows Soony's approval policy: spending and accepting terms belong to
named roles, so anything that commits money or terms waits for a human. Questions,
reminders and requests for confirmation commit nothing, and those the agents send
themselves. Every automatic send has to pass all the checks below; the first one
that fails is recorded on the message, so a person can see why it was held.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

from app import llm
from app.db import audit
from app.mail import Mailbox
from app.outbox import blanks, parse_when, send_draft, supplier_contacts

log = logging.getLogger(__name__)

MAX_AUTOMATIC_PER_CONTACT = 3      # after this many unattended mails to one contact in a case, a person looks
COOL_DOWN = timedelta(minutes=20)  # do not write again while the last mail is this fresh and unanswered
SECOND_OPINION = "An independent check read it as a commitment:"

CHECK_SCHEMA = {
    "type": "object", "required": ["commits", "reason"],
    "properties": {
        "commits": {"type": "boolean"},
        "reason": {"type": "string", "description": "One sentence"},
    },
}


def enabled() -> bool:
    return os.environ.get("AUTO_SEND", "inquiries").lower() not in ("off", "false", "0", "none")


def second_opinion(subject: str, body: str) -> dict:
    """An independent, cheap read of the finished text: does sending it commit the sender to anything?"""
    prompt = (
        "A company is about to send this message to a supplier. Would sending it commit the company to anything: "
        "placing, confirming, changing, releasing or cancelling an order, accepting an offer, a price or a condition, "
        "or promising a payment or an action? Asking for information, availability, a quote, a confirmation or a "
        "status commits nothing. Judge the text as written, not what the sender may have meant.\n\n"
        f"Subject: {subject}\n\n{body}"
    )
    return llm.extract([{"role": "user", "content": prompt}], "judge_commitment", "Record the judgement.",
                       CHECK_SCHEMA, purpose="guard")


def hold_reason(conn: sqlite3.Connection, message: sqlite3.Row, now: datetime,
                checker: Callable[[str, str], dict] = second_opinion) -> str | None:
    """Why this draft must not go out unattended; None if it may."""
    if not enabled():
        return "Automatic sending is switched off."
    if message["commits"] != "nothing":
        return "It commits money or terms, which only a person may approve."
    if blanks(f"{message['subject']} {message['body']}"):
        return "It still has blanks to fill in."
    case = conn.execute("SELECT * FROM cases WHERE key = ?", (message["case_key"],)).fetchone()
    if not case:
        return "It belongs to no open case."
    if case["needs_human"]:
        return f"The case was handed to a person: {case['needs_human']}"
    checks = (json.loads(case["decision"]) if case["decision"] else {}).get("checks") or []
    if checks:
        return f"The recommendation failed a check: {checks[0]}"
    contact = (message["counterpart_email"] or "").lower()
    if contact not in supplier_contacts(conn):
        return "The recipient is not a supplier contact in the data."
    history = conn.execute(
        "SELECT direction, sent_by, sent_at FROM messages WHERE case_key = ? AND counterpart_email = ? AND gmail_id IS NOT NULL "
        "AND COALESCE(sent_by, '') NOT IN ('auto', 'other') ORDER BY id",
        (message["case_key"], message["counterpart_email"])).fetchall()
    if sum(h["sent_by"] == "agent" for h in history) >= MAX_AUTOMATIC_PER_CONTACT:
        return f"{MAX_AUTOMATIC_PER_CONTACT} messages have gone to this contact without a person looking."
    if history and history[-1]["direction"] == "out":
        sent = parse_when(history[-1]["sent_at"])
        if not sent or now.astimezone(timezone.utc) - sent < COOL_DOWN:
            return "The last message to this contact is recent and unanswered."
    if (message["held_because"] or "").startswith(SECOND_OPINION):
        return message["held_because"]  # the text has not changed, so the verdict stands; do not pay for it again
    verdict = checker(message["subject"] or "", message["body_english"] or message["body"] or "")
    if verdict.get("commits"):
        return f"{SECOND_OPINION} {verdict.get('reason')}"
    return None


def send_what_is_safe(conn: sqlite3.Connection, mailbox: Mailbox, now: datetime | None = None,
                      checker: Callable[[str, str], dict] = second_opinion) -> list[dict]:
    """Send every draft that passes all checks; note on the others why they wait."""
    now = now or datetime.now(timezone.utc)
    sent = []
    drafts = conn.execute("SELECT * FROM messages WHERE status = 'draft' AND direction = 'out' ORDER BY id").fetchall()
    for draft in drafts:
        try:
            reason = hold_reason(conn, draft, now, checker)
        except Exception as e:  # a failing check never defaults to sending
            log.exception("check failed for message %s", draft["id"])
            reason = f"A safety check could not be completed: {e}"
        if reason:
            conn.execute("UPDATE messages SET held_because = ? WHERE id = ?", (reason, draft["id"]))
            conn.commit()
            continue
        try:
            send_draft(conn, mailbox, draft["id"], sent_by="agent")
            sent.append({"id": draft["id"], "case_key": draft["case_key"], "to": draft["counterpart_email"]})
            audit(conn, "agent", "sent a question on its own", draft["case_key"], f"{draft['counterpart_name']}: {draft['subject']}")
        except Exception as e:
            log.exception("could not send message %s", draft["id"])
            conn.execute("UPDATE messages SET held_because = ? WHERE id = ?", (f"Sending failed: {e}", draft["id"]))
            conn.commit()
    return sent
