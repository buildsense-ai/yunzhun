"""IMAP IDLE push: one worker thread per account watches INBOX for new mail.

On any EXISTS/RECENT/FETCH/EXPUNGE untagged response the worker runs an
incremental sync + a pipeline round, so a delivery mail lands on disk within
seconds of arriving instead of waiting for the polling loops.

Servers without IDLE capability fall back to plain interval polling.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..mail import imap as imap_client
from ..mail.imap import ImapAccount, MailError
from ..models import Account
from ..security import decrypt
from .pipeline import process_pending
from .sync import sync_account

log = logging.getLogger(__name__)

_POLL_TICK = 5.0  # seconds between stop-event checks while idling
_PUSH_TYPES = {b"EXISTS", b"RECENT", b"EXPUNGE", b"FETCH"}


class IdleManager:
    """Supervises one IDLE worker thread per account; reconciles every 30s."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._workers: dict[int, threading.Thread] = {}
        self._supervisor: threading.Thread | None = None

    def start(self) -> None:
        self._supervisor = threading.Thread(
            target=self._supervise, daemon=True, name="idle-supervisor"
        )
        self._supervisor.start()
        log.info("IMAP IDLE manager started")

    def stop(self) -> None:
        self._stop.set()
        for t in self._workers.values():
            t.join(timeout=_POLL_TICK + 5)

    def _supervise(self) -> None:
        while not self._stop.is_set():
            self._reconcile()
            self._stop.wait(30)

    def _reconcile(self) -> None:
        try:
            with SessionLocal() as session:
                ids = set(session.scalars(select(Account.id)).all())
        except Exception:  # noqa: BLE001
            log.exception("idle reconcile failed")
            return
        # prune dead workers / deleted accounts
        for aid in list(self._workers):
            if aid not in ids or not self._workers[aid].is_alive():
                self._workers.pop(aid, None)
        for aid in ids - set(self._workers):
            t = threading.Thread(
                target=_idle_worker, args=(aid, self._stop),
                daemon=True, name=f"idle-acct-{aid}",
            )
            self._workers[aid] = t
            t.start()


def _load_account(account_id: int) -> ImapAccount:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")
        return ImapAccount(
            address=account.address,
            auth_code=decrypt(account.auth_code_enc),
            provider=account.provider,
        )


def _on_push(account_id: int) -> None:
    """New-mail signal: sync immediately, then let the pipeline do its thing."""
    try:
        stats = sync_account(account_id)
        log.info("idle-triggered sync for account %s: %s", account_id, stats)
        pstats = process_pending()
        if pstats.get("pulled"):
            log.info("idle-triggered pipeline: %s", pstats)
    except Exception:  # noqa: BLE001
        log.exception("idle-triggered sync/pipeline failed for account %s", account_id)


def _watch(account_id: int, stop: threading.Event) -> None:
    """One IDLE session. Returns when connection drops or stop is set."""
    settings = get_settings()
    acct = _load_account(account_id)
    imap = imap_client.connect(acct)
    try:
        if "IDLE" not in getattr(imap, "capabilities", ()):
            log.info("account %s: no IDLE capability, polling instead", account_id)
            while not stop.wait(settings.sync_interval_seconds):
                sync_account(account_id)
            return

        while not stop.is_set():
            imap_client.select(imap, settings.idle_folder, readonly=True)
            # re-SELECT + re-IDLE after every push (RFC 2177: one-shot per IDLE)
            with imap.idle(duration=settings.idle_heartbeat_seconds) as idler:
                while not stop.is_set():
                    typ, _data = idler._pop(_POLL_TICK)  # noqa: SLF001 — only way to tick
                    if not typ:
                        continue  # tick elapsed; loop back to check stop
                    if typ in _PUSH_TYPES:
                        log.info("idle push on account %s: %s", account_id, typ)
                        _on_push(account_id)
                        break  # fresh SELECT + IDLE
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


def _idle_worker(account_id: int, stop: threading.Event) -> None:
    backoff = 5
    while not stop.is_set():
        try:
            _watch(account_id, stop)
            backoff = 5
        except LookupError:
            return  # account deleted — exit quietly
        except MailError as e:
            log.warning("idle account %s: %s (retry in %ds)", account_id, e, backoff)
        except Exception:  # noqa: BLE001
            log.exception("idle worker crashed for account %s", account_id)
        if stop.wait(backoff):
            return
        backoff = min(backoff * 2, 300)
