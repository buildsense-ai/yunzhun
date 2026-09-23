"""Fake IMAP/SMTP doubles simulating a small NetEase mailbox."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SUBJECT_A = "=?gbk?B?suLK1A==?="  # RFC2047-encoded GBK subject ("测试")

MSG_1_HEADERS = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: me@163.com\r\n"
    b"Subject: =?gbk?B?suLK1A==?=\r\n"
    b"Date: Thu, 17 Sep 2026 10:00:00 +0800\r\n"
    b"Message-ID: <msg1@example.com>\r\n"
    b"\r\n"
)

MSG_2_FULL = (
    b"From: =?gbk?B?xKzErA==?= <bob@example.com>\r\n"
    b"To: me@163.com\r\n"
    b"Cc: carol@example.com\r\n"
    b"Subject: Report\r\n"
    b"Date: Thu, 17 Sep 2026 11:30:00 +0800\r\n"
    b"Message-ID: <msg2@example.com>\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/mixed; boundary="BOUND"\r\n'
    b"\r\n"
    b"--BOUND\r\n"
    b"Content-Type: text/plain; charset=gbk\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n"
    b"\r\n"
    + "你好，这是一封 GBK 编码的邮件。".encode("gbk")
    + b"\r\n"
    b"--BOUND\r\n"
    b'Content-Type: application/pdf; name="report.pdf"\r\n'
    b"Content-Disposition: attachment; filename=report.pdf\r\n"
    b"Content-Transfer-Encoding: base64\r\n"
    b"\r\n"
    b"JVBERi0xLjQK\r\n"
    b"--BOUND--\r\n"
)

MSG_3_FULL = (
    b"From: carol@example.com\r\n"
    b"To: me@163.com\r\n"
    b"Subject: HTML newsletter\r\n"
    b"Date: Thu, 18 Sep 2026 09:00:00 +0800\r\n"
    b"Message-ID: <msg3@example.com>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n"
    b"\r\n"
    b"<html><body><h1>Hello</h1><p>World</p></body></html>\r\n"
)

_MESSAGES = {
    1: {"raw": MSG_1_HEADERS + b"plain body\r\n", "flags": b"\\Seen", "size": 120},
    2: {"raw": MSG_2_FULL, "flags": b"", "size": 340},
    3: {"raw": MSG_3_FULL, "flags": b"\\Flagged", "size": 210},
}

_INTERNALDATE = {
    uid: (datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc) + timedelta(hours=uid)).strftime(
        "%d-%b-%Y %H:%M:%S +0000"
    )
    for uid in _MESSAGES
}


class FakeImap:
    """Implements the exact surface app.mail.imap uses."""

    def __init__(self, *args, **kwargs):
        self.logged_in = False
        self.id_sent = False
        self.stored: dict[int, list[str]] = {}

    # -- connection --
    def login(self, user, password):
        assert "@" in user
        if password == "bad-code":
            raise Exception("LOGIN failed")
        self.logged_in = True
        return "OK", [b"LOGIN completed"]

    def logout(self):
        self.logged_in = False
        return "BYE", [b"logging out"]

    def _simple_command(self, command, *args):
        assert command == "ID"
        self.id_sent = True
        return "OK", [b"ID completed"]

    # -- mailbox ops --
    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" INBOX']

    def select(self, name, readonly=False):
        assert name == "INBOX"
        return "OK", [b"3"]

    def status(self, name, items):
        assert name == "INBOX"
        return "OK", [b'STATUS "INBOX" (UIDVALIDITY 42 UIDNEXT 4 MESSAGES 3 UNSEEN 2)']

    def expunge(self):
        return "OK", [b"EXPUNGE completed"]

    # -- uid commands --
    def uid(self, command, *args):
        if command == "SEARCH":
            criteria = args[-1]
            start = int(criteria.split()[-1].split(":")[0])
            uids = [u for u in sorted(_MESSAGES) if u >= start]
            return "OK", [" ".join(map(str, uids) or [""]).encode()]
        if command == "FETCH":
            uid_set, spec = args[0], args[1]
            wanted = [int(u) for u in uid_set.split(",")]
            data = []
            for uid in wanted:
                if uid not in _MESSAGES:
                    continue
                m = _MESSAGES[uid]
                if "BODY.PEEK[]" in spec:
                    meta = f"1 (UID {uid} FLAGS ({m['flags'].decode()}) BODY[] {{{len(m['raw'])}}}".encode()
                    data.append((meta, m["raw"]))
                    data.append(b")")
                elif "BODY.PEEK[HEADER]" in spec:
                    header = m["raw"].split(b"\r\n\r\n")[0] + b"\r\n\r\n"
                    meta = (
                        f"1 (UID {uid} FLAGS ({m['flags'].decode()}) "
                        f'INTERNALDATE "{_INTERNALDATE[uid]}" RFC822.SIZE {m["size"]} '
                        f"BODY[HEADER] {{{len(header)}}}".encode()
                    )
                    data.append((meta, header))
                    data.append(b")")
                elif "FLAGS" in spec:
                    meta = f"1 (UID {uid} FLAGS ({m['flags'].decode()}))".encode()
                    data.append((meta, None))
                else:
                    raise AssertionError(f"unexpected FETCH spec {spec}")
            # include stored flag overrides
            return "OK", data
        if command == "STORE":
            uid, op, flag_list = args
            uid = int(uid)
            flags = flag_list.strip("()").split() if flag_list.strip("()") else []
            current = self.stored.setdefault(uid, _MESSAGES[uid]["flags"].decode().split())
            if op.startswith("+"):
                for f in flags:
                    if f not in current:
                        current.append(f)
            else:
                for f in flags:
                    if f in current:
                        current.remove(f)
            return "OK", [b"STORE completed"]
        raise AssertionError(f"unexpected UID command {command}")


class FakePop:
    def __init__(self, *args, **kwargs):
        pass

    def user(self, address):
        return b"+OK"

    def pass_(self, secret):
        return b"+OK"

    def stat(self):
        return (2, 500)

    def list(self):
        return b"+OK", [b"1 200", b"2 300"], 5

    def uidl(self):
        return b"+OK", [b"1 uid-one", b"2 uid-two"], 5

    def retr(self, num):
        raw = MSG_2_FULL if num == 2 else MSG_3_FULL
        return b"+OK", raw.split(b"\r\n"), len(raw)

    def rset(self):
        return b"+OK"

    def quit(self):
        return b"+OK"
