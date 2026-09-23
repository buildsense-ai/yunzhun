"""IMAP client tuned for NetEase (163/126) servers.

NetEase quirk: after login the client MUST issue an RFC 2971 ``ID`` command,
otherwise every subsequent command fails with
``Unsafe Login. Please contact kefu@188.com``.
"""
from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import PROVIDERS

# Register ID so imaplib allows it in AUTH/SELECTED state.
imaplib.Commands["ID"] = ("AUTH", "SELECTED")

_TIMEOUT = 30

_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_UID_RE = re.compile(rb"UID (\d+)")
_INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')
_SIZE_RE = re.compile(rb"RFC822\.SIZE (\d+)")
_STATUS_RE = re.compile(rb"\((.*)\)")
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>"(?:[^"\\]|\\.)*"|\S+)')

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


class MailError(Exception):
    """Raised for any upstream mail protocol failure."""


@dataclass
class ImapAccount:
    address: str
    auth_code: str
    provider: str = "163"

    @property
    def cfg(self) -> dict:
        try:
            return PROVIDERS[self.provider]
        except KeyError as e:
            raise MailError(f"unknown provider: {self.provider}") from e


def connect(acct: ImapAccount) -> imaplib.IMAP4_SSL:
    try:
        imap = imaplib.IMAP4_SSL(acct.cfg["imap_host"], acct.cfg["imap_port"], timeout=_TIMEOUT)
        imap.login(acct.address, acct.auth_code)
    except imaplib.IMAP4.error as e:
        raise MailError(f"IMAP login failed for {acct.address}: {e}") from e
    except OSError as e:
        raise MailError(f"IMAP connection failed: {e}") from e

    if acct.cfg.get("requires_id"):
        typ, dat = imap._simple_command(
            "ID", '("name" "Yunzhun" "version" "0.1.0" "vendor" "BuildSense AI")'
        )
        if typ != "OK":
            raise MailError(f"IMAP ID command rejected: {dat}")
    return imap


def _check(typ, dat, what: str) -> None:
    if typ != "OK":
        raise MailError(f"IMAP {what} failed: {dat}")


def _q(name: str) -> str:
    """Quote a mailbox name for IMAP commands when needed."""
    if name.startswith('"') and name.endswith('"'):
        return name
    if any(c in name for c in ' "'):
        return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return name


@dataclass
class RemoteFolder:
    name: str  # raw (still modified-UTF-7 encoded) name
    name_decoded: str
    delim: str | None
    flags: list[str]


def list_folders(imap: imaplib.IMAP4_SSL) -> list[RemoteFolder]:
    from . import utf7

    typ, data = imap.list()
    _check(typ, data, "LIST")
    folders: list[RemoteFolder] = []
    for entry in data or []:
        if entry is None:
            continue
        line = entry.decode("utf-8", "replace") if isinstance(entry, bytes) else str(entry)
        m = _LIST_RE.search(line)
        if not m:
            continue
        name = m.group("name")
        if name.startswith('"'):
            name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        delim = m.group("delim")
        delim = None if delim == "NIL" else delim.strip('"')
        folders.append(
            RemoteFolder(
                name=name,
                name_decoded=utf7.decode(name),
                delim=delim,
                flags=m.group("flags").split(),
            )
        )
    return folders


@dataclass
class FolderStatus:
    uidvalidity: int
    uidnext: int
    messages: int
    unseen: int


def status_folder(imap: imaplib.IMAP4_SSL, name: str) -> FolderStatus:
    typ, data = imap.status(_q(name), "(UIDVALIDITY UIDNEXT MESSAGES UNSEEN)")
    _check(typ, data, f"STATUS {name}")
    payload = data[0]
    raw = payload if isinstance(payload, bytes) else b"".join(p or b"" for p in payload)
    m = _STATUS_RE.search(raw)
    body = m.group(1) if m else raw
    vals = dict(re.findall(rb"(UIDVALIDITY|UIDNEXT|MESSAGES|UNSEEN) (\d+)", body))
    return FolderStatus(
        uidvalidity=int(vals.get(b"UIDVALIDITY", 0)),
        uidnext=int(vals.get(b"UIDNEXT", 1)),
        messages=int(vals.get(b"MESSAGES", 0)),
        unseen=int(vals.get(b"UNSEEN", 0)),
    )


def parse_internal_date(raw: bytes | str) -> datetime | None:
    """Parse IMAP internaldate like '17-Sep-2026 10:00:00 +0800' (locale-free)."""
    s = (raw.decode() if isinstance(raw, bytes) else raw).strip('"')
    m = re.match(r"(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})", s)
    if not m:
        return None
    day, mon, year, hh, mm, ss, sign, oh, om = m.groups()
    month = _MONTHS.get(mon.title())
    if month is None:
        return None
    offset = timedelta(hours=int(oh), minutes=int(om))
    if sign == "-":
        offset = -offset
    try:
        return datetime(
            int(year), month, int(day), int(hh), int(mm), int(ss),
            tzinfo=timezone(offset),
        )
    except ValueError:
        return None


