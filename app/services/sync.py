"""Background sync engine: IMAP -> local cache (incremental UID, UIDVALIDITY-safe)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as SASession

from ..config import get_settings
from ..db import SessionLocal
from ..mail import imap as imap_client
from ..mail.imap import ImapAccount, MailError
from ..mail.parse import parse_header_block
from ..models import Account, Attachment, Folder, Judgment, Message, ObjectRef, utcnow
from ..security import decrypt

log = logging.getLogger(__name__)


def _upsert_message_from_header(session: SASession, folder: Folder, header_item: dict) -> Message:
    parsed = parse_header_block(header_item["payload"])
    flags = header_item["flags"]
    msg = Message(
        account_id=folder.account_id,
        folder_id=folder.id,
        uid=header_item["uid"],
        message_id=parsed["message_id"],
        subject=parsed["subject"],
        from_addr=parsed["from"],
        to_addrs=parsed["to"],
        cc_addrs=parsed["cc"],
        date=parsed["date"],
        internal_date=header_item["internal_date"],
        size=header_item["size"],
        flags=flags,
        seen="\\Seen" in flags,
        answered="\\Answered" in flags,
        flagged="\\Flagged" in flags,
        deleted="\\Deleted" in flags,
    )
    session.add(msg)
    return msg


def _refresh_flags(session: SASession, folder: Folder, flags_map: dict[int, list[str]]) -> int:
    if not flags_map:
        return 0
    rows = session.scalars(
        select(Message).where(Message.folder_id == folder.id, Message.uid.in_(flags_map.keys()))
    ).all()
    updates = 0
    for row in rows:
        flags = flags_map[row.uid]
        if row.flags != flags:
            row.flags = flags
            row.seen = "\\Seen" in flags
            row.answered = "\\Answered" in flags
            row.flagged = "\\Flagged" in flags
            row.deleted = "\\Deleted" in flags
            updates += 1
    return updates


def sync_folder(
    imap, session: SASession, account: Account, folder: Folder, mode: str
) -> tuple[int, int]:
    """Sync one folder. Returns (new_messages, flag_updates)."""
    settings = get_settings()
    st = imap_client.status_folder(imap, folder.name)

    # UIDVALIDITY changed (or forced full): cache is invalid, wipe and resync.
    if mode == "full" or folder.uidvalidity != st.uidvalidity:
        doomed = select(Message.id).where(Message.folder_id == folder.id)
        session.query(ObjectRef).filter(ObjectRef.message_id.in_(doomed)).delete(synchronize_session=False)
        session.query(Judgment).filter(Judgment.message_id.in_(doomed)).delete(synchronize_session=False)
        session.query(Attachment).filter(Attachment.message_id.in_(doomed)).delete(synchronize_session=False)
        session.query(Message).filter(Message.folder_id == folder.id).delete()
        folder.last_seen_uid = 0
    folder.uidvalidity = st.uidvalidity
    folder.uidnext = st.uidnext
    folder.messages_count = st.messages
    folder.unseen_count = st.unseen
    folder.updated_at = utcnow()

    new_count = 0
    for item in imap_client.fetch_headers_since(imap, folder.name, folder.last_seen_uid):
        if item["uid"] <= folder.last_seen_uid:
            continue
        _upsert_message_from_header(session, folder, item)
        folder.last_seen_uid = max(folder.last_seen_uid, item["uid"])
        new_count += 1

    flag_updates = _refresh_flags(
        session, folder, imap_client.fetch_flags_window(imap, folder.name, settings.flag_refresh_window)
    )
    return new_count, flag_updates


def sync_account(account_id: int, mode: str = "incremental") -> dict:
    with SessionLocal() as session:
        account = session.get(Account, account_id)
        if account is None:
            raise LookupError(f"account {account_id} not found")

        acct = ImapAccount(
            address=account.address,
            auth_code=decrypt(account.auth_code_enc),
            provider=account.provider,
        )
        stats = {"account": account.address, "folders": 0, "new_messages": 0, "flag_updates": 0}
        imap = imap_client.connect(acct)
        try:
            remote = {f.name: f for f in imap_client.list_folders(imap)}
            stored = {
                f.name: f
                for f in session.scalars(select(Folder).where(Folder.account_id == account.id)).all()
            }

            for name in set(stored) - set(remote):  # folder vanished server-side
                session.delete(stored.pop(name))

            for rf in remote.values():
                folder = stored.get(rf.name)
                if folder is None:
                    folder = Folder(
                        account_id=account.id, name=rf.name,
                        name_decoded=rf.name_decoded, delim=rf.delim,
                    )
                    session.add(folder)
                    session.flush()
                else:
                    folder.name_decoded = rf.name_decoded
                    folder.delim = rf.delim

                new_count, flag_updates = sync_folder(imap, session, account, folder, mode)
                stats["folders"] += 1
                stats["new_messages"] += new_count
                stats["flag_updates"] += flag_updates

            account.last_sync_at = utcnow()
            account.last_error = None
            session.commit()
            return stats
        except MailError as e:
            account.last_error = str(e)
            session.commit()
            raise
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass


def sync_all_accounts() -> list[dict]:
    results: list[dict] = []
    with SessionLocal() as session:
        account_ids = session.scalars(select(Account.id)).all()
    for account_id in account_ids:
        try:
            results.append(sync_account(account_id))
        except MailError as e:
            log.warning("sync failed for account %s: %s", account_id, e)
            results.append({"account_id": account_id, "error": str(e)})
        except Exception:  # noqa: BLE001
            log.exception("unexpected sync failure for account %s", account_id)
            results.append({"account_id": account_id, "error": "unexpected error"})
    return results
