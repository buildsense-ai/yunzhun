"""MIME parsing tuned for real-world 163 mail (GBK/gb2312 legacy charsets)."""
from __future__ import annotations

import re
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import getaddresses, parsedate_to_datetime

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

CHARSET_FALLBACKS = ("utf-8", "gb18030", "gbk", "big5", "latin-1")


def decode_header_value(value: str | None) -> str:
    """RFC 2047 decode (=?gbk?B?...?= etc.) with safe fallback."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def parse_addresses(header_value) -> list[dict[str, str]]:
    if header_value is None:
        return []
    try:
        pairs = getaddresses([str(header_value)])
        return [{"name": decode_header_value(n), "email": a} for n, a in pairs if n or a]
    except Exception:
        return [{"name": "", "email": str(header_value)}]


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None


def decode_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    declared = part.get_content_charset()
    for cs in ([declared] if declared else []) + list(CHARSET_FALLBACKS):
        try:
            return payload.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", "replace")


def make_snippet(text: str | None, html: str | None, limit: int = 240) -> str | None:
    source = text if text and text.strip() else (re.sub(_TAG_RE, " ", html) if html else "")
    if not source:
        return None
    collapsed = _WS_RE.sub(" ", source).strip()
    return collapsed[:limit] or None


def _base_fields(msg: Message) -> dict:
    message_id = msg.get("Message-ID")
    return {
        "message_id": message_id.strip().strip("<>") if message_id else None,
        "subject": decode_header_value(msg.get("Subject")),
        "from": parse_addresses(msg.get("From")),
        "to": parse_addresses(msg.get("To")),
        "cc": parse_addresses(msg.get("Cc")),
        "date": parse_date(msg.get("Date")),
    }


def parse_header_block(raw: bytes) -> dict:
    """Parse a header-only FETCH payload (used during folder sync)."""
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    fields = _base_fields(msg)
    fields.update({"text": None, "html": None, "snippet": None, "attachments": []})
    return fields


def parse_message(raw: bytes) -> dict:
    """Full parse of a complete RFC 822 message."""
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    fields = _base_fields(msg)

    text: str | None = None
    html: str | None = None
    attachments: list[dict] = []
    leaf_index = 0

    for part in msg.walk():
        if part.is_multipart():
            continue
        leaf_index += 1
        filename = None
        try:
            filename = part.get_filename()
        except Exception:
            filename = None
        try:
            disposition = part.get_content_disposition()
        except Exception:
            disposition = None
        ctype = part.get_content_type()

        if disposition == "attachment" or (filename and disposition != "inline"):
            attachments.append(
                {
                    "filename": decode_header_value(filename) if filename else f"part-{leaf_index}",
                    "content_type": ctype,
                    "disposition": disposition,
                    "size": len(part.get_payload(decode=True) or b""),
                    "part_index": leaf_index,
                }
            )
        elif ctype == "text/plain" and text is None:
            text = decode_text(part)
        elif ctype == "text/html" and html is None:
            html = decode_text(part)

    fields.update(
        {
            "text": text,
            "html": html,
            "snippet": make_snippet(text, html),
            "attachments": attachments,
        }
    )
    return fields


def extract_leaf_part(raw: bytes, part_index: int) -> Message | None:
    """Re-walk a stored raw message and return the Nth leaf part (1-based)."""
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    i = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        i += 1
        if i == part_index:
            return part
    return None
