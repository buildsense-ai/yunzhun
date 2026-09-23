"""Jev (TypeSafe System One) semantic judgment layer.

One batched call per message asks parallel atomic questions:
  - category          (Choice): delivery | billing | security | notification | personal | other
  - storage_delivery  (Noul):   does it contain file/data storage delivery info?
  - action_required   (Score):  0 no action .. 2 needs prompt action

The deterministic regex extractor (storagelinks.py) stays the source of truth
for *what* the storage addresses are; Jev adds the semantic *so-what*.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import SessionLocal
from ..models import Judgment, Message

API_URL = "https://api.typesafe.ai/v1/systemone"
_TIMEOUT = 20.0
_STATE_CHAR_LIMIT = 4000

CATEGORIES: dict[str, str] = {
    "delivery": "数据/文件交付（测序数据、报表、样品数据等，含下载地址或交付位置）",
    "billing": "账单、账务、支付、扣款",
    "security": "安全告警（异地登录、OAuth 授权变更、密码修改等）",
    "notification": "系统/平台通知（不含安全事件）",
    "personal": "个人往来邮件",
    "other": "以上都不是",
}

ACTION_LEVELS = ["无需任何行动", "可稍后处理（参阅/归档即可）", "需要尽快关注或处理"]


class JevUnavailable(HTTPException):
    def __init__(self, detail: str):
        super().__init__(503, detail)


def _api_key() -> str:
    return get_settings().jev_api_key or os.environ.get("TYPESAFE_API_KEY", "")


def _post(payload: dict, api_key: str) -> dict:
    resp = httpx.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise HTTPException(502, f"Jev API error HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _build_state(msg: Message, refs: list[str]) -> str:
    body = (msg.text_body or "")[: _STATE_CHAR_LIMIT // 2]
    parts = [
        f"主题: {msg.subject}",
        f"发件人: {', '.join(a['email'] for a in (msg.from_addr or []) if a.get('email'))}",
    ]
    if refs:
        parts.append("已识别的存储地址: " + "; ".join(refs))
    parts.append(f"正文:\n{body}")
    return "\n".join(parts)[:_STATE_CHAR_LIMIT]


def judge_message(message_id: int) -> Judgment:
    settings = get_settings()
    api_key = _api_key()
    if not api_key:
        raise JevUnavailable(
            "Jev is not configured: set YUNZHUN_JEV_API_KEY (or TYPESAFE_API_KEY)"
        )

    with SessionLocal() as session:
        msg = session.get(Message, message_id, options=(selectinload(Message.object_refs),))
        if msg is None:
            raise LookupError(f"message {message_id} not found")
        if not msg.body_fetched:
            raise HTTPException(409, "message body not fetched yet; GET the message first")
        refs = [
            f"{r.provider}://{r.bucket}/{r.key}" for r in msg.object_refs
        ]
        state = _build_state(msg, refs)

    payload = {
        "model": settings.jev_model,
        "state": state,
        "questions": {
            "category": {
                "type": "choice",
                "instructions": "这封邮件属于哪一类？",
                "criteria": CATEGORIES,
            },
            "storage_delivery": {
                "type": "noul",
                "instructions": "这封邮件包含数据或文件的存储交付信息（下载地址、交付位置、网盘/OSS链接等）",
            },
            "action_required": {
                "type": "score",
                "instructions": "收件人需要采取行动的紧迫程度",
                "criteria": ACTION_LEVELS,
            },
        },
    }
    data = _post(payload, api_key)
    answers: dict[str, Any] = data.get("answers", {})

    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        category = answers.get("category", {})
        noul = answers.get("storage_delivery", {})
        score = answers.get("action_required", {})
        judgment = Judgment(
            message_id=message_id,
            category=category.get("choice", "other"),
            category_confidence=float(category.get("confidence") or 0.0),
            storage_delivery=float(noul.get("noul") or 0.0),
            action_required=int(round(float(score.get("score") or 0.0))),
            model=data.get("model", settings.jev_model),
            raw=data,
        )
        msg.judgment = judgment
        session.commit()
        return session.scalars(
            select(Judgment).where(Judgment.message_id == message_id)
        ).one()
