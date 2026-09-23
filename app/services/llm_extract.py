"""LLM fallback extraction: the last link in the find-addresses chain.

Pipeline (intent routing):
  1. Jev judges the mail (batched triage questions, incl. storage_delivery noul)
  2. deterministic regex extractor (storagelinks.py) pulls scheme/URL forms
  3. ONLY when Jev flags delivery-ish AND regex found nothing, an LLM reads the
     full text and normalizes prose-form addresses ("bucket: x, 前缀: y") into
     proper URIs. Every LLM candidate must still pass storagelinks.classify(),
     so stored refs keep the same validation guarantees.
"""
from __future__ import annotations

import json
import os
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession, selectinload

from ..config import get_settings
from ..db import SessionLocal
from ..mail.storagelinks import classify
from ..models import Judgment, Message, ObjectRef

GATEWAY_CHAT_URL = "https://ai-gateway.vercel.app/v1/chat/completions"
_TIMEOUT = 30.0

_SYSTEM_PROMPT = (
    "You extract file-storage addresses from email text. Respond with JSON only: "
    '{"addresses": ["<full address>"]}. '
    "Normalize prose-form storage descriptions (e.g. 'bucket: foo, prefix: bar/x') into "
    "proper URIs (oss://bucket/key, obs://bucket/key, cos://bucket/key, s3://bucket/key, "
    "or https:// URLs). Only include real storage/download addresses; never ordinary web links."
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def _llm_key() -> str:
    s = get_settings()
    return s.llm_api_key or s.vercel_gateway_key or os.environ.get(
        "AI_GATEWAY_API_KEY", os.environ.get("VERCEL_AI_GATEWAY_API_KEY", "")
    )


def llm_post(url: str, payload: dict, api_key: str) -> dict:  # exposed for tests
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"LLM gateway HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _candidate_addresses(text: str, model: str, api_key: str) -> list[str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text[:6000]},
        ],
        "response_format": {"type": "json_object"},
    }
    data = llm_post(GATEWAY_CHAT_URL, payload, api_key)
    content = data["choices"][0]["message"]["content"]
    match = _JSON_BLOCK_RE.search(content)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    addresses = parsed.get("addresses", [])
    return [a.strip() for a in addresses if isinstance(a, str) and a.strip()] if isinstance(addresses, list) else []


def llm_fallback_extract(message_id: int) -> list[ObjectRef]:
    """Run the LLM fallback for a message and persist validated refs (source=llm).

    Called after judging when storage_delivery >= 0.5 and regex found nothing.
    Raises nothing on LLM failure (best-effort by design); returns new refs.
    """
    settings = get_settings()
    api_key = _llm_key()
    if not settings.llm_model or not api_key:
        return []

    with SessionLocal() as session:
        msg = session.get(
            Message, message_id, options=(selectinload(Message.object_refs),)
        )
        if msg is None or not msg.body_fetched:
            return []

    text = " ".join(filter(None, [msg.subject, msg.text_body or "", msg.html_body or ""]))
    try:
        candidates = _candidate_addresses(text, settings.llm_model, api_key)
    except Exception:  # noqa: BLE001 — fallback is best-effort
        return []

    new_refs: list[ObjectRef] = []
    with SessionLocal() as session:
        msg = session.get(
            Message, message_id, options=(selectinload(Message.object_refs),)
        )
        existing_urls = {r.url for r in msg.object_refs}
        for address in candidates:
            ref = classify(address)
            if ref is None or ref.url in existing_urls:
                continue
            row = ObjectRef(
                account_id=msg.account_id,
                provider=ref.provider,
                bucket=ref.bucket,
                key=ref.key,
                region=ref.region,
                url=ref.url,
                presigned=ref.presigned,
                source="llm",
            )
            msg.object_refs.append(row)
            new_refs.append(row)
        if new_refs:
            session.commit()
            for row in new_refs:
                session.refresh(row)
    return new_refs


def maybe_run_fallback(session: SASession, message_id: int) -> bool:
    """True when the message qualifies for (and needs) LLM fallback."""
    judgment = session.scalar(select(Judgment).where(Judgment.message_id == message_id))
    if judgment is None or judgment.storage_delivery < 0.5:
        return False
    refs = session.scalars(select(ObjectRef).where(ObjectRef.message_id == message_id)).all()
    return len(refs) == 0
