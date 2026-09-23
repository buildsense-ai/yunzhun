"""Jev judgment layer tests (fake upstream, both providers)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import jev as jev_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}


def _stub_settings(monkeypatch, **kw):
    from types import SimpleNamespace

    base = dict(jev_provider="vercel", vercel_gateway_key="", jev_api_key="", jev_model="")
    base.update(kw)
    stub = SimpleNamespace(**base)
    monkeypatch.setattr(jev_service, "get_settings", lambda: stub)


JEV_RESPONSE = {
    "model": "jev-test-model",
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
    calls: list[dict] = []

    def fake_post(url, payload, api_key):
        calls.append({"url": url, "payload": payload, "api_key": api_key})
        return JEV_RESPONSE

    monkeypatch.setattr(jev_service, "_post", fake_post)
    monkeypatch.setattr(
        jev_service, "resolve_provider",
        lambda: ("https://ai-gateway.vercel.sh/typesafe/v1/systemone",
                 "typesafe-ai/jev", "fake-gw-key"),
    )
    # these tests exercise the manual /judge endpoint; keep sync from
    # auto-running the pipeline (event-driven kick) so bodies stay unfetched
    import app.services.pipeline as pipeline_service

    monkeypatch.setattr(pipeline_service, "process_pending", lambda **kw: {})
    with TestClient(app) as c:
        yield c, calls


def _make_account_with_message(c):
    r = c.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "jev-test@163.com", "auth_code": "AUTHCODE123", "verify": False},
    )
    if r.status_code == 409:  # account persisted from a previous test in shared DB
        account_id = int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
    else:
        account_id = r.json()["id"]
    c.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={})
    items = c.get(
        f"/v1/accounts/{account_id}/messages", headers=HEADERS, params={"folder": "INBOX"}
    ).json()
    return account_id, items


def test_resolve_provider_vercel(monkeypatch):
    _stub_settings(monkeypatch, jev_provider="vercel")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw-key-123")
    url, model, key = jev_service.resolve_provider()
    assert url == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert model == "typesafe-ai/jev"
    assert key == "gw-key-123"


def test_resolve_provider_typesafe(monkeypatch):
    _stub_settings(monkeypatch, jev_provider="typesafe")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key-456")
    url, model, key = jev_service.resolve_provider()
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert model == "jev-latest"
    assert key == "ts-key-456"


def test_resolve_provider_no_key(monkeypatch):
    _stub_settings(monkeypatch, jev_provider="vercel")
    for e in ("AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "YUNZHUN_VERCEL_GATEWAY_KEY"):
        monkeypatch.delenv(e, raising=False)
    with pytest.raises(jev_service.JevUnavailable):
        jev_service.resolve_provider()


def test_judge_requires_config(monkeypatch):
    def raise_unavailable():
        raise jev_service.JevUnavailable("Jev provider 'vercel' is not configured")
    monkeypatch.setattr(jev_service, "resolve_provider", raise_unavailable)
    with TestClient(app) as c:
        r = c.post("/v1/messages/999/judge", headers=HEADERS)
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]


def test_judge_flow(client):
    c, calls = client
    account_id, items = _make_account_with_message(c)
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
    assert body["model"] == "jev-test-model"

    # routed to the Vercel-compatible endpoint with provider model + key
    assert calls[0]["url"] == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert calls[0]["payload"]["model"] == "typesafe-ai/jev"
    assert calls[0]["api_key"] == "fake-gw-key"

    # state sent upstream contains subject and storage refs
    state = calls[0]["payload"]["state"]
    assert "Report" in state
    assert "aliyun-oss://bktdir/db/dump.csv" in state
    assert set(calls[0]["payload"]["questions"]) == {
        "category", "storage_delivery", "action_required",
    }

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
    account_id, items = _make_account_with_message(c)
    untouched = next(m["id"] for m in items if m["uid"] == 3)  # body never fetched
    r = c.post(f"/v1/messages/{untouched}/judge", headers=HEADERS)
    assert r.status_code == 409
