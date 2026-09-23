"""Pull records: progress listing, DB-level dedup, failure records."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import downloader, jev as jev_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}

JEV_DELIVERY = {
    "model": "typesafe-ai/jev",
    "answers": {
        "category": {"type": "choice", "choice": "delivery", "confidence": 0.99,
                     "probabilities": {}},
        "storage_delivery": {"type": "noul", "noul": 0.97},
        "action_required": {"type": "score", "score": 1.0, "confidence": 1.0},
    },
}


class FakeOperator:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def list(self, prefix):
        return iter([])

    def read(self, path):
        if "bad" in path:
            raise RuntimeError("simulated read failure")
        return iter(b"CONTENT:" + path.encode())


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(downloader, "_operator", lambda store: FakeOperator(bucket=store.bucket))
    monkeypatch.setattr(downloader, "download_dir", lambda: tmp_path / "dl")
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: JEV_DELIVERY)
    from types import SimpleNamespace
    monkeypatch.setattr(jev_service, "get_settings",
                        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                                jev_api_key="", jev_model="", llm_model=""))
    with TestClient(app) as c:
        yield c


def _setup(client):
    r = client.post("/v1/accounts", headers=HEADERS,
                    json={"address": "rec@163.com", "auth_code": "A", "verify": False})
    aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))

    # clear leftover bktdir stores BEFORE sync: the endpoint's event-driven
    # kick would otherwise pull the file before this test's own pull
    from app.db import SessionLocal
    from app.models import PullRecord, Store
    from sqlalchemy import delete as sql_delete
    with SessionLocal() as s:
        s.execute(sql_delete(Store).where(Store.bucket == "bktdir"))
        s.commit()

    client.post(f"/v1/accounts/{aid}/sync", headers=HEADERS, json={})
    items = client.get(f"/v1/accounts/{aid}/messages", headers=HEADERS,
                       params={"folder": "INBOX"}).json()
    mid = next(m for m in items if m["uid"] == 2)["id"]
    with SessionLocal() as s:
        s.execute(sql_delete(PullRecord).where(PullRecord.message_id == mid))
        s.commit()
    client.get(f"/v1/messages/{mid}", headers=HEADERS)
    client.post(f"/v1/messages/{mid}/judge", headers=HEADERS)
    client.post("/v1/stores", headers=HEADERS,
                json={"name": "rec-oss", "provider": "aliyun-oss", "bucket": "bktdir",
                      "region": "cn-hangzhou", "access_key_id": "AK", "secret_access_key": "SK"})
    return aid, mid


def test_pull_records_written_and_dedup(client):
    _, mid = _setup(client)

    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert r.status_code == 200
    assert len(r.json()["downloaded"]) == 1

    # progress endpoint lists the record
    records = client.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
    assert len(records) == 1
    rec = records[0]
    assert rec["status"] == "done"
    assert rec["remote_key"] == "db/dump.csv"
    assert rec["provider"] == "aliyun-oss"
    assert rec["size"] > 0

    # second pull: DB dedup kicks in (file still on disk) -> nothing new
    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert r.json()["downloaded"] == []
    records2 = client.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
    assert len(records2) == 1  # no duplicate rows

    # delete the local file -> record self-heals, re-pull downloads again
    import os
    os.remove(rec["local_path"])
    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert len(r.json()["downloaded"]) == 1


def test_pull_failure_recorded(client, monkeypatch, tmp_path):
    # operator that fails on every read
    class BadOp(FakeOperator):
        def read(self, path):
            raise RuntimeError("simulated read failure")

    monkeypatch.setattr(downloader, "_operator", lambda store: BadOp(bucket=store.bucket))
    _, mid = _setup(client)

    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["downloaded"] == []
    assert any("simulated read failure" in s["reason"] for s in body["skipped"])

    records = client.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert "simulated read failure" in records[0]["error"]
