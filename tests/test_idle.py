"""IMAP IDLE push tests (fake idler)."""
from __future__ import annotations

import threading
import time

from app.mail import imap as imap_client
from app.services import idle as idle_service
from tests.fakes import FakeImap


class FakeIdler:
    """Stands in for imaplib.Idler: pushes one EXISTS then ticks forever."""

    def __init__(self, pushes):
        self._pushes = list(pushes)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _pop(self, timeout):
        if self._pushes:
            return self._pushes.pop(0)
        return ("", None)


class IdleImap(FakeImap):
    capabilities = ("IMAP4rev1", "IDLE")

    def __init__(self, pushes=None):
        super().__init__()
        self._pushes = pushes or []

    def idle(self, duration=None):
        return FakeIdler(self._pushes)


class NoIdleImap(FakeImap):
    capabilities = ("IMAP4rev1",)


def _setup_account(client_headers=None):
    from app.db import init_db, SessionLocal
    from app.models import Account
    from app.security import encrypt

    init_db()
    with SessionLocal() as s:
        acct = s.query(Account).filter_by(address="idle-test@163.com").first()
        if acct is None:
            acct = Account(address="idle-test@163.com", auth_code_enc=encrypt("A"),
                           provider="163")
            s.add(acct)
            s.commit()
            s.refresh(acct)
        return acct.id


def test_idle_push_triggers_sync_and_pipeline(monkeypatch):
    aid = _setup_account()
    imap = IdleImap(pushes=[(b"EXISTS", b"9")])
    monkeypatch.setattr(imap_client, "connect", lambda acct: imap)

    calls = {"sync": 0, "pipeline": 0}
    monkeypatch.setattr(idle_service, "sync_account",
                        lambda a: calls.__setitem__("sync", calls["sync"] + 1) or {})
    monkeypatch.setattr(idle_service, "process_pending",
                        lambda: calls.__setitem__("pipeline", calls["pipeline"] + 1) or {})

    stop = threading.Event()
    t = threading.Thread(target=idle_service._watch, args=(aid, stop), daemon=True)
    t.start()
    deadline = time.time() + 5
    while calls["sync"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=10)

    assert calls["sync"] >= 1
    assert calls["pipeline"] >= 1


def test_idle_fallback_polls_when_unsupported(monkeypatch):
    aid = _setup_account()
    monkeypatch.setattr(imap_client, "connect", lambda acct: NoIdleImap())

    calls = {"sync": 0}
    monkeypatch.setattr(idle_service, "sync_account",
                        lambda a: calls.__setitem__("sync", calls["sync"] + 1) or {})

    from types import SimpleNamespace
    monkeypatch.setattr(idle_service, "get_settings",
                        lambda: SimpleNamespace(sync_interval_seconds=0.05,
                                                idle_folder="INBOX",
                                                idle_heartbeat_seconds=1500))

    stop = threading.Event()
    t = threading.Thread(target=idle_service._watch, args=(aid, stop), daemon=True)
    t.start()
    time.sleep(0.3)
    stop.set()
    t.join(timeout=10)

    assert calls["sync"] >= 1  # polled at least once without IDLE


def test_idle_worker_exits_on_deleted_account(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(idle_service, "_load_account",
                        lambda aid: (_ for _ in ()).throw(LookupError("gone")))
    t = threading.Thread(target=idle_service._idle_worker, args=(999999, stop), daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
