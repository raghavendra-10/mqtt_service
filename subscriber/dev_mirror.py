"""
Best-effort mirror of MQTT writes into the DEV database.

The prod write in db_writer stays authoritative and synchronous. AFTER it
succeeds, the same row is mirrored into the dev DB here — FIRE-AND-FORGET:
the mirror runs on its own task and any error/slowness is swallowed and only
logged, so a dev-DB problem can NEVER block or slow prod ingestion.

Gated by the DEV_DATABASE_URL env var:
  - unset/empty  -> completely inert (prod behaviour unchanged)
  - set          -> mirror each inverter_readings / weather_readings write to dev

In-flight mirror tasks are bounded (MAX_INFLIGHT); if dev falls behind, new
mirrors are dropped (counted) rather than piling up unbounded.
"""
import os
import asyncio
import logging
from typing import Optional, Sequence

import asyncpg

logger = logging.getLogger("dev_mirror")

DEV_DATABASE_URL = os.getenv("DEV_DATABASE_URL", "").strip()
MAX_INFLIGHT = int(os.getenv("DEV_MIRROR_MAX_INFLIGHT", "50"))

_dev_pool: Optional[asyncpg.Pool] = None
_inflight = 0
_stats = {"sent": 0, "failed": 0, "dropped": 0}
_last_log = 0.0
# Hold strong refs to in-flight tasks — asyncio only keeps a weak ref, so without
# this a fire-and-forget task can be garbage-collected before it runs.
_tasks: set = set()


def enabled() -> bool:
    return bool(DEV_DATABASE_URL)


async def _get_dev_pool() -> asyncpg.Pool:
    global _dev_pool
    if _dev_pool is None or _dev_pool._closed:
        dsn = DEV_DATABASE_URL.replace("?schema=public", "")
        # short command timeout so a hung dev DB can't tie up a task for long.
        # ssl='prefer' works for RDS (uses TLS when offered, no cert pinning).
        _dev_pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=5, command_timeout=10, timeout=10, ssl="prefer"
        )
        logger.info("dev-mirror pool connected")
    return _dev_pool


async def close_dev_pool():
    global _dev_pool
    if _dev_pool and not _dev_pool._closed:
        await _dev_pool.close()


def mirror(sql: str, args: Sequence):
    """Schedule a best-effort mirror of one write into the dev DB. Non-blocking;
    safe no-op when DEV_DATABASE_URL is unset."""
    global _inflight
    if not enabled():
        return
    if _inflight >= MAX_INFLIGHT:
        _stats["dropped"] += 1
        return
    _inflight += 1
    t = asyncio.create_task(_run(sql, tuple(args)))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _run(sql: str, args: tuple):
    global _inflight
    try:
        pool = await _get_dev_pool()
        await pool.execute(sql, *args)
        _stats["sent"] += 1
    except Exception as e:
        _stats["failed"] += 1
        # keep this quiet-ish; dev outages shouldn't spam prod logs
        logger.warning(f"dev-mirror write failed (swallowed): {str(e)[:120]}")
    finally:
        _inflight -= 1
        _maybe_log_stats()


def _maybe_log_stats():
    global _last_log
    import time
    now = time.time()
    if now - _last_log >= 300:  # every 5 min
        _last_log = now
        logger.info(f"dev-mirror stats: {_stats} inflight={_inflight}")
