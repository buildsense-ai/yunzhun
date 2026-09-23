"""POP3 client (fallback channel; primary pipeline is IMAP-based sync)."""
from __future__ import annotations

import poplib
from dataclasses import dataclass

from ..config import PROVIDERS
from .imap import MailError

_TIMEOUT = 30


@dataclass
class Pop3Account:
    address: str
    auth_code: str
    provider: str = "163"

    @property
    def cfg(self) -> dict:
        try:
            return PROVIDERS[self.provider]
        except KeyError as e:
            raise MailError(f"unknown provider: {self.provider}") from e


def connect(acct: Pop3Account) -> poplib.POP3_SSL:
    try:
        pop = poplib.POP3_SSL(acct.cfg["pop3_host"], acct.cfg["pop3_port"], timeout=_TIMEOUT)
        pop.user(acct.address)
        pop.pass_(acct.auth_code)
    except poplib.error_proto as e:
        raise MailError(f"POP3 login failed for {acct.address}: {e}") from e
    except OSError as e:
        raise MailError(f"POP3 connection failed: {e}") from e
    return pop


def list_messages(pop: poplib.POP3_SSL) -> list[tuple[int, int]]:
    """[(message_number, size)]"""
    count, _total_size = pop.stat()
    if count == 0:
        return []
    _typ, listings, _octets = pop.list()
    out: list[tuple[int, int]] = []
    for line in listings:
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            out.append((int(parts[0]), int(parts[1])))
    return out


def uid_map(pop: poplib.POP3_SSL) -> dict[int, str]:
    _typ, uidls, _octets = pop.uidl()
    out: dict[int, str] = {}
    for line in uidls:
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            out[int(parts[0])] = parts[1].decode("ascii", "replace")
    return out


def fetch(pop: poplib.POP3_SSL, num: int) -> bytes:
    _typ, lines, _octets = pop.retr(num)
    return b"\r\n".join(lines)


def delete(pop: poplib.POP3_SSL, num: int) -> None:
    pop.dele(num)
