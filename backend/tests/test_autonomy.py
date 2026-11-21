import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agent import basis_of, hand_over, save_case
from app.autonomy import MAX_AUTOMATIC_PER_CONTACT, hold_reason, send_what_is_safe
from app.db import connect
from app.pipeline import awaiting_reply
from app.views import case_state, _messages
from tests.test_outbox import FakeMailbox, NOWAK

NOW = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
CASE = "part:CMP-GAM-008"
INQUIRY = {"type": "email_supplier", "to_name": "Agnieszka Nowak", "to_email": NOWAK, "language": "pl",
           "subject": "Pytanie", "body": "Kiedy dostawa?", "purpose": "ask", "commits": "nothing"}
ORDER = {**INQUIRY, "subject": "Zamówienie", "body": "Zamawiamy 5 900 szt.", "commits": "money_or_terms"}
NOTE = {"type": "internal_escalation", "to_name": "Paul Winter", "subject": "Approve", "body": "Please approve.", "purpose": "approve"}


def harmless(subject, body):
    return {"commits": False, "reason": "only asks"}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.delenv("AUTO_SEND", raising=False)
    c = connect(":memory:")
    c.execute("INSERT INTO records (entity, source_path, source_sha256, row_no, data) VALUES ('supplier', 'p.csv', 's', 1, ?)",
              (json.dumps({"supplier_id": "SUP-0028", "contact_name": "Agnieszka Nowak", "contact_email": NOWAK}),))
    return c


def case(conn, *actions, **decision):
    save_case(conn, "CMP-GAM-008", "basis", {"summary": "s", "options": [], "actions": list(actions), **decision}, [])
    return [r["id"] for r in conn.execute("SELECT id FROM messages WHERE gmail_id IS NULL ORDER BY id")]


def held(conn):
    return {r["subject"]: r["held_because"] for r in conn.execute("SELECT subject, held_because FROM messages WHERE status = 'draft'")}


def test_a_question_goes_out_on_its_own_and_an_order_waits_for_a_person(conn):
    case(conn, INQUIRY, ORDER, NOTE)
    box = FakeMailbox()
    sent = send_what_is_safe(conn, box, NOW, harmless)
    assert [m["subject"] for m in box.sent] == ["Pytanie"] and len(sent) == 1
    assert conn.execute("SELECT sent_by FROM messages WHERE status = 'sent'").fetchone()[0] == "agent"
    assert "only a person may approve" in held(conn)["Zamówienie"]
    assert conn.execute("SELECT status FROM messages WHERE subject = 'Approve'").fetchone()[0] == "internal"


def test_a_message_without_a_label_is_treated_as_a_commitment(conn):
    case(conn, {k: v for k, v in INQUIRY.items() if k != "commits"}, {**INQUIRY, "subject": "Odd", "commits": "maybe"})
    assert send_what_is_safe(conn, FakeMailbox(), NOW, harmless) == []
    assert set(r[0] for r in conn.execute("SELECT commits FROM messages")) == {"money_or_terms"}


def test_the_independent_check_can_stop_what_the_agent_called_harmless(conn):
    case(conn, INQUIRY)
    box, asked = FakeMailbox(), []

    def strict(subject, body):
        asked.append(subject)
        return {"commits": True, "reason": "it accepts the offer"}

    send_what_is_safe(conn, box, NOW, strict)
    send_what_is_safe(conn, box, NOW + timedelta(hours=1), strict)
    assert box.sent == [] and "it accepts the offer" in held(conn)["Pytanie"]
    assert asked == ["Pytanie"]  # the verdict on an unchanged text is not bought twice


def test_only_files_count_as_evidence_and_an_invented_file_is_reported():
    from app.agent import check_decision

    decision = {"options": [{"title": "t", "evidence": ["soony/erp/stock.csv", "Supplier email, 18:05"]}], "recommended_option": 0,
                "evidence": ["soony/erp/stock.csv", "Supplier email from Agnieszka Nowak", "soony/erp/invented.csv",
                             "Karton Nowak reply 18:19: checking Warta Trans ETA/cost"], "actions": []}
    problems = check_decision(decision, {"soony/erp/stock.csv"}, set())
    assert problems == ["cites files that do not exist: soony/erp/invented.csv"]
    assert decision["options"][0]["evidence"] == ["soony/erp/stock.csv"]


def test_a_check_that_breaks_never_defaults_to_sending(conn):
    case(conn, INQUIRY)

    def broken(subject, body):
        raise RuntimeError("model unavailable")

    box = FakeMailbox()
    assert send_what_is_safe(conn, box, NOW, broken) == [] and box.sent == []
    assert "could not be completed" in held(conn)["Pytanie"]


@pytest.mark.parametrize("setup, expected", [
    (lambda c: c.execute("UPDATE cases SET needs_human = 'supplier declined'"), "handed to a person"),
    (lambda c: c.execute("UPDATE cases SET decision = ?", (json.dumps({"checks": ["cites a missing file"]}),)), "failed a check"),
    (lambda c: c.execute("UPDATE messages SET counterpart_email = 'x@elsewhere.com'"), "not a supplier contact"),
])
def test_nothing_goes_out_from_a_case_that_is_not_sound(conn, setup, expected):
    case(conn, INQUIRY)
    setup(conn)
    box = FakeMailbox()
    assert send_what_is_safe(conn, box, NOW, harmless) == [] and box.sent == []
    assert expected in held(conn)["Pytanie"]


def test_the_switch_turns_all_automatic_sending_off(conn, monkeypatch):
    monkeypatch.setenv("AUTO_SEND", "off")
    case(conn, INQUIRY)
    box = FakeMailbox()
    assert send_what_is_safe(conn, box, NOW, harmless) == [] and box.sent == []


