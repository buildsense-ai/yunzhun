"""Integrity checks: md5 extraction from mail text + post-download verification."""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.mail.checksums import extract_checksums
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


def test_extract_checksums_forms():
    text = (
        "数据交付清单：\n"
        "d41d8cd98f00b204e9800998ecf8427e  sample_R1.fastq.gz\n"
        "sample_R2.fastq.gz  md5: 098f6bcd4621d373cade4e832627b4f6\n"
        "md5(report.tar) = 5d41402abc4b2a76b9719d911017c592\n"
    )
    m = extract_checksums(text)
    assert m["sample_R1.fastq.gz"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert m["sample_R2.fastq.gz"] == "098f6bcd4621d373cade4e832627b4f6"
    assert m["report.tar"] == "5d41402abc4b2a76b9719d911017c592"


class FakeOperator:
    def __init__(self, **kwargs):
        pass

    def list(self, prefix):
        return iter([])

    def stat(self, path):
        raise FileNotFoundError(path)

    def read(self, path):
        return iter(b"hello world")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(downloader, "_operator", lambda store: FakeOperator())
    monkeypatch.setattr(downloader, "download_dir", lambda: tmp_path / "dl")
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))
    monkeypatch.setattr(jev_service, "_post", lambda url, payload, key: JEV_DELIVERY)
    from types import SimpleNamespace
    monkeypatch.setattr(jev_service, "get_settings",
                        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                                jev_api_key="", jev_model="", llm_model=""))
    with TestClient(app) as c:
        yield c


def _setup(client, expected_md5):
    """Message uid=2 has the bktdir oss ref; we set its expected_md5 directly."""
    r = client.post("/v1/accounts", headers=HEADERS,
                    json={"address": "md5@163.com", "auth_code": "A", "verify": False})
    aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))

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
    client.get(f"/v1/messages/{mid}", headers=HEADERS)

    with SessionLocal() as s:
        s.execute(sql_delete(PullRecord).where(PullRecord.message_id == mid))
        from app.models import ObjectRef
        ref = s.query(ObjectRef).filter_by(message_id=mid, provider="aliyun-oss").one()
        ref.expected_md5 = expected_md5
        s.commit()

    client.post(f"/v1/messages/{mid}/judge", headers=HEADERS)
    client.post("/v1/stores", headers=HEADERS,
                json={"name": "md5-oss", "provider": "aliyun-oss", "bucket": "bktdir",
                      "region": "cn-hangzhou", "access_key_id": "AK", "secret_access_key": "SK"})
    return mid


def test_pull_md5_match(client):
    good = hashlib.md5(b"hello world").hexdigest()
    mid = _setup(client, good)
    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert len(r.json()["downloaded"]) == 1
    rec = client.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
    assert rec[0]["status"] == "done"


def test_pull_md5_mismatch_marked_failed(client):
    mid = _setup(client, "0" * 32)  # wrong md5
    r = client.post(f"/v1/messages/{mid}/pull", headers=HEADERS, json={})
    assert r.json()["downloaded"] == []
    assert any("md5 mismatch" in s["reason"] for s in r.json()["skipped"])
    rec = client.get(f"/v1/messages/{mid}/pulls", headers=HEADERS).json()
    assert rec[0]["status"] == "failed"
    assert "md5 mismatch" in rec[0]["error"]
