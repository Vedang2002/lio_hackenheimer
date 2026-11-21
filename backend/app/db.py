import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL,
    removed_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    listed INTEGER NOT NULL,
    new INTEGER NOT NULL,
    changed INTEGER NOT NULL,
    removed INTEGER NOT NULL,
    failed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS file_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    ts TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('new', 'changed', 'removed')),
    old_sha256 TEXT,
    new_sha256 TEXT,
    baseline INTEGER NOT NULL DEFAULT 0,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS file_events_unprocessed ON file_events (processed_at, id);
CREATE TABLE IF NOT EXISTS mappings (
    signature TEXT PRIMARY KEY,
    example_path TEXT NOT NULL,
    mapping TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    row_no INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_entity ON records (entity);
CREATE INDEX IF NOT EXISTS records_source ON records (source_path);
CREATE TABLE IF NOT EXISTS extractions (
    sha256 TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    path TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    language TEXT,
    supplier_id TEXT,
    doc_date TEXT,
    summary TEXT,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cases (
    key TEXT PRIMARY KEY,
    material_id TEXT,
    status TEXT NOT NULL,
    basis TEXT NOT NULL,
    decision TEXT,
    trace TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_key TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('out', 'in')),
    status TEXT NOT NULL,
    counterpart_name TEXT,
    counterpart_email TEXT,
    language TEXT,
    subject TEXT,
    body TEXT,
    body_english TEXT,
    purpose TEXT,
    gmail_id TEXT,
    thread_id TEXT,
    message_id TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    reading TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS messages_gmail ON messages (gmail_id) WHERE gmail_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    case_key TEXT NOT NULL,
    material_id TEXT NOT NULL,
    quantity REAL NOT NULL,
    dock_at TEXT NOT NULL,
    firm INTEGER NOT NULL,
    condition TEXT,
    shipment_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    case_key TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'needs_review', 'failed')),
    entity TEXT,
    rows INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);
"""


def audit(conn: sqlite3.Connection, actor: str, action: str, case_key: str | None = None, detail: str | None = None) -> None:
    """Append to the record of who did what. Rows are only ever added."""
    from datetime import datetime, timezone

    conn.execute("INSERT INTO audit (ts, actor, action, case_key, detail) VALUES (?, ?, ?, ?, ?)",
                 (datetime.now(timezone.utc).isoformat(timespec="seconds"), actor, action, case_key, detail))
    conn.commit()


ADDED_COLUMNS = {
    "messages": {"reading": "TEXT", "commits": "TEXT", "sent_by": "TEXT", "held_because": "TEXT", "approved_by": "TEXT"},
    "cases": {"needs_human": "TEXT", "runs_log": "TEXT"},
}


def connect(db_path: Path | str) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # The background loop and the API write at the same time: wait for the other writer instead of failing.
    # A request may open its connection on one worker thread and close it on another; each connection is still
    # only ever used by one piece of work at a time.
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # Databases created before a column existed get it added.
    for table, columns in ADDED_COLUMNS.items():
        have = {c["name"] for c in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    return conn
