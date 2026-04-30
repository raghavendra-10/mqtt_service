"""
Database Writer — writes to mqtt_test_readings table on dev DB.

This is a TEST-ONLY writer. It writes all parsed data (inverter + meter +
load + weather) into a single flat table for inspection. Once confirmed
working, switch to the production writer that targets per-device tables.

The INSERT is built dynamically based on which fields are present in the
incoming dict, so adding new register-map fields requires only:
  1. Adding the column to mqtt_test_readings (see database/widen_test_readings_for_3slaves.sql)
  2. Adding the field name to ALLOWED_COLUMNS below.
"""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Optional

import asyncpg
import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("db_writer")
IST = pytz.timezone("Asia/Kolkata")

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool: Optional[asyncpg.Pool] = None


# Whitelist of column names the test table accepts.
# Anything not in this set is dropped (still preserved in raw_payload).
ALLOWED_COLUMNS = {
    # Slave 1 — inverter
    "dailyPowerYield",
    "activePower",
    "frequency",
    "rCurrent",
    "faultId",
    # Slave 2 — meter
    "sCurrent",
    "tCurrent",
    "meterPowerW",
    "loadPowerW",
    # Slave 3 — load / 3-phase
    "phaseA_voltage",
    "phaseB_voltage",
    "phaseC_voltage",
    "phaseA_current",
    "phaseB_current",
    "phaseC_current",
}

INT_COLUMNS = {"faultId"}


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        dsn = DATABASE_URL.replace("?schema=public", "")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        logger.info("DB pool connected")
    return _pool


async def close_pool():
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()


def _parse_ts(ts_str) -> datetime:
    if isinstance(ts_str, datetime):
        dt = ts_str
    else:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = IST.localize(dt)
    return dt


async def save_inverter_reading(data: Dict) -> Optional[str]:
    """
    Write one reading row to mqtt_test_readings.

    Despite the legacy name, this handles inverter / meter / load rows —
    routed by the device_type field (set by event_handler).

    Required keys:  timestamp
    Common keys:    _gateway_uid, _slave_id, inverterSn, device_type
    Field keys:     any subset of ALLOWED_COLUMNS (others go to raw_payload only)
    """
    pool = await get_pool()
    ts = _parse_ts(data.get("timestamp", datetime.now(IST).isoformat()))

    # Fixed columns (always present)
    fixed_cols = ["gateway_uid", "slave_id", "inverter_sn", "timestamp",
                  "device_type", "raw_payload", "source"]
    fixed_vals = [
        data.get("_gateway_uid", "unknown"),
        data.get("_slave_id", 1),
        data.get("inverterSn", ""),
        ts,
        data.get("device_type", "inverter"),
        json.dumps({k: v for k, v in data.items() if not k.startswith("_")}),
        data.get("_source", "gateway"),
    ]

    # Dynamic columns — only those present in `data` AND in ALLOWED_COLUMNS
    dyn_cols = []
    dyn_vals = []
    for col in ALLOWED_COLUMNS:
        if col in data and data[col] is not None:
            dyn_cols.append(f'"{col}"')
            try:
                dyn_vals.append(int(data[col]) if col in INT_COLUMNS else float(data[col]))
            except (ValueError, TypeError):
                logger.warning(f"Bad value for {col}: {data[col]!r} — skipping")

    all_cols = fixed_cols + dyn_cols
    all_vals = fixed_vals + dyn_vals
    placeholders = ", ".join(f"${i+1}" for i in range(len(all_vals)))
    col_list = ", ".join(
        c if c.startswith('"') or c == "raw_payload" else c
        for c in all_cols
    )

    sql = f"""
        INSERT INTO mqtt_test_readings ({col_list})
        VALUES ({placeholders})
        RETURNING id
    """

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *all_vals)
        rid = str(row["id"])
        dev = data.get("device_type", "inverter")
        sn = data.get("inverterSn", "?")
        sid = data.get("_slave_id", "?")
        logger.info(
            f"DB OK | {dev} slave={sid} sn={sn} | "
            f"{len(dyn_cols)} field cols + raw_payload"
        )
        return rid
    except Exception as e:
        logger.error(f"DB write failed: {e} | sql={sql.strip()[:120]}")
        return None


async def save_weather_reading(data: Dict) -> Optional[str]:
    """Write weather data to same test table."""
    pool = await get_pool()
    ts = _parse_ts(data.get("timestamp", datetime.now(IST).isoformat()))

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO mqtt_test_readings
                    (gateway_uid, slave_id, timestamp, device_type, raw_payload, source)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """,
                data.get("_gateway_uid", "unknown"),
                data.get("_slave_id", 1),
                ts,
                "weather",
                json.dumps(data),
                "weather",
            )
        logger.info(f"DB OK | Weather | Irr: {data.get('irradiance', 0)} W/m2")
        return str(row["id"])
    except Exception as e:
        logger.error(f"DB write failed: {e}")
        return None


async def save_mqtt_message(topic: str, payload: Dict, processed: bool = False, error: str = None) -> Optional[str]:
    """Skip message logging in test mode."""
    return None
