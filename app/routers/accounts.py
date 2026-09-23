"""Account management: register 163 accounts (授权码), verify, delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from ..config import PROVIDERS
from ..db import SessionLocal
from ..mail import imap as imap_client
from ..mail import smtp as smtp_client
from ..mail.imap import ImapAccount, MailError
from ..mail.smtp import SmtpAccount
from ..models import Account
from ..schemas import AccountCreate, AccountOut, VerifyResult
from ..security import decrypt, encrypt, require_api_key

router = APIRouter(prefix="/v1/accounts", tags=["accounts"], dependencies=[Depends(require_api_key)])


def _get_account(account_id: int) -> Account:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        session.refresh(account)
        return account


def _smtp_account(account: Account) -> SmtpAccount:
    return SmtpAccount(
        address=account.address,
        auth_code=decrypt(account.auth_code_enc),
        provider=account.provider,
        display_name=account.display_name,
    )


@router.post("", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate) -> Account:
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"unsupported provider {body.provider!r}; supported: {list(PROVIDERS)}")
    if body.protocol not in ("imap", "pop3"):
        raise HTTPException(400, "protocol must be 'imap' or 'pop3'")

    if body.verify:
        errors: list[str] = []
        try:
            conn = imap_client.connect(ImapAccount(body.address, body.auth_code, body.provider))
            try:
                conn.logout()
            except Exception:  # noqa: BLE001
                pass
        except MailError as e:
            errors.append(str(e))
        try:
            smtp_client.verify(SmtpAccount(body.address, body.auth_code, body.provider))
        except MailError as e:
            errors.append(str(e))
        if errors:
            raise HTTPException(400, "; ".join(errors))

    with SessionLocal() as session:
        exists = session.scalar(select(Account).where(Account.address == body.address))
        if exists:
            raise HTTPException(409, f"account {body.address} already exists (id={exists.id})")
        account = Account(
            address=body.address,
            provider=body.provider,
            protocol=body.protocol,
            display_name=body.display_name,
            auth_code_enc=encrypt(body.auth_code),
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return account


@router.get("", response_model=list[AccountOut])
def list_accounts() -> list[Account]:
    with SessionLocal() as session:
        return session.scalars(select(Account).order_by(Account.id)).all()


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int) -> Account:
    return _get_account(account_id)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int) -> None:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        session.delete(account)
        session.commit()


@router.post("/{account_id}/verify", response_model=VerifyResult)
def verify_account(account_id: int) -> VerifyResult:
    account = _get_account(account_id)
    result = VerifyResult(imap_ok=False, smtp_ok=False)
    try:
        conn = imap_client.connect(
            ImapAccount(account.address, decrypt(account.auth_code_enc), account.provider)
        )
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
        result.imap_ok = True
    except MailError as e:
        result.imap_error = str(e)
    try:
        smtp_client.verify(_smtp_account(account))
        result.smtp_ok = True
    except MailError as e:
        result.smtp_error = str(e)
    return result
