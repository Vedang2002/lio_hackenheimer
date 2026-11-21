import base64
import email
import email.policy
import json

import httpx
import pytest

from app.mail import Mailbox, RecipientNotAllowed, build_message, parse_message

SUPPLIER = "a.nowak@suppliers.example"


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(**overrides) -> dict:
    return {
        "id": "m1", "threadId": "t1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": f"Agnieszka Nowak <{SUPPLIER.upper()}>"},
                {"name": "To", "value": "agents@example.com"},
                {"name": "Subject", "value": "Re: Oferta"},
                {"name": "Message-ID", "value": "<reply-1@suppliers.example>"},
                {"name": "References", "value": "<first@example.com>"},
            ],
            "parts": [
                {"mimeType": "multipart/alternative", "body": {}, "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Potwierdzam: 8 000 szt. załadunek dziś.")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>Potwierdzam</p>")}},
                ]},
                {"mimeType": "application/pdf", "filename": "potwierdzenie.pdf",
                 "body": {"attachmentId": "att1", "size": 1234}},
            ],
        },
        **overrides,
    }


class FakeGmail:
    def __init__(self):
        self.sent, self.token_requests = [], 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_requests += 1
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path.endswith("/messages/send"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "sent1", "threadId": "t1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(200, json=gmail_message())


@pytest.fixture
def gmail():
    return FakeGmail()


@pytest.fixture
def mailbox(gmail):
    return Mailbox("Agents@Example.com", "id", "secret", "refresh", httpx.Client(transport=httpx.MockTransport(gmail.handler)))


def test_parses_sender_body_and_attachments():
    msg = parse_message(gmail_message())
    assert (msg.sender, msg.subject, msg.thread_id) == (SUPPLIER, "Re: Oferta", "t1")
    assert msg.body == "Potwierdzam: 8 000 szt. załadunek dziś."
    assert msg.attachments == [{"filename": "potwierdzenie.pdf", "mime_type": "application/pdf",
                                "attachment_id": "att1", "size": 1234}]


def test_automatic_replies_are_recognised_by_their_headers():
    assert parse_message(gmail_message()).automatic is False
    raw = gmail_message()
    raw["payload"]["headers"].append({"name": "Auto-Submitted", "value": "auto-replied"})
    assert parse_message(raw).automatic is True


def test_html_only_mail_is_reduced_to_its_text():
    raw = gmail_message()
    raw["payload"] = {"mimeType": "text/html", "headers": [], "body": {"data": b64("<div>Ja, <b>bestätigt</b>.</div>")}}
    assert parse_message(raw).body == "Ja, bestätigt."


def test_message_keeps_non_ascii_text_and_threads_a_reply():
    original = parse_message(gmail_message())
    msg = build_message("agents@example.com", SUPPLIER, "Re: Oferta", "Dziękujemy, zamawiamy 8 000 szt.", reply_to=original)
    parsed = email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)
    assert parsed.get_payload(decode=True).decode("utf-8") == "Dziękujemy, zamawiamy 8 000 szt.\n"
    assert parsed["In-Reply-To"] == "<reply-1@suppliers.example>"
    assert parsed["References"] == "<first@example.com> <reply-1@suppliers.example>"
    assert parsed["Message-ID"].endswith("@example.com>")


def test_send_goes_out_to_known_contacts_only(mailbox, gmail):
    with pytest.raises(RecipientNotAllowed):
        mailbox.send("someone@elsewhere.com", "Hi", "Body", allowed={SUPPLIER})
    assert gmail.sent == []

    result = mailbox.send(f" {SUPPLIER.upper()} ", "Zamówienie", "Treść", allowed={SUPPLIER})
    assert (result["id"], result["message_id"]) == ("sent1", "<reply-1@suppliers.example>")
    sent = email.message_from_bytes(base64.urlsafe_b64decode(gmail.sent[0]["raw"]), policy=email.policy.default)
    assert (sent["To"], sent["From"], sent["Subject"]) == (SUPPLIER, "agents@example.com", "Zamówienie")
    assert "threadId" not in gmail.sent[0]


def test_reply_stays_in_the_thread_and_token_is_reused(mailbox, gmail):
    original = mailbox.get(mailbox.search("from:supplier")[0])
    mailbox.send(SUPPLIER, "Re: Oferta", "OK", allowed={SUPPLIER}, reply_to=original)
    assert gmail.sent[0]["threadId"] == "t1"
    assert gmail.token_requests == 1
