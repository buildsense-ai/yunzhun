"""Jev semantic judgment endpoints: judge on demand, query by category/priority."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import Account, Judgment, Message
from ..schemas import JudgmentOut
from ..security import require_api_key
from ..services.jev import judge_message

router = APIRouter(tags=["judgments"], dependencies=[Depends(require_api_key)])


@router.post("/v1/messages/{message_id}/judge", response_model=JudgmentOut)
def judge(message_id: int) -> Judgment:
    """Run one batched Jev call (category + storage_delivery + action_required)."""
    return judge_message(message_id)


@router.get("/v1/messages/{message_id}/judge", response_model=JudgmentOut)
def get_judgment(message_id: int) -> Judgment:
    with SessionLocal() as session:
        judgment = session.scalar(
            select(Judgment).where(Judgment.message_id == message_id)
        )
        if judgment is None:
            raise LookupError(f"no judgment for message {message_id} (POST to create)")
        return judgment


@router.get("/v1/accounts/{account_id}/judgments", response_model=list[JudgmentOut])
def list_judgments(
    account_id: int,
    category: str | None = Query(default=None, description="delivery|billing|security|notification|personal|other"),
    min_storage_delivery: float | None = Query(default=None, ge=0.0, le=1.0),
    min_action_required: int | None = Query(default=None, ge=0, le=2),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Judgment]:
    with SessionLocal() as session:
        if session.get(Account, account_id) is None:
            raise LookupError(f"account {account_id} not found")
        stmt = (
            select(Judgment)
            .join(Message, Judgment.message_id == Message.id)
            .where(Message.account_id == account_id)
        )
        if category:
            stmt = stmt.where(Judgment.category == category)
        if min_storage_delivery is not None:
            stmt = stmt.where(Judgment.storage_delivery >= min_storage_delivery)
        if min_action_required is not None:
            stmt = stmt.where(Judgment.action_required >= min_action_required)
        stmt = (
            stmt.options(selectinload(Judgment.message))
            .order_by(Message.internal_date.desc().nulls_last())
            .limit(limit)
        )
        return session.scalars(stmt).all()