def _parse_fetch_item(meta: bytes, payload: bytes | None) -> dict | None:
    uid_m = _UID_RE.search(meta)
    if not uid_m:
        return None
    flags_m = _FLAGS_RE.search(meta)
    flags = flags_m.group(1).decode("utf-8", "replace").split() if flags_m else []
    date_m = _INTERNALDATE_RE.search(meta)
    size_m = _SIZE_RE.search(meta)
    return {
        "uid": int(uid_m.group(1)),
        "flags": flags,
        "internal_date": parse_internal_date(date_m.group(1)) if date_m else None,
        "size": int(size_m.group(1)) if size_m else 0,
        "payload": payload,
    }


def _iter_fetch_items(data) -> list[tuple[bytes, bytes | None]]:
    items: list[tuple[bytes, bytes | None]] = []
    for entry in data or []:
        if isinstance(entry, tuple):
            meta = entry[0] if isinstance(entry[0], bytes) else b""
            payload = entry[1] if len(entry) > 1 and isinstance(entry[1], bytes) else None
            items.append((meta, payload))
        elif isinstance(entry, bytes) and _UID_RE.search(entry):
            items.append((entry, None))
    return items


def select(imap: imaplib.IMAP4_SSL, name: str, readonly: bool = True) -> None:
    typ, data = imap.select(_q(name), readonly=readonly)
    _check(typ, data, f"SELECT {name}")


def _search_uids(imap: imaplib.IMAP4_SSL, criteria: str) -> list[int]:
    typ, data = imap.uid("SEARCH", None, criteria)
    _check(typ, data, f"UID SEARCH {criteria}")
    raw = data[0] if data and isinstance(data[0], bytes) else b""
    return [int(u) for u in raw.split() if u.isdigit()]


def fetch_headers_since(
    imap: imaplib.IMAP4_SSL, name: str, since_uid: int
) -> list[dict]:
    """New messages with uid > since_uid: flags + internaldate + size + header block."""
    select(imap, name, readonly=True)
    uids = [u for u in _search_uids(imap, f"UID {since_uid + 1}:*") if u > since_uid]
    if not uids:
        return []
    uid_set = ",".join(map(str, uids))
    typ, data = imap.uid(
        "FETCH", uid_set,
        "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER])",
    )
    _check(typ, data, "UID FETCH headers")

    out: list[dict] = []
    for meta, payload in _iter_fetch_items(data):
        item = _parse_fetch_item(meta, payload)
        if item and item["payload"]:
            out.append(item)
    out.sort(key=lambda x: x["uid"])
    return out


def fetch_flags_window(
    imap: imaplib.IMAP4_SSL, name: str, window: int
) -> dict[int, list[str]]:
    """FLAGS for the most recent `window` uids (server-side flag drift sync)."""
    select(imap, name, readonly=True)
    uids = _search_uids(imap, "UID 1:*")
    if not uids:
        return {}
    tail = uids[-window:]
    typ, data = imap.uid("FETCH", ",".join(map(str, tail)), "(UID FLAGS)")
    _check(typ, data, "UID FETCH flags")
    result: dict[int, list[str]] = {}
    for meta, _payload in _iter_fetch_items(data):
        item = _parse_fetch_item(meta, None)
        if item:
            result[item["uid"]] = item["flags"]
    return result


def fetch_message_body(imap: imaplib.IMAP4_SSL, name: str, uid: int) -> bytes:
    select(imap, name, readonly=True)
    typ, data = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
    _check(typ, data, f"UID FETCH body {uid}")
    for _meta, payload in _iter_fetch_items(data):
        if payload:
            return payload
    raise MailError(f"empty FETCH body response for uid {uid}")


def store_flags(
    imap: imaplib.IMAP4_SSL,
    name: str,
    uid: int,
    add: list[str],
    remove: list[str],
    expunge: bool = False,
) -> None:
    select(imap, name, readonly=False)
    if add:
        typ, data = imap.uid("STORE", str(uid), "+FLAGS.SILENT", "(" + " ".join(add) + ")")
        _check(typ, data, "UID STORE +FLAGS")
    if remove:
        typ, data = imap.uid("STORE", str(uid), "-FLAGS.SILENT", "(" + " ".join(remove) + ")")
        _check(typ, data, "UID STORE -FLAGS")
    if expunge:
        typ, data = imap.expunge()
        _check(typ, data, "EXPUNGE")
