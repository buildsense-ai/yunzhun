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

from ..config import get_settings
from ..models import ObjectRef, Store
from ..security import decrypt

# provider -> OpenDAL service name
SERVICE_MAP = {
    "aliyun-oss": "oss",
    "huawei-obs": "obs",
    "tencent-cos": "cos",
    "aws-s3": "s3",
    "s3-compatible": "s3",
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


def _walk_prefix(op, prefix: str, max_files: int) -> list[str]:
    stack = [prefix if prefix.endswith("/") else prefix + "/"]
    files: list[str] = []
    while stack and len(files) < max_files:
        current = stack.pop()
        for entry in op.list(current):
            name = entry.path
            if name == current:
                continue
            if name.endswith("/"):
                stack.append(name)
            else:
                files.append(name)
    return files[:max_files]


def pull_ref(store: Store, ref: ObjectRef, dest: Path, recursive: bool, max_files: int) -> list[PulledFile]:
    op = _operator(store)
    pulled: list[PulledFile] = []

    keys: list[str]
    if recursive:
        keys = _walk_prefix(op, ref.key, max_files)
        if not keys:
            keys = [ref.key]  # maybe it's a plain object after all
    else:
        keys = [ref.key]

    for key in keys:
        target = _safe_local_path(dest, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = bytes(op.read(key))
        target.write_bytes(data)
        pulled.append(PulledFile(path=key, local=str(target), size=len(data)))
    return pulled


def find_store(session: SASession, provider: str, bucket: str) -> Store | None:
    return session.scalar(
        select(Store).where(
            Store.provider == provider, Store.bucket == bucket, Store.enabled == True  # noqa: E712
        )
    )


def download_dir() -> Path:
    d = Path(get_settings().download_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d
