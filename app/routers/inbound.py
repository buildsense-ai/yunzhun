"""Inbound webhook: external sources push raw RFC822 mail straight into the pipeline.

Accepts either `message/rfc822` (or any non-JSON) body with the raw message,
or application/json {"raw_base64": "..."}. Designed for Cloudflare Email
Workers, forwarding bridges, and scripts.
"""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import ObjectRef
from ..security import require_api_key
from ..services import mailbox

router = APIRouter(tags=["inbound"], dependencies=[Depends(require_api_key)])

_MAX_INBOUND = 50 * 1024 * 1024  # 50 MB


@router.post("/v1/inbound", status_code=201)
async def inbound_message(request: Request, background: BackgroundTasks) -> dict:
    ctype = request.headers.get("content-type", "")
    if "json" in ctype:
        try:
            payload = await request.json()
            raw = base64.b64decode(payload.get("raw_base64", ""), validate=True)
        except (binascii.Error, ValueError, AttributeError) as e:
            raise HTTPException(400, f"invalid JSON payload: {e}") from e
    else:
        raw = await request.body()
    if not raw:
        raise HTTPException(400, "empty message body")
    if len(raw) > _MAX_INBOUND:
        raise HTTPException(413, f"message exceeds {_MAX_INBOUND} byte cap")

    msg = mailbox.ingest_raw_message(raw)

    with SessionLocal() as session:
        refs = session.scalar(
            select(func.count(ObjectRef.id)).where(ObjectRef.message_id == msg.id)
        ) or 0

    # kick one pipeline round so the mail is judged/pulled near-instantly
    def _kick():
        from ..services.pipeline import process_pending

        process_pending()

    background.add_task(_kick)
    return {
        "message_id": msg.id,
        "subject": msg.subject,
        "refs": refs,
    }
