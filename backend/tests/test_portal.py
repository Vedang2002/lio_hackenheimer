import hashlib

import httpx
import pytest

from app.db import connect
from app.portal import PortalSync


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakePortal:
    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.listed_sha: dict[str, str] = {}
        self.downloads: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/files":
            files = [
                {"path": p, "bytes": len(b), "sha256": self.listed_sha.get(p, sha(b)), "url": f"http://portal/api/v1/files/{p}"}
                for p, b in sorted(self.files.items())
            ]
            return httpx.Response(200, json={"count": len(files), "files": files})
        name = path.removeprefix("/api/v1/files/")
        self.downloads.append(name)
        if name not in self.files:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, content=self.files[name])


@pytest.fixture
def portal():
    return FakePortal({"soony/erp/stock.csv": b"a,b\n1,2\n", "suppliers/SUP-0028_Karton Nowak/notices/n.pdf": b"%PDF-1"})


@pytest.fixture
def sync(portal, tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(portal.handler), base_url="http://portal")
    return PortalSync(connect(":memory:"), client, tmp_path)


def test_first_run_mirrors_everything_as_baseline(portal, sync, tmp_path):
    events = sync.run()
    assert {(e.path, e.kind, e.baseline) for e in events} == {(p, "new", True) for p in portal.files}
    for p, b in portal.files.items():
        assert (tmp_path / "portal" / p).read_bytes() == b


def test_unchanged_portal_produces_no_events_and_no_downloads(portal, sync):
    sync.run()
    portal.downloads.clear()
    assert sync.run() == []
    assert portal.downloads == []


def test_replaced_file_is_a_change_and_old_version_is_kept(portal, sync, tmp_path):
    sync.run()
    old = portal.files["soony/erp/stock.csv"]
    portal.files["soony/erp/stock.csv"] = b"a,b\n1,3\n"
    (event,) = sync.run()
    assert (event.kind, event.baseline) == ("changed", False)
    assert event.old_sha256 == sha(old)
    assert sync.blob_path(event.old_sha256).read_bytes() == old
    assert (tmp_path / "portal/soony/erp/stock.csv").read_bytes() == b"a,b\n1,3\n"


def test_new_and_removed_files_are_reported(portal, sync, tmp_path):
    sync.run()
    del portal.files["soony/erp/stock.csv"]
    portal.files["soony/news/strike.md"] = b"strike"
    events = {e.path: e.kind for e in sync.run()}
    assert events == {"soony/erp/stock.csv": "removed", "soony/news/strike.md": "new"}
    assert not (tmp_path / "portal/soony/erp/stock.csv").exists()


def test_file_already_on_disk_is_not_downloaded_again(portal, sync, tmp_path):
    for p, b in portal.files.items():
        (tmp_path / "portal" / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "portal" / p).write_bytes(b)
    assert len(sync.run()) == 2
    assert portal.downloads == []


def test_corrupt_download_is_skipped_and_retried_next_sync(portal, sync, tmp_path):
    good = "suppliers/SUP-0028_Karton Nowak/notices/n.pdf"
    portal.listed_sha["soony/erp/stock.csv"] = "0" * 64
    assert [e.path for e in sync.run()] == [good]
    assert not (tmp_path / "portal/soony/erp/stock.csv").exists()
    del portal.listed_sha["soony/erp/stock.csv"]
    assert [(e.path, e.kind) for e in sync.run()] == [("soony/erp/stock.csv", "new")]


def test_listing_cannot_write_outside_the_mirror(portal, sync, tmp_path):
    portal.files["../../evil.txt"] = b"x"
    events = sync.run()
    assert "../../evil.txt" not in {e.path for e in events}
    assert not (tmp_path.parent / "evil.txt").exists()
