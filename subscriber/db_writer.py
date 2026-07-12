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


def _parse_dtm_as_utc(dtm: str) -> Optional[datetime]:
    """Parse gateway dtm (IST) → naive UTC datetime for inverter_readings."""
    if not dtm or len(str(dtm)) < 14:
        return None
    try:
        dt = datetime.strptime(str(dtm)[:14], "%Y%m%d%H%M%S")
        dt_ist = IST.localize(dt)       # Gateway sends IST
        dt_utc = dt_ist.astimezone(pytz.utc)
        return dt_utc.replace(tzinfo=None)  # Naive UTC for storage
    except ValueError:
        return None


def _parse_dtm(dtm: str) -> Optional[datetime]:
    if not dtm or len(str(dtm)) < 14:
        return None
    try:
        dt = datetime.strptime(str(dtm)[:14], "%Y%m%d%H%M%S")
        return IST.localize(dt)  # dtm is IST — localize directly
    except ValueError:
        return None


async def save_production_reading(inverter_id: str, inverter_sn: str,
                                  dtm: str, data: Dict, sid: int = None) -> Optional[str]:
    """
    Write a scaled reading to the production inverter_readings table.

    Maps raw WT410M gateway JSONB fields to production columns with scaling.
    The gateway outputs values in engineering units (V, A, W, kWh, Hz).
    Power values (W) are converted to kW for storage.
    """
    pool = await get_pool()
    # Gateway sends dtm in IST. Parse as IST, convert to UTC (naive) for storage.
    ts = _parse_dtm_as_utc(dtm)
    if ts is None:
        ts = datetime.utcnow()

    # Helper to safely extract and scale a numeric value.
    # Tries new (abbreviated) key first, falls back to old (verbose) key.
    def val(new_key, scale=1.0, old_key=None):
        v = data.get(new_key)
        if v is None and old_key:
            v = data.get(old_key)
        if v is None:
            return None
        try:
            return float(v) * scale
        except (ValueError, TypeError):
            return None

    # Map raw JSONB → production columns
    # Supports both new abbreviated labels (DlyPowYield) and old verbose labels (Daily power yields)
    # Power: gateway outputs in Watts → store in kW (÷1000)
    # Voltage/Current/Freq/PF: already in engineering units
    # Energy: already in kWh
    reading = {
        "activePower": val("TotActPow", 0.001, "Total active power"),       # W → kW
        "dailyPowerYield": val("DlyPowYield", 1.0, "Daily power yields"),   # kWh
        "totalPowerYield": val("TotPowYield", 1.0, "Total power yields"),   # kWh
        "totalDcPower": val("TotDCPow", 0.001, "Total DC power"),           # W → kW
        "totalActivePower": val("TotActPow", 0.001, "Total active power"),  # W → kW
        "reactivePowerKvar": val("TotReaPow", 0.001, "Total reactive pow"), # W → kVAR
        "frequency": val("Gridfreq", 1.0, "Grid frequency"),               # Hz
        "powerFactor": val("PowFactor", 1.0, "Power factor"),               # 0-1
        "faultId": int(data.get("FalCode", data.get("Fault Code", 0)) or 0),
        # Active power ratio / power-limit setpoint (%). Sungrow "PowLimitSet".
        # Not sent by all OEMs — stays None when absent.
        "activePowerRatio": val("PowLimitSet", 1.0, "Active power ratio"),
        # AC voltages (V)
        "ryAcVolt": val("ABlineVol", 1.0, "A-B line voltage/p"),
        "ybAcVolt": val("BClineVol", 1.0, "B-C line Voltage/p"),
        "brAcVolt": val("CAlineVol", 1.0, "C-A line Voltage/p"),
        # AC currents (A)
        "rCurrent": val("PhaseACur", 1.0, "Phase A current"),
        "yCurrent": val("PhaseBCur", 1.0, "Phase B current"),
        "bCurrent": val("PhaseCCur", 1.0, "Phase C current"),
        # Internal temperature (°C)
        "internalTemperature": val("IntTemp", 1.0, "Internal temperatu"),
        # Work state (0/33280=Generating, 5120=Standby, 4608=Starting, 5632=Shutting down)
        "workState": int(data.get("Workstate", data.get("Work state", 0)) or 0),
    }

    # MPPT voltage and current (up to 12 for SG320HX)
    for i in range(1, 21):
        v = val(f"MPPT{i}Vol", 1.0, f"MPPT {i} voltage")
        c = val(f"MPPT{i}Cur", 1.0, f"MPPT {i} current")
        if v is not None:
            reading[f"mppt{i}Voltage"] = v
        if c is not None:
            reading[f"mppt{i}Current"] = c

    # String currents (up to 24 for SG320HX)
    for i in range(1, 41):
        c = val(f"Str{i}Cur", 1.0, f"String {i} current")
        if c is not None:
            reading[f"pv{i}Current"] = c

    # Build dynamic INSERT — only include non-None fields
    cols = ['"inverterId"', '"inverterSn"', '"timestamp"']
    vals = [inverter_id, inverter_sn, ts]

    if sid is not None:
        cols.append('sid')
        vals.append(sid)

    for col, v in reading.items():
        if v is not None:
            cols.append(f'"{col}"')
            vals.append(v)

    placeholders = ", ".join(f"${i+1}" for i in range(len(vals)))
    col_list = ", ".join(cols)

    sql = f"""
        INSERT INTO inverter_readings (id, {col_list})
        VALUES (gen_random_uuid(), {placeholders})
        RETURNING id
    """

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *vals)
        return str(row["id"])
    except Exception as e:
        logger.error(f"Production write failed: {e}")
        return None


