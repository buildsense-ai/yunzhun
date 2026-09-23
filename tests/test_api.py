"""End-to-end API tests against fake NetEase servers."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.mail import pop3 as pop3_client
from app.mail import smtp as smtp_client
from tests.fakes import FakeImap, FakePop

HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(pop3_client, "connect", lambda acct: FakePop())
    sent: dict = {}
    monkeypatch.setattr(smtp_client, "send_mail", lambda acct, **kw: sent.update(kw) or "<sent@163.com>")
    monkeypatch.setattr(smtp_client, "verify", lambda acct: None)
    with TestClient(app) as c:
        yield c, sent


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_auth_required(client):
    c, _ = client
    assert c.get("/v1/accounts").status_code == 401


def test_full_flow(client, monkeypatch):
    c, sent = client

    # -- account create (verify against fakes) --
    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "me@163.com", "auth_code": "AUTHCODE123"},
    )
    assert r.status_code == 201, r.text
    account_id = r.json()["id"]

    # duplicate rejected
    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "me@163.com", "auth_code": "AUTHCODE123", "verify": False},
    )
    assert r.status_code == 409

    # -- trigger first sync --
    r = c.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={"mode": "incremental"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["folders"] == 1
    assert body["new_messages"] == 3

    # -- folders --
    r = c.get(f"/v1/accounts/{account_id}/folders", headers=HEADERS)
    folders = r.json()
    assert len(folders) == 1
    assert folders[0]["name"] == "INBOX"
    assert folders[0]["uidvalidity"] == 42
    assert folders[0]["messages_count"] == 3
    folder_id = folders[0]["id"]

    # -- message list (headers only) --
    r = c.get(f"/v1/accounts/{account_id}/messages", headers=HEADERS, params={"folder": "INBOX"})
    items = r.json()
    assert [m["uid"] for m in items] == [3, 2, 1]  # newest first
    assert items[2]["subject"] == "测试"  # RFC2047 GBK subject decoded
    assert items[1]["body_fetched"] is False
    msg2_id = items[1]["id"]

    # -- filters --
    r = c.get(
        f"/v1/accounts/{account_id}/messages",
        headers=HEADERS,
        params={"q": "Report"},
    )
    assert len(r.json()) == 1

    # -- detail triggers lazy body fetch --
    r = c.get(f"/v1/messages/{msg2_id}", headers=HEADERS)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "GBK 编码的邮件" in detail["text_body"]
    assert detail["body_fetched"] is True
    assert len(detail["attachments"]) == 1
    att_id = detail["attachments"][0]["id"]

    # second read served from cache (no error, same content)
    r2 = c.get(f"/v1/messages/{msg2_id}", headers=HEADERS)
    assert r2.json()["text_body"] == detail["text_body"]

    # -- attachment download --
    r = c.get(f"/v1/messages/{msg2_id}/attachments/{att_id}", headers=HEADERS)
    assert r.status_code == 200
    assert r.content == base64.b64decode("JVBERi0xLjQK")
    assert "report.pdf" in r.headers["content-disposition"]

    # -- raw source --
    r = c.get(f"/v1/messages/{msg2_id}/raw", headers=HEADERS)
    assert r.status_code == 200
    assert b"multipart/mixed" in r.content

    # -- html message snippet is generated when body fetched; uid2 was fetched above --
    r = c.get(f"/v1/accounts/{account_id}/messages", headers=HEADERS)
    by_uid = {m["uid"]: m for m in r.json()}
    assert by_uid[2]["snippet"] and "GBK" in by_uid[2]["snippet"]
    assert by_uid[3]["body_fetched"] is False  # untouched message stays header-only
    assert by_uid[3]["snippet"] is None

    # -- flags: mark unread (remove \\Seen) --
    r = c.patch(
        f"/v1/messages/{items[2]['id']}/flags",
        headers=HEADERS,
        json={"add": [], "remove": ["\\Seen"]},
    )
    assert r.status_code == 200
    assert r.json()["seen"] is False

    # -- send --
    r = c.post(
        f"/v1/accounts/{account_id}/send",
        headers=HEADERS,
        json={"to": ["friend@example.com"], "subject": "Hi", "text": "yo"},
    )
    assert r.status_code == 200
    assert sent["subject"] == "Hi"
    assert sent["to"] == ["friend@example.com"]

    # -- safe fetch endpoint --
    class FakeResp:
        status_code = 200
        content = b"PDFDATA"
        headers = {"Content-Type": "application/pdf", "Content-Length": "7"}

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "request", lambda method, url, **kw: FakeResp())
    r = c.post(
        "/v1/objects/fetch",
        headers=HEADERS,
        json={"url": "https://bkt-1250000000.cos.ap-guangzhou.myqcloud.com/reports/2026/summary.pdf?sign=x"},
    )
    assert r.status_code == 200 and r.content == b"PDFDATA"
    # unknown host rejected before any network call
    monkeypatch.setattr(
        _httpx, "request",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network call attempted")),
    )
    r = c.post("/v1/objects/fetch", headers=HEADERS, json={"url": "https://evil.example.com/secret"})
    assert r.status_code == 400

    # -- pop3 fallback --
    r = c.get(f"/v1/accounts/{account_id}/pop3/messages", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.json()) == 2

    # -- full resync wipes + rebuilds cache --
    r = c.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={"mode": "full"})
    assert r.status_code == 200
    assert r.json()["new_messages"] == 3


def test_verify_bad_auth_code(client, monkeypatch):
    c, _ = client

    def bad_connect(acct):
        return FakeImap(bad=True)  # never used; login raises below

    def raise_login(*a, **k):
        raise imap_client.MailError("LOGIN failed")

    monkeypatch.setattr(imap_client, "connect", lambda acct: (_ for _ in ()).throw(imap_client.MailError("LOGIN failed")) )
    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "bad@163.com", "auth_code": "whatever", "verify": True},
    )
    assert r.status_code == 400
    assert "LOGIN failed" in r.json()["detail"]
