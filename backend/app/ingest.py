"""Read every file version that has not been read yet: `uv run python -m app.ingest`.

Pass `--all` to read everything again, e.g. after the canonical vocabulary changed.
Pass path fragments to read only matching files.
"""

import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import get_settings
from app.db import connect
from app.documents import DocumentIngest
from app.tabular import IngestResult, TabularIngest

log = logging.getLogger(__name__)
READERS = 8


def pending(conn: sqlite3.Connection, everything: bool = False) -> list[sqlite3.Row]:
    unread = "" if everything else (
        "AND NOT EXISTS (SELECT 1 FROM ingest_log l WHERE l.path = f.path AND l.sha256 = f.sha256 AND l.status != 'failed')")
    return conn.execute(
        f"SELECT f.path, f.sha256, f.first_seen FROM files f WHERE f.removed_at IS NULL {unread} ORDER BY f.path"
    ).fetchall()


def _failed(conn: sqlite3.Connection, f: sqlite3.Row, e: Exception) -> IngestResult:
    # One unreadable file must not stop the others; it stays pending and is retried on the next run.
    log.warning("could not read %s: %s", f["path"], e)
    detail = f"{type(e).__name__}: {e}"[:500]
    conn.execute(
        "INSERT INTO ingest_log (ts, path, sha256, status, rows, detail) VALUES (datetime('now'), ?, ?, 'failed', 0, ?)",
        (f["path"], f["sha256"], detail))
    conn.commit()
    return IngestResult("failed", None, 0, detail)


def ingest_pending(conn: sqlite3.Connection, data_dir: Path, everything: bool = False,
                   only: tuple[str, ...] = ()) -> list[tuple[str, IngestResult]]:
    files = [f for f in pending(conn, everything) if not only or any(o in f["path"] for o in only)]
    blob = lambda f: (data_dir / "blobs" / f["sha256"]).read_bytes()
    tabular, documents, results = TabularIngest(conn), DocumentIngest(conn), []

    for f in (f for f in files if f["path"].lower().endswith(".csv")):
        try:
            results.append((f["path"], tabular.ingest(f["path"], f["sha256"], blob(f))))
        except Exception as e:
            results.append((f["path"], _failed(conn, f, e)))

    docs = [f for f in files if not f["path"].lower().endswith(".csv")]
    readings = {f["sha256"]: r for f in docs if (r := documents.cached(f["sha256"])) is not None}

    def read(f):
        try:
            return documents.read(f["path"], f["sha256"], blob(f), f["first_seen"])
        except Exception as e:
            return e

    todo = [f for f in docs if f["sha256"] not in readings]
    with ThreadPoolExecutor(READERS) as pool:
        for f, reading in zip(todo, pool.map(read, todo)):
            readings[f["sha256"]] = reading
    for f in docs:
        reading = readings[f["sha256"]]
        if isinstance(reading, Exception):
            results.append((f["path"], _failed(conn, f, reading)))
        else:
            results.append((f["path"], IngestResult(*documents.store(f["path"], f["sha256"], reading))))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # supplier paperwork is not ASCII
    s = get_settings()
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    only = tuple(a for a in sys.argv[1:] if not a.startswith("--"))
    for path, r in ingest_pending(connect(s.db_path), s.data_dir, everything="--all" in flags, only=only):
        print(f"{r.status:12} {r.entity or '-':20} {r.rows:>5}  {path}  {r.detail}")
