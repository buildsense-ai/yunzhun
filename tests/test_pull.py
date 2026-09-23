"""Store registry + confidence-gated pull tests (fake OpenDAL operator)."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import downloader
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}


class FakeEntry:
    def __init__(self, path):
        self.path = path


class FakeOperator:
    """Minimal OpenDAL surface: objects under 'data/' prefix + one plain file."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        assert kwargs["bucket"] in ("novo-china-region", "bktdir")

    def stat(self, path):
        raise FileNotFoundError(path)

    def list(self, prefix):
        if prefix == "X101SC26023844-Z01/X101SC26023844-Z01-J039/":
            return iter([FakeEntry(prefix + "QC/"), FakeEntry(prefix + "raw.fq.gz")])
        if prefix.endswith("QC/"):
            return iter([FakeEntry(prefix + "qc.pdf")])
        return iter([])

    def read(self, path):
        return iter([]) if False else iter(b"CONTENT:" + path.encode())


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(downloader, "_operator", lambda store: FakeOperator(**{"bucket": store.bucket}))
    monkeypatch.setattr(downloader, "download_dir", lambda: tmp_path / "dl")
    with TestClient(app) as c:
        yield c


def _setup_account_with_delivery(client):
    r = client.post(
        "/v1/accounts",
        headers=HEADERS,
        json={"address": "pull-test@163.com", "auth_code": "AUTH", "verify": False},
    )
    if r.status_code == 409:  # persisted from an earlier test in the shared DB
        account_id = int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
    else:
        account_id = r.json()["id"]
    client.post(f"/v1/accounts/{account_id}/sync", headers=HEADERS, json={})
    items = client.get(
        f"/v1/accounts/{account_id}/messages", headers=HEADERS, params={"folder": "INBOX"}
    ).json()
    msg2_id = items[1]["id"]
    client.get(f"/v1/messages/{msg2_id}", headers=HEADERS)  # fetch body -> object refs
    client.patch(
        f"/v1/messages/{msg2_id}/flags", headers=HEADERS, json={"add": [], "remove": []}
    )
    return account_id, msg2_id


def test_store_crud_masks_keys(client):
    r = client.post(
        "/v1/stores",
        headers=HEADERS,
        json={
            "name": "novo-oss", "provider": "aliyun-oss", "bucket": "novo-china-region",
            "region": "cn-hangzhou", "access_key_id": "AK-TEST", "secret_access_key": "SK-TEST",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["bucket"] == "novo-china-region"
    assert "access_key" not in body and "secret" not in body  # never echoed

    listed = client.get("/v1/stores", headers=HEADERS).json()
    assert any(s["name"] == "novo-oss" for s in listed)
    assert all("access_key" not in s and "secret" not in s for s in listed)  # never echoed


def test_pull_requires_registered_store(client):
    _, msg2_id = _setup_account_with_delivery(client)
    # no Jev judgment + no store: gate blocks first
    r = client.post(f"/v1/messages/{msg2_id}/pull", headers=HEADERS, json={})
    assert r.status_code == 403
    assert r.json()["detail"]["gate"]["reason"].startswith("no judgment")


def test_pull_gate_and_download(client, monkeypatch):
    import json as _json

    account_id, msg2_id = _setup_account_with_delivery(client)

    # shared DB: clear any leftover store for this bucket so the first pull
    # below actually hits the "no registered store" branch
    from app.db import SessionLocal
    from app.models import Store
    from sqlalchemy import delete as sql_delete
    with SessionLocal() as s:
        s.execute(sql_delete(Store).where(Store.bucket == "bktdir"))
        s.commit()

    # register a passing judgment (category=delivery) via fake jev upstream
    from app.services import jev as jev_service

    monkeypatch.setattr(
        jev_service, "_post",
        lambda url, payload, key: {
            "model": "typesafe-ai/jev",
            "answers": {
                "category": {"type": "choice", "choice": "delivery", "confidence": 0.99,
                             "probabilities": {}},
                "storage_delivery": {"type": "noul", "noul": 0.97},
                "action_required": {"type": "score", "score": 1.0, "confidence": 1.0},
            },
        },
    )
    monkeypatch.setattr(jev_service, "resolve_provider",
                        lambda: ("http://x", "m", "k"))
    r = client.post(f"/v1/messages/{msg2_id}/judge", headers=HEADERS)
    assert r.status_code == 200

    # gate passes now, but no store registered -> skipped with reason
    r = client.post(f"/v1/messages/{msg2_id}/pull", headers=HEADERS, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["gate"]["passed"] is True
    assert body["skipped"] and "no registered store" in body["skipped"][0]["reason"]

    # register credentials for the aliyun-oss bucket only -> per-ref store matching:
    # the oss ref downloads, the tencent-cos ref is skipped with a reason
    client.post(
        "/v1/stores",
        headers=HEADERS,
        json={
            "name": "bktdir-oss-gate", "provider": "aliyun-oss", "bucket": "bktdir",
            "region": "cn-hangzhou", "access_key_id": "AK-TEST", "secret_access_key": "SK-TEST",
        },
    )
    r = client.post(f"/v1/messages/{msg2_id}/pull", headers=HEADERS,
                    json={"ref_id": None, "recursive": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["provider"] == "tencent-cos"
    assert len(body["downloaded"]) >= 1
    for path in body["downloaded"]:
        p = _json.loads(_json.dumps(path))
        content = open(p, "rb").read()
        assert content.startswith(b"CONTENT:")
        assert "msg-" in p  # scoped under the message download dir


def test_pull_gate_force_bypass_and_403_detail(client):
    account_id, msg2_id = _setup_account_with_delivery(client)
    # unjudged message: force=true passes the gate even without judgment
    r = client.post(f"/v1/messages/{msg2_id}/pull", headers=HEADERS,
                    json={"force": True, "recursive": False})
    assert r.status_code == 200
    assert r.json()["gate"]["forced"] is True
