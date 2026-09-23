"""Jev judgment layer tests (fake upstream)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import jev as jev_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}

JEV_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "delivery",
            "confidence": 0.92,
            "probabilities": {"delivery": 0.95, "billing": 0.0, "security": 0.0,
                              "notification": 0.05, "personal": 0.0, "other": 0.0},
        },
        "storage_delivery": {"type": "noul", "noul": 0.97},
        "action_required": {"type": "score", "score": 1.0, "confidence": 1.0,
                            "probabilities": {"0": 0.0, "1": 1.0, "2": 0.0}},
    },
    "usage": {"input_tokens": 300, "output_tokens": 40},
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(jev_service, "_api_key", lambda: "fake-key")
    calls: list[dict] = []

    def fake_post(payload, api_key):
        calls.append(payload)
        return JEV_RESPONSE

    monkeypatch.setattr(jev_service, "_post", fake_post)
    with TestClient(app) as c:
        yield c, calls


def test_judge_requires_config(monkeypatch):
    monkeypatch.setattr(jev_service, "_api_key", lambda: "")
    with TestClient(app) as c:
        r = c.post("/v1/messages/999/judge", headers=HEADERS)
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]


def test_judge_flow(client):
    c, calls = client

    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "jev-test@163.com", "auth_code": "AUTHCODE123", "verify": False},
    )
    account_id = r.json()["id"]
    c.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={})
    items = c.get(
        f"/v1/accounts/{account_id}/messages", headers=HEADERS, params={"folder": "INBOX"}
    ).json()
    msg2_id = items[1]["id"]
    c.get(f"/v1/messages/{msg2_id}", headers=HEADERS)  # trigger lazy body fetch

    # judge -> structured answers stored
    r = c.post(f"/v1/messages/{msg2_id}/judge", headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "delivery"
    assert body["category_confidence"] == pytest.approx(0.92)
    assert body["storage_delivery"] == pytest.approx(0.97)
    assert body["action_required"] == 1
    assert body["model"] == "jev-1.13.0"

    # state sent upstream contains subject and storage refs
    state = calls[0]["state"]
    assert "Report" in state
    assert "aliyun-oss://bktdir/db/dump.csv" in state
    q = calls[0]["questions"]
    assert set(q) == {"category", "storage_delivery", "action_required"}

    # stored judgment retrievable
    r = c.get(f"/v1/messages/{msg2_id}/judge", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["category"] == "delivery"

    # account-level filter by category
    r = c.get(
        f"/v1/accounts/{account_id}/judgments",
        headers=HEADERS,
        params={"category": "delivery", "min_storage_delivery": 0.5},
    )
    assert len(r.json()) == 1
    r = c.get(
        f"/v1/accounts/{account_id}/judgments",
        headers=HEADERS,
        params={"category": "billing"},
    )
    assert r.json() == []


def test_judge_unfetched_message_409(client):
    c, _ = client
    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "jev-b@163.com", "auth_code": "AUTHCODE123", "verify": False},
    )
    account_id = r.json()["id"]
    c.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={})
    items = c.get(
        f"/v1/accounts/{account_id}/messages", headers=HEADERS, params={"folder": "INBOX"}
    ).json()
    untouched = next(m["id"] for m in items if m["uid"] == 3)  # body never fetched
    r = c.post(f"/v1/messages/{untouched}/judge", headers=HEADERS)
    assert r.status_code == 409
