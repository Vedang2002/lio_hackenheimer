import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SOONY_PORTAL_URL", "http://portal")
    monkeypatch.setenv("SOONY_API_TOKEN", "t")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("APP_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GMAIL_REFRESH_TOKEN", raising=False)  # tests must never reach the real mailbox
    return TestClient(app)  # not entered as a context manager, so the watcher does not start


def add_draft(tmp_path, status="draft"):
    conn = connect(tmp_path / "state.sqlite")
    mid = conn.execute(
        "INSERT INTO messages (case_key, direction, status, counterpart_email, subject, body, body_english, created_at) "
        "VALUES ('part:X', 'out', ?, 'a@suppliers.example', 'Old', 'Old body', 'Old english', 't')", (status,)).lastrowid
    conn.commit()
    return conn, mid


def test_overview_explains_what_is_missing_before_anything_is_read(client):
    body = client.get("/api/overview").json()
    assert body["forecast"]["ready"] is False and body["cases"] == [] and body["mail_address"] is None


def test_with_a_key_configured_every_data_call_needs_it(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "s3cret")
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/status", headers={"X-Access-Key": "wrong"}).status_code == 401
    assert client.get("/api/status", headers={"X-Access-Key": "s3cret"}).status_code == 200
    assert client.get("/api/status?key=s3cret").status_code == 200  # for evidence links opened in a new tab
    assert client.post("/api/messages/1/send").status_code == 401


def test_a_public_deployment_without_a_key_serves_no_data(client, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com")
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/overview").status_code == 503
    assert client.get("/api/file", params={"path": "soony/note.md"}).status_code == 503


def test_evidence_files_are_served_only_from_the_mirror(client, tmp_path):
    (tmp_path / "portal" / "soony").mkdir(parents=True)
    (tmp_path / "portal" / "soony" / "note.md").write_text("hello")
    (tmp_path / "secret.txt").write_text("no")
    assert client.get("/api/file", params={"path": "soony/note.md"}).text == "hello"
    assert client.get("/api/file", params={"path": "../secret.txt"}).status_code == 404
    assert client.get("/api/file", params={"path": "soony/missing.md"}).status_code == 404


def test_a_draft_can_be_reworded_but_a_sent_message_cannot(client, tmp_path):
    conn, mid = add_draft(tmp_path)
    assert client.patch(f"/api/messages/{mid}", json={"subject": "New", "body": "New body"}).status_code == 200
    row = conn.execute("SELECT subject, body, body_english FROM messages WHERE id = ?", (mid,)).fetchone()
    assert tuple(row) == ("New", "New body", None)  # the old translation no longer matches
    conn.execute("UPDATE messages SET status = 'sent' WHERE id = ?", (mid,))
    conn.commit()
    assert client.patch(f"/api/messages/{mid}", json={"subject": "x", "body": "y"}).status_code == 409


def test_nothing_is_sent_without_a_mailbox(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS", "on")
    _, mid = add_draft(tmp_path)
    assert client.post(f"/api/messages/{mid}/send").status_code == 503


def test_a_copy_without_agents_cannot_send(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS", "off")
    _, mid = add_draft(tmp_path)
    assert client.post(f"/api/messages/{mid}/send").status_code == 409


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_status_before_first_sync(client):
    assert client.get("/api/status").json() == {"files": 0, "last_sync": None, "drops": []}


def test_status_lists_drops_but_not_the_baseline(client, tmp_path):
    conn = connect(tmp_path / "state.sqlite")
    conn.execute("INSERT INTO sync_runs (started_at, listed, new, changed, removed, failed) VALUES ('t0', 2, 2, 0, 0, 0)")
    conn.execute("INSERT INTO files VALUES ('a.csv', 'x', 1, 't0', 't0', NULL)")
    conn.execute("INSERT INTO file_events (run_id, ts, path, kind, baseline) VALUES (1, 't0', 'a.csv', 'new', 1)")
    conn.execute("INSERT INTO file_events (run_id, ts, path, kind, baseline) VALUES (1, 't1', 'b.pdf', 'new', 0)")
    conn.commit()
    body = client.get("/api/status").json()
    assert body["files"] == 1
    assert body["last_sync"]["listed"] == 2
    assert body["drops"] == [{"ts": "t1", "path": "b.pdf", "kind": "new"}]


USERS = '[{"name": "Paul Winter", "key": "pw", "role": "approver", "limit_eur": 50000}, {"name": "Maren Vogt", "key": "mv", "role": "admin"}]'


def test_each_person_has_a_key_and_the_shared_key_only_looks(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_KEY", "shared")
    monkeypatch.setenv("APP_USERS", USERS)
    assert client.get("/api/me", headers={"X-Access-Key": "pw"}).json() == {"name": "Paul Winter", "role": "approver", "limit_eur": 50000, "verified": True}
    assert client.get("/api/me", headers={"X-Access-Key": "shared"}).json()["role"] == "viewer"
    assert client.get("/api/me", headers={"X-Access-Key": "nope"}).status_code == 401
    assert client.post("/api/cases/X/rework", headers={"X-Access-Key": "shared"}).status_code == 403


def test_approval_limits_come_from_the_customer_not_the_model():
    from app.main import may_approve

    order = {"commits": "money_or_terms"}
    case = lambda cost, cur="EUR": {"decision": '{"options": [{"cost": %s, "currency": "%s"}], "recommended_option": 0}' % (cost, cur)}
    paul = {"name": "Paul Winter", "role": "approver", "limit_eur": 50000}
    assert may_approve(paul, order, case(12390)) is None
    assert "your limit" in may_approve(paul, order, case(74324))
    assert "not known in euros" in may_approve(paul, order, case(74324, "USD"))
    assert may_approve(paul, {"commits": "nothing"}, case(74324)) is None          # a question commits nothing
    assert may_approve({"name": "Maren", "role": "admin"}, order, case(74324)) is None
    assert "look, not approve" in may_approve({"name": None, "role": "viewer"}, order, case(1))


def test_a_refused_approval_is_on_the_record(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTS", "on")
    monkeypatch.setenv("APP_USERS", USERS)
    monkeypatch.setattr("app.main._mailbox", lambda settings: object())
    conn, mid = add_draft(tmp_path)
    conn.execute("UPDATE messages SET commits = 'money_or_terms' WHERE id = ?", (mid,))
    conn.execute("INSERT INTO cases (key, material_id, status, basis, decision, created_at, updated_at) VALUES "
                 "('part:X', 'X', 'proposed', 'b', '{\"options\": [{\"cost\": 74324, \"currency\": \"EUR\"}], \"recommended_option\": 0}', 't', 't')")
    conn.commit()
    assert client.post(f"/api/messages/{mid}/send", headers={"X-Access-Key": "pw"}).status_code == 403
    assert tuple(conn.execute("SELECT actor, action FROM audit").fetchone()) == ("Paul Winter", "approval refused")
    assert conn.execute("SELECT status FROM messages WHERE id = ?", (mid,)).fetchone()[0] == "draft"
