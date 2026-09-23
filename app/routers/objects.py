"""Object-storage reference endpoints: list, scan, account-wide search, safe fetch."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..mail.storagelinks import classify
from ..models import Account, Message, ObjectRef
from ..schemas import ObjectFetchRequest, StorageRefOut
from ..security import require_api_key
from ..services.mailbox import scan_message_objects

router = APIRouter(tags=["object-refs"], dependencies=[Depends(require_api_key)])

_FETCH_TIMEOUT = 30.0
_MAX_FETCH_BYTES = 100 * 1024 * 1024  # 100 MB safety cap


@router.get("/v1/messages/{message_id}/objects", response_model=list[StorageRefOut])
def list_message_objects(message_id: int) -> list[ObjectRef]:
    with SessionLocal() as session:
        if session.get(Message, message_id) is None:
            raise LookupError(f"message {message_id} not found")
        return session.scalars(
            select(ObjectRef).where(ObjectRef.message_id == message_id).order_by(ObjectRef.id)
        ).all()


@router.post("/v1/messages/{message_id}/objects/scan", response_model=list[StorageRefOut])
def rescan_message_objects(message_id: int) -> list[ObjectRef]:
    """(Re-)extract object refs from the cached body (idempotent)."""
    return scan_message_objects(message_id)


@router.get("/v1/accounts/{account_id}/objects", response_model=list[StorageRefOut])
def list_account_objects(
    account_id: int,
    provider: str | None = Query(
        default=None,
        description="aliyun-oss | tencent-cos | huawei-obs | aws-s3 | gcs | azure-blob | s3-compatible",
    ),
    bucket: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ObjectRef]:
    with SessionLocal() as session:
        if session.get(Account, account_id) is None:
            raise LookupError(f"account {account_id} not found")
        stmt = (
            select(ObjectRef)
            .join(Message, ObjectRef.message_id == Message.id)
            .where(ObjectRef.account_id == account_id)
            .options(selectinload(ObjectRef.message))
        )
        if provider:
            stmt = stmt.where(ObjectRef.provider == provider)
        if bucket:
            stmt = stmt.where(ObjectRef.bucket == bucket)
        return session.scalars(
            stmt.order_by(Message.internal_date.desc().nulls_last(), ObjectRef.id.desc()).limit(limit)
        ).all()


@router.post("/v1/objects/fetch")
def fetch_object(body: ObjectFetchRequest) -> StreamingResponse:
    """Download a public/presigned object URL previously classified by the gateway.

    SSRF guard: only hosts recognized as known storage providers are fetched.
    For private buckets, use the OpenDAL integration (see README) with credentials.
    """
    if classify(body.url) is None:
        raise HTTPException(400, "URL is not a recognized object-storage reference")
    if not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            400,
            f"{body.url.split(':', 1)[0]}:// requires credentials; use the OpenDAL integration (see README)",
        )
    resp = httpx.request("GET", body.url, timeout=_FETCH_TIMEOUT, follow_redirects=True)
    if resp.status_code >= 400:
        raise HTTPException(502, f"upstream fetch failed: HTTP {resp.status_code}")
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > _MAX_FETCH_BYTES:
        raise HTTPException(413, f"object exceeds {_MAX_FETCH_BYTES} byte cap")
    if len(resp.content) > _MAX_FETCH_BYTES:
        raise HTTPException(413, f"object exceeds {_MAX_FETCH_BYTES} byte cap")
    headers = {"Content-Type": resp.headers.get("Content-Type", "application/octet-stream")}
    if disposition := resp.headers.get("Content-Disposition"):
        headers["Content-Disposition"] = disposition
    return StreamingResponse(iter([resp.content]), headers=headers)
