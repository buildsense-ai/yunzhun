"""ORM models: accounts, folders, messages, attachments."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(20), default="163")
    protocol: Mapped[str] = mapped_column(String(10), default="imap")  # imap | pop3
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_code_enc: Mapped[str] = mapped_column(Text)  # Fernet-encrypted 授权码
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    folders: Mapped[list["Folder"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Folder(Base):
    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("account_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))  # raw IMAP name (modified UTF-7)
    name_decoded: Mapped[str] = mapped_column(String(512), default="")  # display name
    delim: Mapped[str | None] = mapped_column(String(8), nullable=True)
    uidvalidity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uidnext: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_uid: Mapped[int] = mapped_column(Integer, default=0)
    messages_count: Mapped[int] = mapped_column(Integer, default=0)
    unseen_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped[Account] = relationship(back_populates="folders")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("account_id", "folder_id", "uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id"), index=True)
    uid: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str | None] = mapped_column(String(998), nullable=True, index=True)

    subject: Mapped[str] = mapped_column(Text, default="")
    from_addr: Mapped[list | None] = mapped_column(JSON, nullable=True)
    to_addrs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    cc_addrs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # Date header
    internal_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # lazy-fetched
    body_fetched: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    account: Mapped[Account] = relationship(back_populates="messages")
    folder: Mapped[Folder] = relationship(back_populates="messages")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    object_refs: Mapped[list["ObjectRef"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    judgment: Mapped["Judgment | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    filename: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    disposition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    part_index: Mapped[int] = mapped_column(Integer)  # leaf-part order for re-extraction

    message: Mapped[Message] = relationship(back_populates="attachments")


class ObjectRef(Base):
    """Object-storage reference (OSS/OBS/COS/S3/...) extracted from a message body."""

    __tablename__ = "object_refs"
    __table_args__ = (UniqueConstraint("message_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    bucket: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    presigned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Message] = relationship(back_populates="object_refs")


class Judgment(Base):
    """Jev (System One) semantic judgment for a message."""

    __tablename__ = "judgments"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(40))  # delivery|billing|security|notification|personal|other
    category_confidence: Mapped[float] = mapped_column(default=0.0)
    storage_delivery: Mapped[float] = mapped_column(default=0.0)  # Noul p(true)
    action_required: Mapped[int] = mapped_column(default=0)  # Score 0..2
    model: Mapped[str] = mapped_column(String(40), default="")
    raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Message] = relationship(back_populates="judgment")