## ---- Weather: Physical sensor (direct) + Open-Meteo fallback ----

_last_weather_cycle: Optional[str] = None  # Last dtm cycle we fetched weather for (Open-Meteo)
_cached_weather: Optional[dict] = None     # Cached Open-Meteo response (reused within 15 min)
_cache_time: Optional[datetime] = None

# Track last sensor write timestamp — used to skip Open-Meteo when sensor is live
_last_sensor_ts: Optional[datetime] = None

# Per-sensor dedup: keyed by "{plant_id}:{sensor_id}" → last cycle_key saved
# Allows SID 30 (temp) and SID 91 (irr) to each save once per minute independently
_sensor_cycles: Dict[str, str] = {}

# Open-Meteo cache: reuse wind + ambient temperature response for 15 minutes
_wind_cache: Optional[float] = None
_wind_cache_time: Optional[datetime] = None
_wind_cache_minute: Optional[str] = None  # cycle_key of last update
_openmeteo_temp_cache: Optional[float] = None


async def save_sensor_weather_reading(plant_id: str, reading_ts: Optional[datetime],
                                       sensor_id: str = "default",
                                       temperature: Optional[float] = None,
                                       irradiance: Optional[float] = None,
                                       wind_speed: Optional[float] = None,
                                       body_temperature: Optional[float] = None) -> Optional[str]:
    """
    Save weather data from a physical on-site (datalogger) sensor to weather_readings.
    `temperature` (SID 30 gateway reading) is ground truth for the module-area
    sensor and is stored in moduleTemperature — it runs hot in direct sun
    (60-70C) and is NOT true ambient air temperature. ambientTemperature is
    owned by the Open-Meteo follow-up call (fetch_openmeteo_and_update) and is
    never overwritten here — a placeholder is inserted only if the row is new.
    Deduplicates per minute per sensor (SID 30 and SID 91 each save independently).
    """
    global _last_sensor_ts, _sensor_cycles

    now = datetime.utcnow()
    save_ts = reading_ts if reading_ts else now
    # Truncate to the minute so SID 30 (temp) and SID 91 (irradiance) — which
    # arrive in separate MQTT messages a few seconds apart — both resolve to
    # the same timestamp and the UPSERT merges them into one row.
    save_ts = save_ts.replace(second=0, microsecond=0)

    cycle_key = save_ts.strftime("%Y%m%d%H%M")
    dedup_key = f"{plant_id}:{sensor_id}"
    if _sensor_cycles.get(dedup_key) == cycle_key:
        return None  # Already saved for this sensor+minute

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO weather_readings
                    (id, "plantId", timestamp, irradiance, "ambientTemperature", "moduleTemperature", "windSpeed", "bodyTemperature")
                VALUES (gen_random_uuid(), $1, $2, $3, 0.0, $4, $5, $6)
                ON CONFLICT ("plantId", timestamp)
                DO UPDATE SET
                    irradiance          = GREATEST(EXCLUDED.irradiance,          weather_readings.irradiance),
                    "moduleTemperature" = GREATEST(EXCLUDED."moduleTemperature", weather_readings."moduleTemperature"),
                    "windSpeed"         = COALESCE(EXCLUDED."windSpeed",         weather_readings."windSpeed"),
                    "bodyTemperature"   = COALESCE(EXCLUDED."bodyTemperature",   weather_readings."bodyTemperature")
                RETURNING id
                """,
                plant_id,
                save_ts,
                float(irradiance) if irradiance is not None else 0.0,
                float(temperature) if temperature is not None else 0.0,
                float(wind_speed) if wind_speed is not None else None,
                float(body_temperature) if body_temperature is not None else None,
            )
        _sensor_cycles[dedup_key] = cycle_key
        _last_sensor_ts = now
        logger.info(
            f"[SENSOR] plant={plant_id[:8]} | sensor={sensor_id} | "
            f"GHI={irradiance} W/m² | ModuleTemp={temperature}°C | BodyTemp={body_temperature}°C [physical sensor]"
        )
        return str(row["id"])
    except Exception as e:
        logger.error(f"Sensor weather write failed: {e}")
        return None


async def fetch_openmeteo_and_update(plant_id: str, lat: float, lon: float, save_ts: datetime) -> None:
    """
    Fetch wind speed + ambient temperature from Open-Meteo and UPDATE the
    existing weather_readings row for this plant+minute.
    Called after save_sensor_weather_reading. ambientTemperature is owned
    exclusively by this function — the datalogger's own temperature reading
    goes to moduleTemperature instead (it runs hot in direct sun, not
    representative of true air temperature).
    Cached for 15 minutes — only one API call per 15-min window.
    """
    global _wind_cache, _wind_cache_time, _wind_cache_minute
    global _openmeteo_temp_cache

    now = datetime.utcnow()
    cycle_key = save_ts.strftime("%Y%m%d%H%M")

    # Only fetch once per row (per minute per plant)
    if _wind_cache_minute == cycle_key:
        return

    # Reuse cached values if within 15 minutes
    need_fetch = (
        _wind_cache is None or
        _wind_cache_time is None or
        (now - _wind_cache_time).total_seconds() >= 900
    )

    if need_fetch:
        try:
            import aiohttp
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=wind_speed_10m,temperature_2m"
                f"&timezone=auto"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[WEATHER-API] Open-Meteo HTTP {resp.status}")
                        return
                    data = await resp.json()

            current = data.get("current", {})
            raw_wind = current.get("wind_speed_10m")
            raw_temp = current.get("temperature_2m")
            if raw_wind is None and raw_temp is None:
                logger.warning("[WEATHER-API] Open-Meteo returned no wind/temperature")
                return

            # Open-Meteo returns km/h → convert to m/s
            if raw_wind is not None:
                _wind_cache = round(float(raw_wind) / 3.6, 2)
            if raw_temp is not None:
                _openmeteo_temp_cache = round(float(raw_temp), 2)
            _wind_cache_time = now
            logger.info(f"[WEATHER-API] refresh: wind={raw_wind} km/h → {_wind_cache} m/s, temp={_openmeteo_temp_cache}°C")

        except Exception as e:
            logger.error(f"[WEATHER-API] Fetch failed: {e}")
            return

    wind_speed = _wind_cache
    ambient_temp = _openmeteo_temp_cache

    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE weather_readings
                SET "windSpeed" = COALESCE($1, "windSpeed"),
                    "ambientTemperature" = COALESCE($2, "ambientTemperature")
                WHERE "plantId" = $3 AND timestamp = $4
                """,
                wind_speed, ambient_temp, plant_id, save_ts,
            )
        _wind_cache_minute = cycle_key
        logger.info(f"[WEATHER-API] Updated plantId={plant_id[:8]} ts={save_ts} wind={wind_speed} m/s ambientTemp={ambient_temp}°C")
    except Exception as e:
        logger.error(f"[WEATHER-API] DB update failed: {e}")


