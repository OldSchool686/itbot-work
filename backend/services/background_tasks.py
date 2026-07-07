"""Background task runner for pending sync retry and auto-close."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.ticket import Ticket
from backend.services.bitrix_service import get_bitrix_service
from backend.utils.config import settings
from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)

_BITRIX_CONCURRENCY = 5
_MAX_FAILURES = 10
_FAILURE_TTL = 86400 * 7


async def _sync_single_ticket(ticket: Ticket, bitrix) -> bool:
    """Sync a single ticket to Bitrix24. Returns True on success."""
    redis = await get_redis()
    fail_key = f"sync_failures:{ticket.id}"
    fail_count = int(await redis.get(fail_key) or 0)

    if fail_count >= _MAX_FAILURES:
        logger.warning(
            f"Ticket #{ticket.id} exceeded max sync failures ({fail_count}), "
            f"manual intervention required."
        )
        return False

    try:
        deal_id = await bitrix.create_deal(
            title=f"[BOT #{ticket.id}] {ticket.category}: {ticket.full_name}",
            stage_id=settings.bitrix24_stage_new,
            contact_id=None,
            phone=ticket.phone,
            department=ticket.department,
            category=ticket.category,
            ticket_id=ticket.id,
            description=ticket.description,
        )
        if deal_id:
            await redis.delete(fail_key)
            ticket.status = "new"
            ticket.bitrix_deal_id = deal_id
            return True
    except Exception:
        fail_count += 1
        await redis.set(fail_key, str(fail_count), ex=_FAILURE_TTL)
        logger.exception(
            f"Failed to sync pending ticket #{ticket.id} "
            f"(failure {fail_count}/{_MAX_FAILURES})"
        )
    return False


async def _close_single_ticket(ticket: Ticket, bitrix) -> bool:
    """Close a single resolved ticket. Returns True on success."""
    if not ticket.bitrix_deal_id:
        ticket.status = "closed"
        return True

    redis = await get_redis()
    fail_key = f"close_failures:{ticket.id}"
    fail_count = int(await redis.get(fail_key) or 0)

    if fail_count >= _MAX_FAILURES:
        logger.warning(
            f"Ticket #{ticket.id} exceeded max close failures ({fail_count}), "
            f"closing locally without Bitrix24 update."
        )
        ticket.status = "closed"
        return True

    try:
        await bitrix.update_stage(ticket.bitrix_deal_id, settings.bitrix24_stage_closed)
        await redis.delete(fail_key)
        ticket.status = "closed"
        return True
    except Exception:
        fail_count += 1
        await redis.set(fail_key, str(fail_count), ex=_FAILURE_TTL)
        logger.exception(
            f"Failed to auto-close ticket #{ticket.id} in Bitrix24 "
            f"(failure {fail_count}/{_MAX_FAILURES})"
        )
        ticket.status = "resolved"
        return False


async def sync_pending_tickets():
    """Every 5 minutes: find tickets with status='pending_sync' → retry Bitrix24 deal creation.

    On success: update status to 'new', save bitrix_deal_id.
    Processed in parallel batches for performance.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(Ticket).where(Ticket.status == "pending_sync").order_by(Ticket.created_at)
        )
        pending = result.scalars().all()

        if not pending:
            return 0

        bitrix = get_bitrix_service()
        sem = asyncio.Semaphore(_BITRIX_CONCURRENCY)

        async def _limited(ticket):
            async with sem:
                return await _sync_single_ticket(ticket, bitrix)

        results = await asyncio.gather(*[_limited(t) for t in pending])
        synced = sum(1 for r in results if r)

        if synced:
            await session.commit()
            logger.info(f"sync_pending_tickets: synced {synced}/{len(pending)} tickets")
        return synced


async def auto_close_resolved_tickets():
    """Daily: find tickets with status='resolved' and updated_at > 7 days ago → set status='closed'.

    Update B24 deal stage to CLOSED if bitrix_deal_id exists.
    Processed in parallel batches for performance.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.bitrix24_auto_close_days)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Ticket).where(
                Ticket.status == "resolved",
                Ticket.updated_at <= cutoff,
            )
        )
        to_close = result.scalars().all()

        if not to_close:
            return 0

        bitrix = get_bitrix_service()
        sem = asyncio.Semaphore(_BITRIX_CONCURRENCY)

        async def _limited(ticket):
            async with sem:
                return await _close_single_ticket(ticket, bitrix)

        results = await asyncio.gather(*[_limited(t) for t in to_close])
        closed = sum(1 for r in results if r)

        if closed:
            await session.commit()
            logger.info(f"auto_close_resolved_tickets: closed {closed}/{len(to_close)} tickets")
        return closed


_task_handle = None


async def _run_sync_loop():
    """Run sync_pending_tickets every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            synced = await sync_pending_tickets()
            if synced:
                logger.info(f"sync_pending_tickets completed: {synced} synced")
        except Exception:
            logger.exception("Error in sync_pending_tickets")


async def _run_close_loop():
    """Run auto_close_resolved_tickets every 24 hours."""
    while True:
        await asyncio.sleep(86400)
        try:
            closed = await auto_close_resolved_tickets()
            if closed:
                logger.info(f"auto_close_resolved_tickets completed: {closed} closed")
        except Exception:
            logger.exception("Error in auto_close_resolved_tickets")


async def start_background_tasks():
    """Start the background task loops as independent asyncio tasks."""
    global _task_handle
    if settings.app_env == "development":
        logger.info("Background tasks disabled in development mode")
        return

    _task_handle = {
        "sync": asyncio.create_task(_run_sync_loop()),
        "close": asyncio.create_task(_run_close_loop()),
    }
    logger.info("Background tasks started (sync_pending: 5min, auto_close: 24h)")


async def stop_background_tasks():
    """Signal background tasks to stop."""
    global _task_handle
    if _task_handle:
        for task in _task_handle.values():
            task.cancel()
        _task_handle = None