def test_agents_do_not_chase_while_the_last_message_is_fresh_or_forever(conn):
    box = FakeMailbox()
    case(conn, INQUIRY)
    send_what_is_safe(conn, box, NOW, harmless)
    conn.execute("UPDATE messages SET sent_at = ? WHERE status = 'sent'", (NOW.isoformat(),))

    case(conn, {**INQUIRY, "subject": "Przypomnienie"})
    assert send_what_is_safe(conn, box, NOW + timedelta(minutes=5), harmless) == []
    assert "recent and unanswered" in held(conn)["Przypomnienie"]
    assert len(send_what_is_safe(conn, box, NOW + timedelta(minutes=40), harmless)) == 1

    conn.execute("UPDATE messages SET sent_at = ? WHERE status = 'sent'", ((NOW - timedelta(hours=3)).isoformat(),))
    for n in range(MAX_AUTOMATIC_PER_CONTACT):
        case(conn, {**INQUIRY, "subject": f"Chaser {n}"})
        send_what_is_safe(conn, box, NOW + timedelta(hours=n + 1), harmless)
        conn.execute("UPDATE messages SET sent_at = ? WHERE status = 'sent'", ((NOW - timedelta(hours=3)).isoformat(),))
    assert len(box.sent) == MAX_AUTOMATIC_PER_CONTACT  # two before, one more, then a person has to look
    assert "without a person looking" in held(conn)[f"Chaser {MAX_AUTOMATIC_PER_CONTACT - 1}"]


def test_handover_keeps_the_last_recommendation_but_offers_nothing_to_send(conn):
    case(conn, INQUIRY, summary="Art books are short.")
    hand_over(conn, "CMP-GAM-008", "basis-2", "The agent could not work this case.")
    row = conn.execute("SELECT needs_human, basis, decision FROM cases").fetchone()
    assert row["needs_human"].startswith("The agent could not") and row["basis"] == "basis-2"
    assert json.loads(row["decision"])["summary"] == "Art books are short."
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    assert case_state(_messages(conn, CASE), row["needs_human"]) == "needs_human"


def test_case_state_follows_the_correspondence(conn):
    case(conn, INQUIRY)
    assert case_state(_messages(conn, CASE)) == "proposed"  # a harmless draft is about to go out by itself
    case(conn, ORDER)
    assert case_state(_messages(conn, CASE)) == "needs_approval"


def test_a_case_is_not_reworked_while_the_supplier_still_has_time_to_answer(conn):
    case(conn, INQUIRY)
    assert not awaiting_reply(conn, "CMP-GAM-008", NOW)
    send_what_is_safe(conn, FakeMailbox(), NOW, harmless)
    conn.execute("UPDATE messages SET sent_at = ? WHERE status = 'sent'", (NOW.isoformat(),))
    assert awaiting_reply(conn, "CMP-GAM-008", NOW + timedelta(minutes=10))
    assert not awaiting_reply(conn, "CMP-GAM-008", NOW + timedelta(minutes=45))


def test_a_round_to_several_suppliers_is_worked_once_not_once_per_answer(conn):
    def mail(n, contact, direction, minutes):
        conn.execute("INSERT INTO messages (case_key, direction, status, counterpart_email, gmail_id, sent_at, created_at) "
                     "VALUES (?, ?, 'x', ?, ?, ?, 't')", (CASE, direction, contact, f"g{n}", (NOW + timedelta(minutes=minutes)).isoformat()))

    mail(1, "kim@suppliers.example", "out", 0)
    mail(2, "ortiz@suppliers.example", "out", 0)
    mail(3, "ortiz@suppliers.example", "in", 4)
    assert awaiting_reply(conn, "CMP-GAM-008", NOW + timedelta(minutes=5))       # the other may still answer
    assert not awaiting_reply(conn, "CMP-GAM-008", NOW + timedelta(minutes=12))  # grace over: work with what we have
    mail(4, "kim@suppliers.example", "in", 13)
    assert not awaiting_reply(conn, "CMP-GAM-008", NOW + timedelta(minutes=13))


def brief(gap, need=18900, short=5900, mail=()):
    return {"findings": [{"title": "Receipt books 80,000, note says 8,000"}], "correspondence_so_far": list(mail),
            "lines_using_this_part": [{"product_id": "CE", "copies_short_if_nobody_acts": short, "stopped_by": "CMP-GAM-008"}],
            "this_part": {"gap": gap, "need": need, "confirmed_by_supplier": 0}, "priority_rule": {"order": ["CE"]}}


def test_small_changes_in_the_numbers_do_not_reopen_a_case_but_material_ones_do():
    base = basis_of(brief(5900))
    assert basis_of(brief(5800)) == base                      # an hour of packing later
    assert basis_of(brief(2000)) != base                      # most of the gap closed
    assert basis_of(brief(0, short=0)) != base                # solved
    assert basis_of(brief(5900, mail=[{"direction": "supplier answered", "at": "t", "subject": "Re"}])) != base


def test_spend_in_the_last_hour_counts_only_the_last_hour(conn):
    from app.pipeline import spent_last_hour

    for minutes, usd in ((10, 0.25), (50, 0.40), (70, 5.00)):
        conn.execute("INSERT INTO llm_usage (ts, purpose, model, input_tokens, cached_tokens, output_tokens, cost_usd) "
                     "VALUES (?, 'case:X', 'm', 0, 0, 0, ?)", ((NOW - timedelta(minutes=minutes)).isoformat(timespec="seconds"), usd))
    assert spent_last_hour(conn, NOW) == pytest.approx(0.65)
