"""The full pipeline, strung together: fetch body -> judge -> gated pull.

Runs in the background loop (and on-demand via POST /v1/pipeline/run) so that
new delivery mails flow from IMAP to ./downloads without human involvement.
"""
from __future__ import annotations

import logging
import time

from fastapi import HTTPException
from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Judgment, Message

log = logging.getLogger(__name__)


def pending_message_ids(limit: int) -> list[int]:
    """Messages that still need a body fetch and/or a Jev judgment."""
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Message.id)
                .outerjoin(Judgment, Judgment.message_id == Message.id)
                .where((Message.body_fetched == False) | (Judgment.id.is_(None)))  # noqa: E712
                .order_by(Message.id)
                .limit(limit)
            ).scalars()
        )


def process_pending(limit: int | None = None, recursive: bool = True) -> dict:
    settings = get_settings()
    limit = limit or settings.pipeline_batch_limit
    stats = {"pending": 0, "fetched": 0, "judged": 0, "pulled": 0, "skipped": 0, "errors": 0}

    from ..services import jev as jev_service
    from ..services.downloader import pull_message
    from ..services.jev import judge_message
    from ..services.mailbox import ensure_message_body

    # Fast no-op when Jev is not configured: judging is the pipeline's spine.
    try:
        jev_service.resolve_provider()
    except jev_service.JevUnavailable as e:
        return {**stats, "reason": str(e.detail)}

    ids = pending_message_ids(limit)
    stats["pending"] = len(ids)
    for mid in ids:
        try:
            ensure_message_body(mid)
            stats["fetched"] += 1
            judgment = judge_message(mid)
            stats["judged"] += 1
            if judgment.category == "delivery" or judgment.storage_delivery >= 0.5:
                result = pull_message(mid, recursive=recursive)
                stats["pulled"] += len(result["downloaded"])
                stats["skipped"] += len(result["skipped"])
                if result["downloaded"]:
                    log.info("pipeline pulled %d file(s) for message %d",
                             len(result["downloaded"]), mid)
        except HTTPException as e:
            # upstream transient failures (rate limit / gateway): stop this round
            if e.status_code in (429, 502, 503):
                log.warning("pipeline pausing round on HTTP %s", e.status_code)
                break
            log.warning("pipeline HTTP error on message %s: %s", mid, e.detail)
            stats["errors"] += 1
        except LookupError as e:
            log.warning("pipeline lookup miss: %s", e)
            stats["errors"] += 1
        time.sleep(0.3)  # be gentle with free-tier rate limits
    return stats
