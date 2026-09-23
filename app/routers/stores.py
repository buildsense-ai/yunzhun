"""Bucket credential registry (stores) and confidence-gated automated pulls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Judgment, Message, ObjectRef, Store
from ..schemas import PullRequest, PullResult, StoreCreate, StoreOut
from ..security import encrypt, require_api_key
from ..services import downloader
from ..services.downloader import StoreNotFound, download_dir, pull_ref

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


@router.post("/v1/messages/{message_id}/pull", response_model=PullResult)
def pull_message_attachments(message_id: int, body: PullRequest) -> PullResult:
    """Download the storage refs of a delivery mail via registered bucket credentials.

    Jev gate: allowed when the message was judged category=delivery or
    storage_delivery >= 0.5; otherwise pass force=true explicitly.
    """
    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg is None:
            raise LookupError(f"message {message_id} not found")

        judgment = session.scalar(select(Judgment).where(Judgment.message_id == message_id))
        gate = {
            "category": judgment.category if judgment else None,
            "storage_delivery": judgment.storage_delivery if judgment else None,
            "passed": False,
            "forced": body.force,
        }
        if not body.force:
            if judgment is None:
                gate["reason"] = "no judgment yet; run /judge first or pass force=true"
            elif judgment.category == "delivery" or judgment.storage_delivery >= 0.5:
                gate["passed"] = True
            else:
                gate["reason"] = (
                    f"judged {judgment.category!r} with storage_delivery="
                    f"{judgment.storage_delivery:.2f}; pass force=true to override"
                )
        else:
            gate["passed"] = True

        if not gate["passed"]:
            raise HTTPException(403, {"gate": gate})

        stmt = select(ObjectRef).where(ObjectRef.message_id == message_id)
        if body.ref_id:
            stmt = stmt.where(ObjectRef.id == body.ref_id)
        refs = session.scalars(stmt.order_by(ObjectRef.id)).all()
        if not refs:
            raise LookupError(f"no storage refs for message {message_id}")

        stores: dict[tuple[str, str], Store] = {}
        skipped: list[dict] = []
        downloaded: list[str] = []
        dest = download_dir() / f"msg-{message_id}"

        for ref in refs:
            key = (ref.provider, ref.bucket)
            store = stores.get(key)
            if store is None:
                store = downloader.find_store(session, ref.provider, ref.bucket)
                stores[key] = store
            if store is None:
                skipped.append(
                    {"provider": ref.provider, "bucket": ref.bucket,
                     "reason": "no registered store with credentials for this bucket"}
                )
                continue
            try:
                for f in pull_ref(store, ref, dest, body.recursive, body.max_files):
                    downloaded.append(f.local)
            except StoreNotFound as e:  # endpoint/region misconfig
                skipped.append({"provider": ref.provider, "bucket": ref.bucket, "reason": e.detail})
        return PullResult(message_id=message_id, downloaded=downloaded, skipped=skipped, gate=gate)
