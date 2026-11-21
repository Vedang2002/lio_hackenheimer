import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    portal_url: str
    portal_token: str
    data_dir: Path
    db_path: Path
    poll_seconds: int
    public_url: str | None
    access_key: str | None
    users: tuple[dict, ...]
    mail_configured: bool
    agents_on: bool


def _users() -> tuple[dict, ...]:
    """People who may use the desk, each with their own key, role and approval limit. Set by the customer:
    APP_USERS='[{"name": "Paul Winter", "key": "...", "role": "approver", "limit_eur": 50000}]'.
    Roles: viewer (reads), approver (approves up to the limit), admin (no limit)."""
    try:
        users = json.loads(os.environ.get("APP_USERS") or "[]")
    except ValueError:
        users = []
    return tuple(u for u in users if isinstance(u, dict) and u.get("name") and u.get("key")
                 and u.get("role") in ("viewer", "approver", "admin"))


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", ROOT / "data"))
    return Settings(
        portal_url=os.environ["SOONY_PORTAL_URL"].rstrip("/"),
        portal_token=os.environ["SOONY_API_TOKEN"],
        data_dir=data_dir,
        db_path=Path(os.environ.get("DB_PATH", data_dir / "state.sqlite")),
        poll_seconds=int(os.environ.get("POLL_SECONDS", "120")),
        public_url=os.environ.get("RENDER_EXTERNAL_URL"),
        access_key=os.environ.get("APP_ACCESS_KEY") or None,
        users=_users(),
        mail_configured=all(os.environ.get(k) for k in ("MAIL_ADDRESS", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                                                        "GMAIL_REFRESH_TOKEN")),
        # Exactly one running copy may have its agents on: two would each pay for the same work and each
        # write to the suppliers. So the loop only runs where it is switched on explicitly.
        agents_on=os.environ.get("AGENTS", "off").lower() in ("on", "true", "1"),
    )
