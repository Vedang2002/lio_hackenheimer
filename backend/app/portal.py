"""Mirror the Soony portal and record what changed.

The portal never announces drops: files appear, are replaced or vanish. Every sync
compares the listing's SHA-256 values with what we last saw and writes one event
per difference. Every version is kept in a content-addressed store so agents can
compare a file with what it said before.
"""

import hashlib
import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.db import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileEvent:
    path: str
    kind: str
    old_sha256: str | None
    new_sha256: str | None
    baseline: bool


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class PortalSync:
    def __init__(self, conn: sqlite3.Connection, client: httpx.Client, data_dir: Path):
        self.conn = conn
        self.client = client
        self.tree = data_dir / "portal"
        self.blobs = data_dir / "blobs"

    def blob_path(self, sha256: str) -> Path:
        return self.blobs / sha256

    def _target(self, path: str) -> Path:
        target = (self.tree / path).resolve()
        if not target.is_relative_to(self.tree.resolve()):
            raise ValueError(f"path escapes the mirror: {path!r}")
        return target

    def _fetch(self, path: str, sha256: str) -> None:
        """Make sure the blob for `sha256` exists and the mirror shows it at `path`."""
        target, blob = self._target(path), self.blob_path(sha256)
        self.blobs.mkdir(parents=True, exist_ok=True)
        if not blob.exists():
            if target.exists() and _sha256(target) == sha256:
                shutil.copyfile(target, blob)
            else:
                tmp = blob.with_suffix(".part")
                with self.client.stream("GET", f"/api/v1/files/{quote(path)}") as r:
                    r.raise_for_status()
                    with tmp.open("wb") as f:
                        for chunk in r.iter_bytes():
                            f.write(chunk)
                got = _sha256(tmp)
                if got != sha256:
                    tmp.unlink()
                    raise ValueError(f"checksum mismatch for {path}: listed {sha256}, got {got}")
                tmp.replace(blob)
        if not target.exists() or _sha256(target) != sha256:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(blob, target)

    def run(self) -> list[FileEvent]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        r = self.client.get("/api/v1/files")
        r.raise_for_status()
        listed = {f["path"]: f for f in r.json()["files"]}
        known = {
            row["path"]: row["sha256"]
            for row in self.conn.execute("SELECT path, sha256 FROM files WHERE removed_at IS NULL")
        }
        baseline = self.conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0] == 0
        run_id = self.conn.execute(
            "INSERT INTO sync_runs (started_at, listed, new, changed, removed, failed) VALUES (?, ?, 0, 0, 0, 0)",
            (now, len(listed)),
        ).lastrowid

        events: list[FileEvent] = []
        failed = 0
        for path, f in listed.items():
            old = known.get(path)
            if old == f["sha256"]:
                if not self.blob_path(old).exists():
                    # The database came back after a restart but the disk is empty: fetch the file again.
                    # Nothing changed on the portal, so this is not a drop.
                    try:
                        self._fetch(path, old)
                    except (httpx.HTTPError, ValueError, OSError) as e:
                        failed += 1
                        log.warning("could not fetch %s: %s", path, e)
                continue
            try:
                self._fetch(path, f["sha256"])
            except (httpx.HTTPError, ValueError, OSError) as e:
                # Leave the row untouched so the next sync retries this file.
                failed += 1
                log.warning("could not fetch %s: %s", path, e)
                continue
            self.conn.execute(
                """INSERT INTO files (path, sha256, bytes, first_seen, last_changed) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, bytes = excluded.bytes,
                       last_changed = excluded.last_changed, removed_at = NULL""",
                (path, f["sha256"], f["bytes"], now, now),
            )
            events.append(FileEvent(path, "changed" if old else "new", old, f["sha256"], baseline))

        for path in known.keys() - listed.keys():
            self.conn.execute("UPDATE files SET removed_at = ? WHERE path = ?", (now, path))
            self._target(path).unlink(missing_ok=True)
            events.append(FileEvent(path, "removed", known[path], None, baseline))

        self.conn.executemany(
            "INSERT INTO file_events (run_id, ts, path, kind, old_sha256, new_sha256, baseline) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(run_id, now, e.path, e.kind, e.old_sha256, e.new_sha256, int(e.baseline)) for e in events],
        )
        counts = {k: sum(e.kind == k for e in events) for k in ("new", "changed", "removed")}
        self.conn.execute(
            "UPDATE sync_runs SET new = ?, changed = ?, removed = ?, failed = ? WHERE id = ?",
            (counts["new"], counts["changed"], counts["removed"], failed, run_id),
        )
        self.conn.commit()
        return events


def portal_client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=120)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = get_settings()
    with portal_client(s.portal_url, s.portal_token) as client:
        events = PortalSync(connect(s.db_path), client, s.data_dir).run()
    for e in events:
        if not e.baseline:
            print(f"{e.kind:8} {e.path}")
    print(f"{len(events)} events ({'baseline' if events and events[0].baseline else 'drops'})")
