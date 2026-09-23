"""Automated pulls: object-storage downloads via Apache OpenDAL.

Confidence-gated: a message is auto-pullable when Jev judged it a delivery
(storage_delivery >= gate, or category == delivery). Lower-confidence or
unjudged messages need force=true (or a human look first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from ..db import SessionLocal
from ..models import ObjectRef, Store
from ..security import decrypt

# provider -> OpenDAL service name
SERVICE_MAP = {
    "aliyun-oss": "oss",
    "huawei-obs": "obs",
    "tencent-cos": "cos",
    "aws-s3": "s3",
    "s3-compatible": "s3",
    "azure-blob": "azblob",
}

_UNSAFE_PATH = re.compile(r"(\.\.|^/|[\x00\\])")


class StoreNotFound(HTTPException):
    pass


@dataclass
class PulledFile:
    path: str  # remote key
    local: str  # local file path
    size: int


def endpoint_for(store: Store) -> str:
    if store.endpoint:
        return store.endpoint
    if store.provider == "aliyun-oss" and store.region:
        return f"https://oss-{store.region}.aliyuncs.com"
    if store.provider == "huawei-obs" and store.region:
        return f"https://obs.{store.region}.myhuaweicloud.com"
    if store.provider == "tencent-cos" and store.region:
        return f"https://cos.{store.region}.myqcloud.com"
    if store.provider == "aws-s3" and store.region:
        return f"https://s3.{store.region}.amazonaws.com"
    raise StoreNotFound(
        400, f"store {store.name!r}: region (or explicit endpoint) is required for {store.provider}"
    )


def _operator(store: Store):
    try:
        import opendal
    except ImportError as e:  # pragma: no cover
        raise HTTPException(
            503, "opendal is not installed; run: pdm install --extra opendal"
        ) from e
    service = SERVICE_MAP.get(store.provider)
    if service is None:
        raise HTTPException(400, f"provider {store.provider!r} is not pullable")
    if store.provider == "azure-blob":
        # extractor stores bucket as "account/container"
        account, _, container = store.bucket.partition("/")
        return opendal.Operator(
            "azblob",
            root="/",
            container=container,
            account_name=account,
            account_key=decrypt(store.secret_key_enc),
            endpoint=store.endpoint or "",
        )
    kwargs: dict = {
        "root": "/",
        "bucket": store.bucket,
        "endpoint": endpoint_for(store),
        "access_key_id": decrypt(store.access_key_enc),
        "secret_access_key": decrypt(store.secret_key_enc),
    }
    if store.provider == "aws-s3" and store.region:
        kwargs["region"] = store.region
    return opendal.Operator(service, **kwargs)


def _safe_local_path(base: Path, key: str) -> Path:
    if _UNSAFE_PATH.search(key):
        raise HTTPException(400, f"unsafe object key: {key!r}")
    target = (base / key).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(400, f"key escapes download dir: {key!r}")
    return target


def _walk_prefix(op, prefix: str, max_files: int) -> tuple[list[str], bool]:
    """BFS a directory prefix. Returns (files, truncated)."""
    stack = [prefix if prefix.endswith("/") else prefix + "/"]
    files: list[str] = []
    truncated = False
    while stack:
        if len(files) >= max_files:
            truncated = True
            break
        current = stack.pop()
        for entry in op.list(current):
            name = entry.path
            if name == current:
                continue
            if name.endswith("/"):
                stack.append(name)
            else:
                files.append(name)
                if len(files) >= max_files:
                    truncated = True
                    break
        if truncated:
            break
    return files, truncated


def pull_ref(
    store: Store,
    ref: ObjectRef,
    dest: Path,
    recursive: bool,
    max_files: int,
    skip_keys: set[str] | None = None,
) -> tuple[list[PulledFile], bool, list[dict]]:
    """Pull one ref. Returns (pulled, truncated, failures).

    Atomic: every object lands via <name>.part -> rename, so a crash mid-write
    never counts as done. Per-key failures are collected, not raised.
    """
    op = _operator(store)
    pulled: list[PulledFile] = []
    failures: list[dict] = []

    keys: list[str]
    truncated = False
    if recursive:
        keys, truncated = _walk_prefix(op, ref.key, max_files)
        if not keys:
            keys = [ref.key]  # maybe it's a plain object after all
    else:
        keys = [ref.key]

    for key in keys:
        if skip_keys and key in skip_keys:
            continue
        try:
            target = _safe_local_path(dest, key)
        except HTTPException as e:
            failures.append({"key": key, "error": str(e.detail)})
            continue
        if target.exists() and target.stat().st_size > 0:
            continue  # already pulled (idempotent re-runs)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".part")
            data = bytes(op.read(key))
            tmp.write_bytes(data)
            tmp.replace(target)  # atomic commit
            pulled.append(PulledFile(path=key, local=str(target), size=len(data)))
        except Exception as e:  # noqa: BLE001 — per-key failure shouldn't abort the ref
            failures.append({"key": key, "error": str(e)})
    return pulled, truncated, failures


def find_store(session: SASession, provider: str, bucket: str) -> Store | None:
    return session.scalar(
        select(Store).where(
            Store.provider == provider, Store.bucket == bucket, Store.enabled == True  # noqa: E712
        )
    )


def download_dir() -> Path:
    from ..config import get_settings

    d = Path(get_settings().download_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def pull_message(
    message_id: int,
    ref_id: int | None = None,
    recursive: bool = True,
    max_files: int = 200,
    force: bool = False,
) -> dict:
    """Core pull flow shared by the API endpoint and the background pipeline."""
    from ..models import Judgment, Message, ObjectRef, PullRecord, utcnow

    with SessionLocal() as session:
        msg = session.get(Message, message_id)
        if msg is None:
            raise LookupError(f"message {message_id} not found")

        judgment = session.scalar(
            select(Judgment).where(Judgment.message_id == message_id)
        )
        gate = {
            "category": judgment.category if judgment else None,
            "storage_delivery": judgment.storage_delivery if judgment else None,
            "passed": False,
            "forced": force,
        }
        if not force:
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
            from fastapi import HTTPException

            raise HTTPException(403, {"gate": gate})

        stmt = select(ObjectRef).where(ObjectRef.message_id == message_id)
        if ref_id:
            stmt = stmt.where(ObjectRef.id == ref_id)
        refs = session.scalars(stmt.order_by(ObjectRef.id)).all()
        if not refs:
            raise LookupError(f"no storage refs for message {message_id}")

        # DB-level dedup: keys already pulled for this message (and still on disk)
        existing_records = {
            r.remote_key: r
            for r in session.scalars(
                select(PullRecord).where(PullRecord.message_id == message_id)
            ).all()
        }
        dest = download_dir() / f"msg-{message_id}"
        done_keys = {
            r.remote_key
            for r in existing_records.values()
            if r.status == "done" and Path(r.local_path).exists()  # self-heal if file deleted
        }

        stores: dict[tuple[str, str], Store] = {}
        skipped: list[dict] = []
        downloaded: list[str] = []

        for ref in refs:
            key = (ref.provider, ref.bucket)
            store = stores.get(key)
            if store is None:
                store = find_store(session, ref.provider, ref.bucket)
                stores[key] = store
            if store is None:
                skipped.append(
                    {"provider": ref.provider, "bucket": ref.bucket,
                     "reason": "no registered store with credentials for this bucket"}
                )
                continue
            try:
                pulled, truncated, failures = pull_ref(
                    store, ref, dest, recursive, max_files, skip_keys=done_keys
                )
            except StoreNotFound as e:
                skipped.append({"provider": ref.provider, "bucket": ref.bucket, "reason": e.detail})
                continue
            for f in pulled:
                downloaded.append(f.local)
                rec = existing_records.get(f.path)
                if rec is None:
                    rec = PullRecord(message_id=message_id, remote_key=f.path)
                    session.add(rec)
                rec.object_ref_id = ref.id
                rec.store_id = store.id
                rec.provider = ref.provider
                rec.bucket = ref.bucket
                rec.local_path = f.local
                rec.size = f.size
                rec.status = "done"
                rec.error = None
                rec.pulled_at = utcnow()
            for fail in failures:
                rec = existing_records.get(fail["key"])
                if rec is None:
                    rec = PullRecord(message_id=message_id, remote_key=fail["key"])
                    session.add(rec)
                rec.object_ref_id = ref.id
                rec.store_id = store.id
                rec.provider = ref.provider
                rec.bucket = ref.bucket
                rec.local_path = ""
                rec.size = 0
                rec.status = "failed"
                rec.error = fail["error"][:500]
                rec.pulled_at = utcnow()
                skipped.append(
                    {"provider": ref.provider, "bucket": ref.bucket,
                     "reason": f"key {fail['key']!r}: {fail['error'][:120]}"}
                )
            if truncated:
                skipped.append(
                    {"provider": ref.provider, "bucket": ref.bucket,
                     "reason": f"prefix listing truncated at {max_files} files"}
                )
        session.commit()
        return {
            "message_id": message_id,
            "downloaded": downloaded,
            "skipped": skipped,
            "gate": gate,
        }
