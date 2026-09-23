"""Pydantic schemas for the REST API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


# ---------- accounts ----------
class AccountCreate(BaseModel):
    address: str
    auth_code: str = Field(min_length=1, description="163 授权码，不是网页登录密码")
    provider: str = "163"
    protocol: str = "imap"  # imap | pop3
    display_name: str | None = None
    verify: bool = True  # test IMAP/SMTP login before storing


class AccountOut(BaseModel):
    id: int
    address: str
    provider: str
    protocol: str
    display_name: str | None
    status: str
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VerifyResult(BaseModel):
    imap_ok: bool
    smtp_ok: bool
    imap_error: str | None = None
    smtp_error: str | None = None


# ---------- folders ----------
class FolderOut(BaseModel):
    id: int
    name: str
    name_decoded: str
    delim: str | None
    uidvalidity: int | None
    messages_count: int
    unseen_count: int
    last_seen_uid: int

    model_config = {"from_attributes": True}


# ---------- messages ----------
class AddressOut(BaseModel):
    name: str = ""
    email: str = ""


class AttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    disposition: str | None
    size: int

    model_config = {"from_attributes": True}


class MessageListItem(BaseModel):
    id: int
    uid: int
    folder_id: int
    message_id: str | None
    subject: str
    from_addr: list[AddressOut] | None
    to_addrs: list[AddressOut] | None
    cc_addrs: list[AddressOut] | None
    date: datetime | None
    internal_date: datetime | None
    size: int
    flags: list[str]
    seen: bool
    answered: bool
    flagged: bool
    deleted: bool
    snippet: str | None
    body_fetched: bool
    attachments: list[AttachmentOut] = []

    model_config = {"from_attributes": True}


class MessageDetail(MessageListItem):
    text_body: str | None
    html_body: str | None


class FlagsPatch(BaseModel):
    add: list[str] = []
    remove: list[str] = []


# ---------- sending ----------
class AttachmentIn(BaseModel):
    filename: str
    content_base64: str
    content_type: str = "application/octet-stream"


class SendRequest(BaseModel):
    to: list[EmailStr]
    cc: list[EmailStr] = []
    subject: str
    text: str | None = None
    html: str | None = None
    attachments: list[AttachmentIn] = []


class SendResult(BaseModel):
    message_id: str
    to: list[str]


# ---------- sync ----------
class SyncMode(BaseModel):
    mode: str = "incremental"  # incremental | full


class SyncResult(BaseModel):
    account: str
    folders: int
    new_messages: int
    flag_updates: int
