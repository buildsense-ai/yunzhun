"""Kanban board API: aggregate messages -> judgments -> pulls into columns.

Stage derivation (no manual state yet):
  pending     – no Jev judgment yet
  identified  – judged delivery-ish, has storage refs, nothing pulled yet
  downloaded  – >=1 pull record done, zero failed
  failed      – >=1 pull record failed
  other       – judged non-delivery (or delivery-ish but no refs)
Future stages (analysis / archived) are returned as disabled placeholders —
the GPU-side dispatcher will own them once it exists.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import Judgment, Message, ObjectRef, PullRecord
from ..security import require_api_key

router = APIRouter(tags=["board"], dependencies=[Depends(require_api_key)])

COLUMNS = [
    {"key": "pending", "title": "待判定", "hint": "已入库，等待 Jev 语义判断"},
    {"key": "identified", "title": "已识别交付", "hint": "判定为交付邮件，等待/可以下载"},
    {"key": "downloaded", "title": "已下载", "hint": "文件已落盘并通过完整性校验"},
    {"key": "failed", "title": "失败", "hint": "下载或校验失败，可人工介入"},
    {"key": "other", "title": "其他邮件", "hint": "非交付类邮件"},
    {"key": "analysis", "title": "分析中", "hint": "Cell Ranger 队列（下游接入后启用）", "disabled": True},
    {"key": "archived", "title": "已归档", "hint": "表达矩阵归档（下游接入后启用）", "disabled": True},
]


def _stage(msg: Message, done: int, failed: int) -> str:
    j = msg.judgment
    if j is None:
        return "pending"
    is_delivery = j.category == "delivery" or j.storage_delivery >= 0.5
    if not is_delivery:
        return "other"
    if failed:
        return "failed"
    if done:
        return "downloaded"
    return "identified"


@router.get("/v1/board")
def board() -> dict:
    with SessionLocal() as session:
        messages = session.scalars(
            select(Message)
            .options(selectinload(Message.judgment), selectinload(Message.object_refs))
            .order_by(Message.internal_date.desc().nulls_last(), Message.id.desc())
            .limit(200)
        ).all()

        # aggregate pull records per message
        agg: dict[int, dict] = {}
        for mid, status, cnt, size in session.execute(
            select(
                PullRecord.message_id,
                PullRecord.status,
                func.count(),
                func.coalesce(func.sum(PullRecord.size), 0),
            ).group_by(PullRecord.message_id, PullRecord.status)
        ).all():
            a = agg.setdefault(mid, {"done": 0, "failed": 0, "bytes": 0})
            a[status] = cnt
            a["bytes"] += size

        cards: dict[str, list[dict]] = {c["key"]: [] for c in COLUMNS}
        for m in messages:
            a = agg.get(m.id, {"done": 0, "failed": 0, "bytes": 0})
            stage = _stage(m, a["done"], a["failed"])
            sender = ""
            if m.from_addr:
                sender = m.from_addr[0].get("name") or m.from_addr[0].get("email", "")
            cards[stage].append(
                {
                    "message_id": m.id,
                    "account_id": m.account_id,
                    "subject": m.subject,
                    "from": sender,
                    "date": (m.internal_date or m.date or m.created_at).isoformat()
                    if (m.internal_date or m.date or m.created_at)
                    else None,
                    "category": m.judgment.category if m.judgment else None,
                    "storage_delivery": m.judgment.storage_delivery if m.judgment else None,
                    "refs": [
                        {"provider": r.provider, "bucket": r.bucket, "key": r.key}
                        for r in m.object_refs
                    ],
                    "pull": {"done": a["done"], "failed": a["failed"], "bytes": a["bytes"]},
                }
            )

        columns = [
            {**c, "count": len(cards[c["key"]]), "cards": cards[c["key"]]}
            for c in COLUMNS
        ]
        stats = {
            "messages": len(messages),
            "delivery": len(cards["identified"]) + len(cards["downloaded"]) + len(cards["failed"]),
            "files_done": sum(a["done"] for a in agg.values()),
            "files_failed": sum(a["failed"] for a in agg.values()),
            "bytes": sum(a["bytes"] for a in agg.values()),
        }
        return {"columns": columns, "stats": stats}
