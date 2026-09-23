"""Kanban board endpoint test."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mail import imap as imap_client
from app.services import downloader, jev as jev_service
from tests.fakes import FakeImap

HEADERS = {"X-API-Key": "test-key"}


class FakeOperator:
    def __init__(self, **kw):
        pass

    def list(self, p):
        return iter([])

    def stat(self, p):
        raise FileNotFoundError(p)

    def read(self, p):
        return iter(b"CONTENT:" + p.encode())


def test_board_columns_and_stages(monkeypatch, tmp_path):
    monkeypatch.setattr(imap_client, "connect", lambda acct: FakeImap())
    monkeypatch.setattr(downloader, "_operator", lambda store: FakeOperator())
    monkeypatch.setattr(downloader, "download_dir", lambda: tmp_path / "dl")
    monkeypatch.setattr(jev_service, "resolve_provider", lambda: ("http://x", "m", "k"))

    # mixed judgments: uid1 -> delivery, uid2 -> delivery, uid3 -> billing
    def fake_post(url, payload, key):
        subj = payload["state"].split("主题: ")[1].split("\n")[0]
        cat = "billing" if "发票" in subj or "Invoice" in subj else "delivery"
        return {
            "model": "m",
            "answers": {
                "category": {"type": "choice", "choice": cat, "confidence": 0.9,
                             "probabilities": {}},
                "storage_delivery": {"type": "noul",
                                     "noul": 0.9 if cat == "delivery" else 0.05},
                "action_required": {"type": "score", "score": 1.0, "confidence": 1.0},
            },
        }

    monkeypatch.setattr(jev_service, "_post", fake_post)
    from types import SimpleNamespace
    monkeypatch.setattr(jev_service, "get_settings",
                        lambda: SimpleNamespace(jev_provider="vercel", vercel_gateway_key="gw",
                                                jev_api_key="", jev_model="", llm_model=""))

    with TestClient(app) as c:
        r = c.post("/v1/accounts", headers=HEADERS,
                   json={"address": "board@163.com", "auth_code": "A", "verify": False})
        aid = r.json()["id"] if r.status_code == 201 else int(r.json()["detail"].rsplit("=", 1)[-1].rstrip(")"))
        c.post(f"/v1/accounts/{aid}/sync", headers=HEADERS, json={})

        from app.services.pipeline import process_pending
        process_pending()  # judge everything (delivery mails get pulled if store exists)

        r = c.get("/v1/board", headers=HEADERS)
        assert r.status_code == 200
        board = r.json()
        keys = [col["key"] for col in board["columns"]]
        assert keys == ["pending", "identified", "downloaded", "failed",
                        "other", "analysis", "archived"]
        # future stages exist but disabled
        assert board["columns"][5]["disabled"] is True

        # our account's msgs: uid2 (delivery w/ refs, no store -> identified),
        # uid1+uid3 also judged delivery by fake (no refs -> identified too,
        # since stage only checks delivery + no pulls)
        all_cards = [card for col in board["columns"] for card in col["cards"]]
        our = [c for c in all_cards if c["account_id"] == aid]
        assert our  # cards present
        msg2 = next(c for c in our if c["refs"])
        assert msg2["category"] == "delivery"
        assert msg2["refs"][0]["provider"] in (
            "aliyun-oss", "tencent-cos", "huawei-obs", "s3-compatible", "cloud-drive"
        )
