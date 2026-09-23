"""Inbound webhook tests: raw MIME POST -> stored -> refs extracted -> pending."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.fakes import MSG_2_FULL

HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_inbound_raw_mime(client):
    r = client.post(
        "/v1/inbound",
        headers={**HEADERS, "Content-Type": "message/rfc822"},
        content=MSG_2_FULL,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message_id"] > 0
    assert body["refs"] >= 2  # cos + oss links inside MSG_2_FULL

    # message is fully stored (body already local — no IMAP needed)
    msg = client.get(f"/v1/messages/{body['message_id']}", headers=HEADERS).json()
    assert "GBK" in msg["subject"] or msg["subject"]  # parsed
    refs = client.get(f"/v1/messages/{body['message_id']}/objects", headers=HEADERS).json()
    providers = {r["provider"] for r in refs}
    assert "aliyun-oss" in providers and "tencent-cos" in providers


def test_inbound_json_base64(client):
    r = client.post(
        "/v1/inbound",
        headers=HEADERS,
        json={"raw_base64": base64.b64encode(MSG_2_FULL).decode()},
    )
    assert r.status_code == 201
    assert r.json()["refs"] >= 2


def test_inbound_rejects_bad_payload(client):
    r = client.post("/v1/inbound", headers=HEADERS, json={"raw_base64": "!!!"})
    assert r.status_code == 400
    r = client.post("/v1/inbound", headers={**HEADERS, "Content-Type": "message/rfc822"},
                    content=b"")
    assert r.status_code == 400


def test_webhook_account_excluded_from_sync(client, monkeypatch):
    """The synthetic inbound account must never be IMAP-synced."""
    client.post("/v1/inbound", headers={**HEADERS, "Content-Type": "message/rfc822"},
                content=MSG_2_FULL)

    from app.mail import imap as imap_client
    connected: list[str] = []

    def fake_connect(acct):
        connected.append(acct.address)
        raise Exception("should not be called for webhook accounts")

    monkeypatch.setattr(imap_client, "connect", fake_connect)
    from app.services.sync import sync_all_accounts
    sync_all_accounts()
    assert "inbound@webhook.local" not in connected
