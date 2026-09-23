"""Bucket credential registry (stores) and confidence-gated automated pulls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Store
from ..schemas import PullRecordOut, PullRequest, PullResult, StoreCreate, StoreOut
from ..security import encrypt, require_api_key
from ..services import downloader

router = APIRouter(tags=["stores", "pulls"], dependencies=[Depends(require_api_key)])


@router.post("/v1/stores", response_model=StoreOut, status_code=201)
def create_store(body: StoreCreate) -> Store:
    if body.provider not in downloader.SERVICE_MAP:
        raise HTTPException(400, f"provider must be one of {list(downloader.SERVICE_MAP)}")
    with SessionLocal() as session:
        if session.scalar(select(Store).where(Store.name == body.name)):
            raise HTTPException(409, f"store {body.name!r} already exists")
        store = Store(
            name=body.name,
            provider=body.provider,
            bucket=body.bucket,
            region=body.region,
            endpoint=body.endpoint,
            access_key_enc=encrypt(body.access_key_id),
            secret_key_enc=encrypt(body.secret_access_key),
        )
        session.add(store)
        session.commit()
        session.refresh(store)
        return store


@router.get("/v1/stores", response_model=list[StoreOut])
def list_stores() -> list[Store]:
    with SessionLocal() as session:
        return session.scalars(select(Store).order_by(Store.id)).all()


@router.delete("/v1/stores/{store_id}", status_code=204)
def delete_store(store_id: int) -> None:
    with SessionLocal() as session:
        store = session.get(Store, store_id)
        if store is None:
            raise LookupError(f"store {store_id} not found")
        session.delete(store)
        session.commit()


@router.post("/v1/pipeline/run")
def run_pipeline(limit: int = 0) -> dict:
    """Trigger one pipeline round manually (fetch -> judge -> gated pull)."""
    from ..services.pipeline import process_pending

    return process_pending(limit=limit or None)


@router.get("/v1/messages/{message_id}/pulls", response_model=list[PullRecordOut])
def list_pull_records(message_id: int):
    """Per-file pull history for a message (progress + dedup audit)."""
    from ..models import PullRecord

    with SessionLocal() as session:
        return session.scalars(
            select(PullRecord)
            .where(PullRecord.message_id == message_id)
            .order_by(PullRecord.id)
        ).all()


@router.post("/v1/messages/{message_id}/pull", response_model=PullResult)
def pull_message_attachments(message_id: int, body: PullRequest) -> PullResult:
    """Download the storage refs of a delivery mail via registered bucket credentials.

    Jev gate: allowed when the message was judged category=delivery or
    storage_delivery >= 0.5; otherwise pass force=true explicitly.
    """
    result = downloader.pull_message(
        message_id,
        ref_id=body.ref_id,
        recursive=body.recursive,
        max_files=body.max_files,
        force=body.force,
    )
    return PullResult(**result)
