"""State that survives a restart.

A free host wipes its disk whenever the service restarts. Everything the agents
know and have decided lives in one SQLite file, so after each pass that file is
copied, compressed, into a single row of a Postgres database, and copied back
when the service starts on an empty disk. Without DATABASE_URL nothing happens
and the service simply starts from scratch, as before.
"""

import gzip
import hashlib
import logging
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
_last_saved: str | None = None


class Store(Protocol):
    def get(self) -> bytes | None: ...
    def put(self, data: bytes, digest: str) -> None: ...


class PostgresStore:
    def __init__(self, url: str):
        self.url = url

    def _connect(self):
        import psycopg  # only needed where a database is configured

        conn = psycopg.connect(self.url, connect_timeout=20)
        conn.execute("CREATE TABLE IF NOT EXISTS agent_state (id INT PRIMARY KEY, saved_at TIMESTAMPTZ NOT NULL, "
                     "sha256 TEXT NOT NULL, data BYTEA NOT NULL)")
        return conn

    def get(self) -> bytes | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM agent_state WHERE id = 1").fetchone()
        return bytes(row[0]) if row else None

    def put(self, data: bytes, digest: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_state (id, saved_at, sha256, data) VALUES (1, now(), %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET saved_at = now(), sha256 = EXCLUDED.sha256, data = EXCLUDED.data",
                (digest, data))


def default_store() -> Store | None:
    url = os.environ.get("DATABASE_URL")
    return PostgresStore(url) if url else None


def _snapshot(db_path: Path) -> bytes:
    """A consistent copy of the database, taken while it is in use."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.sqlite"
        with closing(sqlite3.connect(db_path, timeout=30)) as src, closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        return copy.read_bytes()


def save(db_path: Path, store: Store | None = None) -> bool:
    """Copy the state to the store if it changed since the last copy. Returns whether it was written."""
    global _last_saved
    store = store or default_store()
    if not store or not Path(db_path).exists():
        return False
    raw = _snapshot(Path(db_path))
    digest = hashlib.sha256(raw).hexdigest()
    if digest == _last_saved:
        return False
    store.put(gzip.compress(raw, mtime=0), digest)
    _last_saved = digest
    return True


def restore(db_path: Path, store: Store | None = None) -> bool:
    """Bring the state back onto an empty disk. A database that is already there is never overwritten."""
    store = store or default_store()
    if not store or Path(db_path).exists():
        return False
    data = store.get()
    if not data:
        return False
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(db_path).write_bytes(gzip.decompress(data))
    log.info("state restored from the database (%d bytes)", len(data))
    return True
