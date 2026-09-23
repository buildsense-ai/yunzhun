"""End-to-end pipeline test: sync -> fetch -> judge -> gated pull, all fakes."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import downloader, jev as jev_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}


class FakeEntry:
    def __init__(self, path):
        self.path = path


class FakeOperator:
    def __init__(self, **kwargs):
        assert kwargs["bucket"] == "bktdir"

    def stat(self, path):
        raise FileNotFoundError(path)

    def list(self, prefix):
        return iter([])

    def read(self, path):
        return iter(b"CONTENT:" + path.encode())


def test_pipeline_runs_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(downloader, "_operator", lambda store: FakeOperator(**{"bucket": store.bucket}))
    monkeypatch.setattr(downloader, "download_dir", lambda: tmp_path / "dl")
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: {
        "model": "typesafe-ai/jev",
        "answers": {
            "category": {"type": "choice", "choice": "delivery", "confidence": 0.99,
                         "probabilities": {}},
            "storage_delivery": {"type": "noul", "noul": 0.97},
            "action_required": {"type": "score", "score": 1.0, "confidence": 1.0},
        },
    })
    from types import SimpleNamespace
    monkeypatch.setattr(jev_service, "get_settings",
                        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                                jev_api_key="", jev_model="", llm_model=""))

    with TestClient(app) as c:
        r = c.post("/v1/accounts", headers=HEADERS,
                   json={"address": "pipe@163.com", "auth_code": "A", "verify": False})
        aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))

        # register credentials BEFORE sync: the sync endpoint's event-driven
        # kick runs fetch -> judge -> pull in the background task
        c.post("/v1/stores", headers=HEADERS,
               json={"name": "pipe-oss", "provider": "aliyun-oss", "bucket": "bktdir",
                     "region": "cn-hangzhou", "access_key_id": "AK", "secret_access_key": "SK"})
        c.post(f"/v1/accounts/{aid}/sync", headers=HEADERS, json={})

        # end state: uid-2 delivery mail was auto-downloaded during the sync kick
        pulled = list((tmp_path / "dl").rglob("dump.csv"))
        assert pulled and pulled[0].read_bytes().startswith(b"CONTENT:")

        # pull record persisted
        items = c.get(f"/v1/accounts/{aid}/messages", headers=HEADERS,
                      params={"folder": "INBOX"}).json()
        mid = next(m for m in items if m["uid"] == 2)["id"]
        records = c.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
        assert any(r["status"] == "done" for r in records)

        # explicit round: nothing pending anymore
        from app.services.pipeline import process_pending
        stats2 = process_pending()
        assert stats2["pending"] == 0

        # manual trigger via API works too
        r = c.post("/v1/pipeline/run", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["pending"] == 0


def test_pipeline_noop_without_jev_key(monkeypatch):
    from app.services.pipeline import process_pending

    def raise_unavailable():
        raise jev_service.JevUnavailable("not configured")

    monkeypatch.setattr(jev_service, "resolve_provider", raise_unavailable)
    stats = process_pending()
    assert stats["reason"] == "not configured"
    assert stats["judged"] == 0
