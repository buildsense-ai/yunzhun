"""Extract and classify object-storage references (OSS/OBS/COS/S3/GCS/Azure/...)
found in email text/html.

Extraction layer only — for credentialed access to the referenced buckets,
use the unified access layer (Apache OpenDAL python binding), which speaks
oss/obs/cos/s3 natively. See README "Object storage references".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

# Broad URL candidates: hrefs/src/text. Stops at quotes/whitespace/closing brackets.
URL_RE = re.compile(
    r"""(?:https?://|s3://)[^\s<>"'`)\]}]+""",
)
_TRAILING_PUNCT = ".,;:!?'\""

# Query params that mark a presigned / signed URL.
_PRESIGN_PARAMS = (
    "x-amz-signature", "x-amz-expires",        # AWS SigV4
    "signature", "ossaccesskeyid",             # AWS SigV2 / Aliyun OSS
    "q-sign-algorithm", "q-sign-time",         # Tencent COS
    "sig", "se=", "sp=",                       # Azure SAS (sig/se/sp)
)


@dataclass(frozen=True)
class StorageRef:
    provider: str  # aliyun-oss | tencent-cos | huawei-obs | aws-s3 | gcs | azure-blob | s3-compatible
    bucket: str
    key: str
    region: str | None
    url: str
    presigned: bool


def _is_presigned(query: str) -> bool:
    q = query.lower()
    return any(p in q for p in _PRESIGN_PARAMS)


def _first_path_segment(path: str) -> str:
    return unquote(path.lstrip("/").split("/", 1)[0])


def _rest_as_key(path: str) -> str:
    """For path-style URLs: everything after the first (bucket) segment."""
    parts = unquote(path.lstrip("/")).split("/", 1)
    return parts[1] if len(parts) > 1 else ""


def _path_key(path: str) -> str:
    """For virtual-hosted URLs: the full path is the object key."""
    return unquote(path.lstrip("/"))


def _oss_region(endpoint: str) -> str | None:
    m = re.match(r"oss(?:-dfile)?-([a-z0-9-]+?)(?:-internal)?$", endpoint)
    return m.group(1) if m else None


def classify(url: str) -> StorageRef | None:
    """Classify a single URL into a StorageRef, or None if not a known store."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme, host, path, query = (
        parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query,
    )
    if not host:
        return None
    presigned = _is_presigned(query)

    if scheme == "s3":
        bucket = host
        return StorageRef("s3-compatible", bucket, unquote(path.lstrip("/")), None, url, presigned)

    # --- virtual-hosted style: bucket in subdomain ---
    m = re.match(
        r"^(?P<bucket>[^>.]+)\.oss(?:-dfile)?-(?P<region>[a-z0-9-]+?)(?:-internal)?\.aliyuncs\.com$", host
    )
    if m:
        return StorageRef("aliyun-oss", m["bucket"], _path_key(path), m["region"], url, presigned)
    if host.endswith(".aliyuncs.com") and host.startswith("oss-"):  # path-style oss-cn-x.aliyuncs.com/bucket/key
        return StorageRef("aliyun-oss", _first_path_segment(path), _rest_as_key(path), _oss_region(host), url, presigned)

    m = re.match(r"^(?P<bucket>[^.]+)\.cos\.(?P<region>[^.]+)\.myqcloud\.com$", host)
    if m:
        return StorageRef("tencent-cos", m["bucket"], _path_key(path), m["region"], url, presigned)

    m = re.match(r"^(?P<bucket>[^.]+)\.obs\.(?P<region>[^.]+)\.myhuaweicloud\.com$", host)
    if m:
        return StorageRef("huawei-obs", m["bucket"], _path_key(path), m["region"], url, presigned)
    if re.match(r"^obs\.[^.]+\.myhuaweicloud\.com$", host):  # path-style OBS
        return StorageRef("huawei-obs", _first_path_segment(path), _rest_as_key(path), host.split(".")[1], url, presigned)

    m = re.match(r"^(?P<bucket>[^.]+)\.s3(?:[-.](?P<region>[\w-]+))?\.amazonaws\.com(?:\.cn)?$", host)
    if m:
        return StorageRef("aws-s3", m["bucket"], _path_key(path), m["region"], url, presigned)
    if re.match(r"^s3[.-][\w-]+\.amazonaws\.com(?:\.cn)?$", host):  # path-style
        region = re.match(r"^s3[.-]([\w-]+)\.", host)
        return StorageRef("aws-s3", _first_path_segment(path), _rest_as_key(path), region.group(1) if region else None, url, presigned)

    if host == "storage.googleapis.com":
        return StorageRef("gcs", _first_path_segment(path), _rest_as_key(path), None, url, presigned)
    if host.endswith(".storage.googleapis.com"):
        return StorageRef("gcs", host[: -len(".storage.googleapis.com")], _path_key(path), None, url, presigned)

    m = re.match(r"^(?P<account>[^.]+)\.blob\.core\.windows\.net$", host)
    if m:  # container==bucket
        return StorageRef("azure-blob", m["account"] + "/" + _first_path_segment(path), _rest_as_key(path), None, url, presigned)

    # --- cloud-drive share links (pan.baidu.com, pan.sysu.edu.cn, ...) ---
    if host.startswith("pan."):
        return StorageRef("cloud-drive", host, unquote(path.lstrip("/")), None, url, presigned)

    # --- S3-compatible self-hosted (MinIO etc.): only claim it when signed or explicit s3 markers ---
    if presigned and any(k in parse_qs(query) for k in ("X-Amz-Signature", "Signature", "OSSAccessKeyId")):
        return StorageRef("s3-compatible", _first_path_segment(path), _rest_as_key(path), None, url, presigned)
    return None


def extract_storage_refs(*sources: str | None) -> list[StorageRef]:
    """Scan text/html sources, return deduplicated StorageRefs (order-stable)."""
    seen: set[tuple] = set()
    refs: list[StorageRef] = []
    for source in sources:
        if not source:
            continue
        for raw in URL_RE.findall(source):
            url = raw.rstrip(_TRAILING_PUNCT)
            ref = classify(url)
            if ref is None:
                continue
            dedup_key = (ref.provider, ref.bucket, ref.key, ref.url)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            refs.append(ref)
    return refs