async def fetch_and_save_weather(plant_id: str, lat: float, lon: float,
                                  reading_ts: Optional[datetime] = None,
                                  gateway_temperature: Optional[float] = None) -> Optional[str]:
    """
    Fetch irradiance from Open-Meteo and save to weather_readings.
    Skipped automatically when a physical sensor wrote data within the last 10 minutes.
    If gateway_temperature is provided it replaces Open-Meteo temperature.
    """
    global _last_sensor_ts
    # Skip Open-Meteo if physical sensor is live (data within last 10 min)
    if _last_sensor_ts is not None and (datetime.utcnow() - _last_sensor_ts).total_seconds() < 600:
        logger.debug("[WEATHER] Skipping Open-Meteo — physical sensor is live")
        return None
    global _last_weather_cycle, _cached_weather, _cache_time

    now = datetime.utcnow()

    # Use inverter reading timestamp if provided, otherwise current UTC
    save_ts = reading_ts if reading_ts else now

    # Deduplicate per cycle: only one weather save per minute
    cycle_key = save_ts.strftime("%Y%m%d%H%M") if save_ts else None
    if cycle_key == _last_weather_cycle:
        return None

    # Check if this is buffered/flushed data (dtm is more than 5 min in the past)
    is_historical = (now - save_ts).total_seconds() > 300 if save_ts else False

    # Cache API call: reuse response within 15 min, only call API when cache expires
    # For historical data: always fetch (different timestamp = different weather)
    need_fetch = (is_historical or _cached_weather is None or _cache_time is None or
                  (now - _cache_time).total_seconds() >= 900)

    if need_fetch:
        import aiohttp

        if is_historical:
            # Buffered data — fetch weather for the dtm time using hourly historical
            # Convert save_ts (UTC) to IST for the API
            save_ist = save_ts + timedelta(hours=5, minutes=30)
            date_str = save_ist.strftime('%Y-%m-%d')
            hour = save_ist.hour
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&hourly=shortwave_radiation,temperature_2m,wind_speed_10m"
                f"&start_date={date_str}&end_date={date_str}"
                f"&timezone=Asia/Kolkata"
            )
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            logger.error(f"Open-Meteo historical HTTP {resp.status}")
                            return None
                        data = await resp.json()

                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                ghi_list = hourly.get("shortwave_radiation", [])
                temp_list = hourly.get("temperature_2m", [])
                wind_list = hourly.get("wind_speed_10m", [])

                # Find the closest hour
                irradiance = ghi_list[hour] if hour < len(ghi_list) else 0
                temperature = temp_list[hour] if hour < len(temp_list) else 30.0
                wind_speed = wind_list[hour] if hour < len(wind_list) else 0
                if wind_speed is not None:
                    wind_speed = round(wind_speed / 3.6, 2)

                logger.info(f"[WEATHER] Historical fetch for {save_ist.strftime('%H:%M')} IST: GHI={irradiance}")

            except Exception as e:
                logger.error(f"Weather historical fetch failed: {e}")
                return None
        else:
            # Real-time — fetch current weather
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&current=shortwave_radiation,temperature_2m,wind_speed_10m"
                f"&timezone=auto"
            )
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            logger.error(f"Open-Meteo HTTP {resp.status}")
                            return None
                        data = await resp.json()

                current = data.get("current", {})
                irradiance = current.get("shortwave_radiation")
                temperature = current.get("temperature_2m")
                wind_speed = current.get("wind_speed_10m")

            except Exception as e:
                logger.error(f"Weather fetch failed: {e}")
                return None

        if irradiance is None:
            logger.warning("Open-Meteo returned no irradiance")
            return None

        if wind_speed is not None and isinstance(wind_speed, (int, float)):
            wind_speed = round(wind_speed / 3.6, 2)  # km/h → m/s (Open-Meteo always returns km/h)

        _cached_weather = {"irradiance": irradiance, "temperature": temperature, "wind_speed": wind_speed}
        _cache_time = now
        logger.info(f"[WEATHER] API refresh: GHI={irradiance} W/m²")

    irradiance = _cached_weather["irradiance"]
    wind_speed = _cached_weather["wind_speed"]

    # Use gateway sensor temperature if available, otherwise fall back to Open-Meteo
    if gateway_temperature is not None:
        temperature = gateway_temperature
        temp_source = "gateway"
    else:
        temperature = _cached_weather["temperature"]
        temp_source = "open-meteo"

    try:
        pool = await get_pool()
        sql = """
            INSERT INTO weather_readings (id, "plantId", timestamp, irradiance, "ambientTemperature", "windSpeed")
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5)
            RETURNING id
        """
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, plant_id, save_ts, irradiance, temperature, wind_speed)

        _last_weather_cycle = cycle_key
        logger.info(
            f"[WEATHER] plant={plant_id[:8]} | "
            f"GHI={irradiance:.0f} W/m² | Temp={temperature:.1f}°C [{temp_source}] | Wind={wind_speed:.1f} m/s"
        )
        return str(row["id"])

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        return None


async def save_inverter_log(kind: str, gateway_imei: str, topic: str,
                            uid, sid, dtm, seq, data: Dict) -> Optional[str]:
    """
    Insert one modbus entry into either inverter_full_log or inverter_realtime_log.

    kind: 'full' or 'realtime' — selects the table.
    data: the modbus[i] dict (gateway register fields, JSONB).
    """
    table = "inverter_full_log" if kind == "full" else "inverter_realtime_log"

    pool = await get_pool()
    ts = _parse_dtm(dtm)

    sql = f"""
        INSERT INTO {table} (gateway_imei, uid, sid, dtm, seq, topic, data)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                sql,
                gateway_imei,
                int(uid) if uid is not None else None,
                int(sid) if sid is not None else None,
                ts,
                int(seq) if seq is not None else None,
                topic,
                json.dumps(data),
            )
        return str(row["id"])
    except Exception as e:
        logger.error(f"DB write failed ({table}): {e}")
        return None
