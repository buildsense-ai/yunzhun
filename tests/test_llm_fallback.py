"""LLM fallback extraction tests (fake gateway)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import jev as jev_service
from app.services import llm_extract as llm_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}

JEV_DELIVERY = {
    "model": "typesafe-ai/jev",
    "answers": {
        "category": {"type": "choice", "choice": "delivery", "confidence": 0.9,
                     "probabilities": {}},
        "storage_delivery": {"type": "noul", "noul": 0.93},
        "action_required": {"type": "score", "score": 1.0, "confidence": 1.0},
    },
}


def test_llm_fallback_triggers_on_delivery_without_regex(monkeypatch):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: JEV_DELIVERY)

    llm_calls: list[dict] = []

    def fake_llm_post(url, payload, api_key):
        llm_calls.append(payload)
        return {"choices": [{"message": {"content":
            '{"addresses": ["s3://prose-bkt/novel/form/data.tar"]}'}}]}

    monkeypatch.setattr(llm_service, "llm_post", fake_llm_post)
    monkeypatch.setattr(llm_service, "_llm_key", lambda: "gw-key")
    from types import SimpleNamespace
    monkeypatch.setattr(
        llm_service, "get_settings",
        lambda: SimpleNamespace(llm_model="openai/gpt-4.1-mini", llm_api_key="",
                                vercel_gateway_key="gw"),
    )
    monkeypatch.setattr(
        jev_service, "get_settings",
        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                jev_api_key="", jev_model="", llm_model="openai/gpt-4.1-mini"),
    )

    with TestClient(app) as c:
        r = c.post("/v1/accounts", headers=HEADERS,
                   json={"address": "llm-test@163.com", "auth_code": "A", "verify": False})
        aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
        c.post(f"/v1/accounts/{aid}/sync", headers=H if (H := HEADERS) else HEADERS, json={})
        items = c.get(f"/v1/accounts/{aid}/messages", headers=HEADERS,
                      params={"folder": "INBOX"}).json()
        # uid 1's body ("plain body") contains no URL -> regex finds nothing
        uid1 = next(m for m in items if m["uid"] == 1)
        c.get(f"/v1/messages/{uid1['id']}", headers=HEADERS)

        r = c.post(f"/v1/messages/{uid1['id']}/judge", headers=HEADERS)
        assert r.status_code == 200, r.text
        # the sync kick's process_pending is global — other accounts' pending
        # mail may also trigger the fallback; what matters is it fired for uid1
        assert len(llm_calls) >= 1

        refs = c.get(f"/v1/messages/{uid1['id']}/objects", headers=HEADERS).json()
        assert len(refs) == 1
        assert refs[0]["source"] == "llm"
        assert refs[0]["provider"] == "s3-compatible"
        assert refs[0]["bucket"] == "prose-bkt"
        assert refs[0]["key"] == "novel/form/data.tar"

        # account-level: llm-sourced ref visible with judgment context
        r = c.get(f"/v1/accounts/{aid}/objects", headers=HEADERS,
                  params={"category": "delivery"})
        llm_refs = [x for x in r.json() if x["source"] == "llm"]
        assert len(llm_refs) == 1
        assert llm_refs[0]["bucket"] == "prose-bkt"
        assert llm_refs[0]["storage_delivery"] == 0.93


def test_llm_fallback_not_triggered_when_regex_found_something(monkeypatch):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: JEV_DELIVERY)

    def boom(url, payload, api_key):
        raise AssertionError("LLM should not be called when regex already found refs")

    monkeypatch.setattr(llm_service, "llm_post", boom)
    monkeypatch.setattr(llm_service, "_llm_key", lambda: "gw-key")
    from types import SimpleNamespace
    monkeypatch.setattr(
        llm_service, "get_settings",
        lambda: SimpleNamespace(llm_model="openai/gpt-4.1-mini", llm_api_key="",
                                vercel_gateway_key="gw"),
    )
    monkeypatch.setattr(
        jev_service, "get_settings",
        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                jev_api_key="", jev_model="", llm_model="openai/gpt-4.1-mini"),
    )

    with TestClient(app) as c:
        r = c.post("/v1/accounts", headers=HEADERS,
                   json={"address": "llm-b@163.com", "auth_code": "A", "verify": False})
        aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
        c.post(f"/v1/accounts/{aid}/sync", headers=HEADERS, json={})
        items = c.get(f"/v1/accounts/{aid}/messages", headers=HEADERS,
                      params={"folder": "INBOX"}).json()
        uid2 = next(m for m in items if m["uid"] == 2)  # body has real URLs -> regex hits
        c.get(f"/v1/messages/{uid2['id']}", headers=HEADERS)
        r = c.post(f"/v1/messages/{uid2['id']}/judge", headers=HEADERS)
        assert r.status_code == 200
        refs = c.get(f"/v1/messages/{uid2['id']}/objects", headers=HEADERS).json()
        assert all(x["source"] == "regex" for x in refs)
        assert refs  # regex found them; LLM never consulted


def test_llm_fallback_skipped_without_config(monkeypatch):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: JEV_DELIVERY)

    def boom(url, payload, api_key):
        raise AssertionError("LLM disabled -> must not be called")

    monkeypatch.setattr(llm_service, "llm_post", boom)
    from types import SimpleNamespace
    monkeypatch.setattr(
        llm_service, "get_settings",
        lambda: SimpleNamespace(llm_model="", llm_api_key="", vercel_gateway_key="gw"),
    )
    monkeypatch.setattr(
        jev_service, "get_settings",
        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                jev_api_key="", jev_model="", llm_model=""),
    )

    with TestClient(app) as c:
        r = c.post("/v1/accounts", headers=HEADERS,
                   json={"address": "llm-c@163.com", "auth_code": "A", "verify": False})
        aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
        c.post(f"/v1/accounts/{aid}/sync", headers=H2 if (H2 := HEADERS) else HEADERS, json={})
        items = c.get(f"/v1/accounts/{aid}/messages", headers=HEADERS,
                      params={"folder": "INBOX"}).json()
        uid1 = next(m for m in items if m["uid"] == 1)
        c.get(f"/v1/messages/{uid1['id']}", headers=HEADERS)
        r = c.post(f"/v1/messages/{uid1['id']}/judge", headers=HEADERS)
        assert r.status_code == 200
        assert c.get(f"/v1/messages/{uid1['id']}/objects", headers=HEADERS).json() == []
