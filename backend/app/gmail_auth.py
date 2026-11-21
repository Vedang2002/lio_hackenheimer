"""One-time sign-in for the agents' mailbox: `uv run python -m app.gmail_auth`.

Opens the browser for consent and stores a refresh token in `.env`. The Gmail API
runs over HTTPS, which also works on hosts that block the SMTP ports.
"""

import json
import re
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import ROOT

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_FILE = ROOT / "data" / "gmail_oauth_client.json"
ENV_FILE = ROOT / ".env"


def set_env(values: dict[str, str]) -> None:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        text, n = re.subn(rf"(?m)^{key}=.*$", lambda _: line, text)
        if not n:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


def main() -> None:
    client = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))["installed"]
    state, result = secrets.token_urlsafe(16), {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            result.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = client["auth_uri"] + "?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
    })
    print("Opening the browser. If nothing opens, visit:\n" + url, flush=True)
    webbrowser.open(url)
    server.timeout = 280
    while "code" not in result and "error" not in result:
        before = len(result)
        server.handle_request()
        if len(result) == before:
            raise SystemExit("Timed out waiting for the sign-in.")
    if result.get("state") != state or "code" not in result:
        raise SystemExit(f"Sign-in failed: {result.get('error', 'state mismatch')}")

    token = httpx.post(client["token_uri"], data={
        "code": result["code"], "client_id": client["client_id"], "client_secret": client["client_secret"],
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=30)
    token.raise_for_status()
    token = token.json()
    profile = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                        headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=30)
    profile.raise_for_status()
    address = profile.json()["emailAddress"]
    set_env({"GMAIL_CLIENT_ID": client["client_id"], "GMAIL_CLIENT_SECRET": client["client_secret"],
             "GMAIL_REFRESH_TOKEN": token["refresh_token"], "MAIL_ADDRESS": address})
    print(f"Signed in as {address}. Granted scopes: {token.get('scope')}")


if __name__ == "__main__":
    main()
