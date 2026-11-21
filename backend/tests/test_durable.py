import hashlib

import httpx
import pytest

from app import durable
from app.db import connect
from app.portal import PortalSync


class MemoryStore:
    def __init__(self):
        self.data, self.puts = None, 0

    def get(self):
        return self.data

    def put(self, data, digest):
        self.data, self.puts = data, self.puts + 1


@pytest.fixture(autouse=True)
def fresh():
    durable._last_saved = None


def test_state_comes_back_on_an_empty_disk(tmp_path):
    store, first = MemoryStore(), tmp_path / "a" / "state.sqlite"
    conn = connect(first)
    conn.execute("INSERT INTO cases (key, material_id, status, basis, created_at, updated_at) VALUES ('part:X', 'X', 'proposed', 'b', 't', 't')")
    conn.commit()
    assert durable.save(first, store) is True

    second = tmp_path / "b" / "state.sqlite"  # the service restarted on a wiped disk
    assert durable.restore(second, store) is True
    assert connect(second).execute("SELECT key FROM cases").fetchone()[0] == "part:X"


def test_unchanged_state_is_not_uploaded_again(tmp_path):
    store, path = MemoryStore(), tmp_path / "state.sqlite"
    conn = connect(path)
    assert durable.save(path, store) is True
    assert durable.save(path, store) is False
    conn.execute("INSERT INTO settings VALUES ('k', 'v', 't')")
    conn.commit()
    assert durable.save(path, store) is True
    assert store.puts == 2


def test_an_existing_database_is_never_overwritten(tmp_path):
    store, path = MemoryStore(), tmp_path / "state.sqlite"
    connect(path).close()
    durable.save(path, store)
    conn = connect(path)
    conn.execute("INSERT INTO settings VALUES ('newer', 'v', 't')")
    conn.commit()
    assert durable.restore(path, store) is False
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_without_a_database_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "state.sqlite"
    connect(path).close()
    assert durable.save(path) is False and durable.restore(tmp_path / "other.sqlite") is False


def test_files_missing_after_a_restore_are_fetched_again_without_being_reported_as_drops(tmp_path):
    content = b"a,b\n1,2\n"
    listing = {"path": "soony/erp/stock.csv", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "url": "x"}
    downloads = []

    def handler(request):
        if request.url.path == "/api/v1/files":
            return httpx.Response(200, json={"count": 1, "files": [listing]})
        downloads.append(request.url.path)
        return httpx.Response(200, content=content)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://portal")
    conn = connect(":memory:")
    PortalSync(conn, client, tmp_path / "old").run()
    downloads.clear()

    events = PortalSync(conn, client, tmp_path / "new").run()  # same database, empty disk
    assert events == [] and len(downloads) == 1
    assert (tmp_path / "new" / "portal" / "soony/erp/stock.csv").read_bytes() == content
