"""The agents' mailbox, over the Gmail API (HTTPS, so it also works where SMTP ports are blocked).

Sending is fenced in: a message only goes out to an address on the allow-list the
caller passes in, which is built from the supplier contacts found in the data.
"""

import base64
import os
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from html.parser import HTMLParser

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
CASE_HEADER = "X-Soony-Case"
SENT_BY_HEADER = "X-Soony-Sent-By"


class RecipientNotAllowed(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender: str
    to: str
    subject: str
    date: str
    message_id: str
    in_reply_to: str
    references: str
    body: str
    attachments: list[dict] = field(default_factory=list)
    case: str = ""
    sent_by: str = ""
    automatic: bool = False


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []

    def handle_data(self, data):
        self.chunks.append(data)


def _decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def build_message(sender: str, to: str, subject: str, body: str, reply_to: Message | None = None,
                  case: str = "", sent_by: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
    if case:
        # The mailbox is the lasting record of what was sent; this ties a sent mail back to its case.
        msg[CASE_HEADER] = case
    if sent_by:
        msg[SENT_BY_HEADER] = sent_by  # "agent" or "human": who released it
    if reply_to and reply_to.message_id:
        msg["In-Reply-To"] = reply_to.message_id
        msg["References"] = f"{reply_to.references} {reply_to.message_id}".strip()
    msg.set_content(body, charset="utf-8")
    return msg


def parse_message(raw: dict) -> Message:
    """Turn a Gmail API message into something an agent can read."""
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    plain, html, attachments = [], [], []

    def walk(part: dict) -> None:
        body, mime = part.get("body", {}), part.get("mimeType", "")
        if part.get("filename"):
            attachments.append({"filename": part["filename"], "mime_type": mime,
                                "attachment_id": body.get("attachmentId"), "size": body.get("size", 0)})
        elif body.get("data") and mime in ("text/plain", "text/html"):
            (plain if mime == "text/plain" else html).append(_decode(body["data"]).decode("utf-8", errors="replace"))
        for child in part.get("parts", []):
            walk(child)

    walk(payload)
    if plain:
        text = "\n".join(plain)
    else:
        parser = _Text()
        parser.feed("\n".join(html))
        text = "".join(parser.chunks)
    return Message(
        id=raw["id"], thread_id=raw.get("threadId", ""), sender=parseaddr(headers.get("from", ""))[1].lower(),
        to=parseaddr(headers.get("to", ""))[1].lower(), subject=headers.get("subject", ""),
        date=headers.get("date", ""), message_id=headers.get("message-id", ""),
        in_reply_to=headers.get("in-reply-to", ""), references=headers.get("references", ""),
        body=text.strip(), attachments=attachments, case=headers.get(CASE_HEADER.lower(), ""),
        sent_by=headers.get(SENT_BY_HEADER.lower(), ""),
        # Receipts and out-of-office notes say so in their headers; nobody wrote them, so nobody should answer them.
        automatic=(headers.get("auto-submitted", "no").lower() != "no" or "x-autoreply" in headers
                   or headers.get("precedence", "").lower() in ("auto_reply", "bulk", "junk")),
    )


class Mailbox:
    def __init__(self, address: str, client_id: str, client_secret: str, refresh_token: str,
                 http: httpx.Client | None = None):
        self.address = address.lower()
        self._auth = {"client_id": client_id, "client_secret": client_secret,
                      "refresh_token": refresh_token, "grant_type": "refresh_token"}
        self._http = http or httpx.Client(timeout=60)
        self._token, self._expires = "", 0.0

    @classmethod
    def from_env(cls) -> "Mailbox":
        return cls(os.environ["MAIL_ADDRESS"], os.environ["GMAIL_CLIENT_ID"],
                   os.environ["GMAIL_CLIENT_SECRET"], os.environ["GMAIL_REFRESH_TOKEN"])

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if time.time() > self._expires - 60:
            r = self._http.post(TOKEN_URL, data=self._auth)
            r.raise_for_status()
            token = r.json()
            self._token, self._expires = token["access_token"], time.time() + token.get("expires_in", 3600)
        r = self._http.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {self._token}"}, **kwargs)
        r.raise_for_status()
        return r.json()

    def send(self, to: str, subject: str, body: str, allowed: set[str], reply_to: Message | None = None,
             case: str = "", sent_by: str = "") -> dict:
        to = to.strip().lower()
        if to not in {a.lower() for a in allowed}:
            raise RecipientNotAllowed(f"{to} is not a known contact; nothing was sent")
        msg = build_message(self.address, to, subject, body, reply_to, case, sent_by)
        payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
        if reply_to:
            payload["threadId"] = reply_to.thread_id
        sent = self._request("POST", "/messages/send", json=payload)
        # Gmail replaces the Message-ID on the way out; later replies are matched against the real one.
        stored = self._request("GET", f"/messages/{sent['id']}",
                               params={"format": "metadata", "metadataHeaders": "Message-ID"})
        return {"id": sent["id"], "thread_id": sent.get("threadId", ""), "message_id": parse_message(stored).message_id}

    def search(self, query: str, limit: int = 50) -> list[str]:
        found = self._request("GET", "/messages", params={"q": query, "maxResults": limit})
        return [m["id"] for m in found.get("messages", [])]

    def get(self, message_id: str) -> Message:
        return parse_message(self._request("GET", f"/messages/{message_id}", params={"format": "full"}))

    def attachment(self, message_id: str, attachment_id: str) -> bytes:
        return _decode(self._request("GET", f"/messages/{message_id}/attachments/{attachment_id}")["data"])
