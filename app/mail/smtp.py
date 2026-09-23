"""SMTP client for NetEase (163/126).

Note: NetEase auto-saves messages sent through their SMTP to the server-side
Sent folder, so no IMAP APPEND is needed.
"""
from __future__ import annotations

import base64
import binascii
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import PROVIDERS
from .imap import MailError

_TIMEOUT = 30


@dataclass
class SmtpAccount:
    address: str
    auth_code: str
    provider: str = "163"
    display_name: str | None = None

    @property
    def cfg(self) -> dict:
        try:
            return PROVIDERS[self.provider]
        except KeyError as e:
            raise MailError(f"unknown provider: {self.provider}") from e


def build_mime(
    account: SmtpAccount,
    to: list[str],
    subject: str,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    from_addr = account.address
    msg["From"] = (
        formataddr((account.display_name, from_addr)) if account.display_name else from_addr
    )
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=account.address.split("@", 1)[-1])

    if text is None and html is not None:
        text = ""
    msg.set_content(text or "")
    if html:
        msg.add_alternative(html, subtype="html")

    for att in attachments or []:
        try:
            data = base64.b64decode(att["content_base64"], validate=True)
        except (binascii.Error, ValueError) as e:
            raise MailError(f"attachment {att.get('filename')!r} is not valid base64") from e
        ctype = att.get("content_type", "application/octet-stream")
        maintype, _, subtype = ctype.partition("/")
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=att.get("filename") or "attachment"
        )
    return msg


def send_mail(
    account: SmtpAccount,
    to: list[str],
    subject: str,
    text: str | None = None,
    html: str | None = None,
    cc: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> str:
    msg = build_mime(account, to, subject, text, html, cc, attachments)
    try:
        with smtplib.SMTP_SSL(account.cfg["smtp_host"], account.cfg["smtp_port"], timeout=_TIMEOUT) as s:
            s.login(account.address, account.auth_code)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"SMTP login failed (check 授权码): {e}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP send failed: {e}") from e
    return msg["Message-ID"].strip("<>")


def verify(account: SmtpAccount) -> None:
    try:
        with smtplib.SMTP_SSL(account.cfg["smtp_host"], account.cfg["smtp_port"], timeout=_TIMEOUT) as s:
            s.login(account.address, account.auth_code)
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"SMTP login failed (check 授权码): {e}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP connection failed: {e}") from e
