"""Folder listing (from local cache)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Account, Folder
from ..schemas import FolderOut
from ..security import require_api_key

router = APIRouter(prefix="/v1/accounts/{account_id}/folders", tags=["folders"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[FolderOut])
def list_folders(account_id: int) -> list[Folder]:
    with SessionLocal() as session:
        if session.get(Account, account_id) is None:
            raise LookupError(f"account {account_id} not found")
        return session.scalars(
            select(Folder).where(Folder.account_id == account_id).order_by(Folder.id)
        ).all()
