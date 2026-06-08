# MQTT Service — Complete Documentation

## Overview

The MQTT service is the data ingestion pipeline for GoodEnergies solar plant monitoring. It receives real-time 1-minute inverter readings from WT410M dataloggers via MQTT, processes and stores them in PostgreSQL.

## Architecture

```
WT410M Datalogger (Sivakasi)
    │
    │  MQTT (1 msg/inverter/min)
    │  Topic: iot1/{IMEI}/event/
    ▼
Mosquitto Broker (184.33.25.240:1883)
    │
    ▼
mqtt-subscriber (Python service on same server)
    ├── event_handler.py  → Parse, clean, route
    ├── db_writer.py      → Store in PostgreSQL
    │     ├── inverter_realtime_log  (raw JSONB)
    │     ├── inverter_readings      (production, scaled)
    │     └── weather_readings       (Open-Meteo API)
    │
    ▼
PostgreSQL (AWS RDS)
    │
    ▼
Backend Server (100.23.111.31)
    ├── event_driven_listener.py  → PG NOTIFY trigger
    ├── Yield Curve Calculator
    ├── PR Calculator (1-min + 15-min)
    ├── PLF Calculator
    └── Alert Processor
```

## Infrastructure

| Component | Host | Details |
|-----------|------|---------|
| MQTT Broker | 184.33.25.240 | Mosquitto, port 1883 |
| MQTT Subscriber | 184.33.25.240 | Python service, systemd `mqtt-subscriber` |
| PostgreSQL | AWS RDS (us-west-2) | `goodenergies` database |
| Backend API | 100.23.111.31 | FastAPI + event listener, supervisor managed |
| Datalogger | WT410M | IMEI: 860710086613836, Sivakasi plant |

## Datalogger Details

**Gateway:** WT410M (Modbus-to-MQTT bridge)
**IMEI:** 860710086613836
**Plant:** Sivakasi (Plant ID: `4e830dca-ee75-46eb-9799-8ce5cbb573ce`)
**Location:** 9.4729°N, 77.7047°E
**Inverters:** 8 × Sungrow SG320HX (320 kW AC each)
**Poll Interval:** 1 minute

### Modbus Slave IDs (SID)

| SID | Inverter SN | Inverter UUID | DC Capacity |
|-----|-------------|---------------|-------------|
| 1 | 1 | 42de11b3-... | 387 kWp |
| 7 | 7 | f71493d2-... | 387 kWp |
| 8 | 8 | 1d83b267-... | 387 kWp |
| 9 | 9 | 4b97e3d2-... | 387 kWp |
| 11 | 11 | b4bb3b70-... | 387 kWp |
| 12 | 12 | 1050deec-... | 387 kWp |
| 13 | 13 | edbd3f28-... | 387 kWp |
| 16 | 16 | 5c151f36-... | 387 kWp |

SID mapping is configured in `config/device_registry.json`.

## MQTT Payload Format

The datalogger sends one MQTT message per inverter per minute on topic `iot1/{IMEI}/event/`.

### Payload Structure

```json
{
  "data": {
    "imei": "860710086613836",
    "uid": 1,
    "dtm": "20260520124200",
    "seq": 7672,
    "msg": "log",
    "modbus": [{
      "sid": 1,
      "stat": 0,
      "rcnt": 76,
      "DlyPowYield": 1098.9,
      "TotPowYield": 1248378,
      "TotActPow": 234690,
      "TotDCPow": 237444,
      ...
    }]
  }
}
```

**Key envelope fields:**
- `imei` — Gateway identifier (used to lookup device_registry)
- `dtm` — Timestamp in IST, format: `YYYYMMDDHHmmSS`
- `seq` — Sequence number (monotonic)
- `sid` — Modbus slave ID (identifies which inverter)
- `stat` — 0 = success, non-zero = communication error (skipped)

### Register Labels (New Format — May 2026)

The datalogger was reconfigured with abbreviated labels and decimal precision.
The code supports **both old and new labels** for backward compatibility.

| New Label | Old Label | Value | Unit | Notes |
|-----------|-----------|-------|------|-------|
| `DlyPowYield` | `Daily power yields` | 1098.9 | kWh | Daily cumulative, resets ~00:13 IST |
| `TotPowYield` | `Total power yields` | 1248378 | kWh | Lifetime total |
| `TotActPow` | `Total active power` | 234690 | **W** | Active power (Watts) |
| `TotDCPow` | `Total DC power` | 237444 | **W** | DC input power (Watts) |
| `TotReaPow` | `Total reactive pow` | 2 | **W** | Reactive power (Watts) |
| `TotAptPow` | `Total apparent pow` | 234690 | W | Apparent power (not stored) |
| `Gridfreq` | `Grid frequency` | 49.9 | Hz | |
| `PowFactor` | `Power factor` | 1.000 | - | 0-1 range |
| `IntTemp` | `Internal temperatu` | 62.3 | °C | Inverter cabinet temp |
| `Workstate` | `Work state` | 33280 | - | See work state table below |
| `FalCode` | `Fault Code` | 0 | - | 0 = no fault |
| `ABlineVol` | `A-B line voltage/p` | 771.2 | V | Line-to-line voltage |
| `BClineVol` | `B-C line Voltage/p` | 773.6 | V | |
| `CAlineVol` | `C-A line Voltage/p` | 771.2 | V | |
| `PhaseACur` | `Phase A current` | 174.4 | A | |
| `PhaseBCur` | `Phase B current` | 174.7 | A | |
| `PhaseCCur` | `Phase C current` | 174.6 | A | |
| `MPPT1Vol` | `MPPT 1 voltage` | 1151.8 | V | MPPT 1-12 supported |
| `MPPT1Cur` | `MPPT 1 current` | 17.8 | A | |
| `Str1Cur` | `String 1 current` | 8.74 | A | String 1-24 supported |
| `MonPowYield` | `Monthlypower yeild` | 28887.7 | kWh | Monthly total (not stored) |
| `TotRunTime` | `Total running time` | 9618 | hours | (not stored) |
| `reserved1/2/3` | `reserved` | - | - | Binary garbage, stripped |
| `FalYear/Month/Day/Hour/Minute/Second` | `Fault Year/Month/...` | 0 | - | (not stored) |

### Work State Values

| Value | Label | Description |
|-------|-------|-------------|
| 0 | Generating | Normal operation (some inverters use this) |
| 33280 | Generating | Normal operation (most inverters use this) |
| 5120 | Standby | Night/sleep mode, no production |
| 4608 | Starting | Inverter attempting to start |
| 5632 | Shutting Down | Inverter shutting down |
| 21760 | Shutdown/Fault | Shutdown with fault condition |

**Typical daily pattern:**
- ~00:20 IST: Enters Standby (5120) after sunset
- ~11:00-11:30 IST: Attempts startup (4608 → 5120 → 4608 → 0/33280)
- ~11:30 IST - 00:00 IST: Generating (0 or 33280)

## Data Processing Pipeline

### Step 1: Payload Cleaning (`event_handler.py`)

Before JSON parsing, the raw payload is cleaned:

1. **Binary filter** — Strip non-printable bytes (gateway sends raw binary in `reserved` fields)
2. **Reserved field strip** — `"reserved":"<binary>"` → `"reserved":""`
3. **Truncated field fix** — Gateway firmware bug drops field names, leaving orphaned values like `"String 23 current":0,0`. Fixed with regex: `re.sub(r',\s*(-?\d+)\s*\}', r' }', text)`

### Step 2: Device Routing (`event_handler.py`)

1. Parse `imei` and `sid` from payload
2. Lookup `device_registry.json`: `IMEI → SID → {plant_id, inverter_id, inverter_sn, type}`
3. If `type == "inverter"` → call `save_production_reading()`
4. First inverter in each cycle → also call `fetch_and_save_weather()`

### Step 3: Timestamp Handling

The gateway sends `dtm` in **IST** (e.g., `20260520124200` = 12:42:00 IST).

Conversion in `_parse_dtm_as_utc()`:
```
dtm "20260520124200"
→ Parse as IST: 2026-05-20 12:42:00 IST
→ Convert to UTC: 2026-05-20 07:12:00 UTC
→ Store as naive UTC (no timezone info in DB)
```

### Step 4: Field Mapping & Conversion (`save_production_reading()`)

Only **power fields** are converted (Watts → kilowatts). Everything else stored as-is.

| DB Column | Source Field | Conversion |
|-----------|-------------|------------|
| `activePower` | `TotActPow` (or `Total active power`) | **÷ 1000** (W→kW) |
| `totalActivePower` | `TotActPow` | **÷ 1000** |
| `totalDcPower` | `TotDCPow` (or `Total DC power`) | **÷ 1000** |
| `reactivePowerKvar` | `TotReaPow` (or `Total reactive pow`) | **÷ 1000** |
| `dailyPowerYield` | `DlyPowYield` (or `Daily power yields`) | None (already kWh) |
| `totalPowerYield` | `TotPowYield` (or `Total power yields`) | None (already kWh) |
| `frequency` | `Gridfreq` (or `Grid frequency`) | None (Hz) |
| `powerFactor` | `PowFactor` (or `Power factor`) | None (0-1) |
| `faultId` | `FalCode` (or `Fault Code`) | None (int) |
| `workState` | `Workstate` (or `Work state`) | None (int) |
| `ryAcVolt` | `ABlineVol` (or `A-B line voltage/p`) | None (V) |
| `ybAcVolt` | `BClineVol` (or `B-C line Voltage/p`) | None (V) |
| `brAcVolt` | `CAlineVol` (or `C-A line Voltage/p`) | None (V) |
| `rCurrent` | `PhaseACur` (or `Phase A current`) | None (A) |
| `yCurrent` | `PhaseBCur` (or `Phase B current`) | None (A) |
| `bCurrent` | `PhaseCCur` (or `Phase C current`) | None (A) |
| `internalTemperature` | `IntTemp` (or `Internal temperatu`) | None (°C) |
| `mppt{N}Voltage` | `MPPT{N}Vol` (or `MPPT {N} voltage`) | None (V) |
| `mppt{N}Current` | `MPPT{N}Cur` (or `MPPT {N} current`) | None (A) |
| `pv{N}Current` | `Str{N}Cur` (or `String {N} current`) | None (A) |
| `sid` | From MQTT payload `sid` field | None (int) |

The `val()` helper tries the **new abbreviated key first**, falls back to the **old verbose key**, ensuring backward compatibility.

### Step 5: Database Insert

Dynamic INSERT into `inverter_readings` — only non-None fields are included.
Each reading gets a UUID (`gen_random_uuid()`).

### Step 6: Weather Fetch (`fetch_and_save_weather()`)

Called once per minute cycle (deduplicated by cycle key).

**Source:** Open-Meteo Forecast API (free, no API key)
**URL:** `https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=shortwave_radiation,temperature_2m,wind_speed_10m`
**Cache:** API response cached for 15 minutes (Open-Meteo's resolution)
**Storage:** One row per minute in `weather_readings` with irradiance, temperature, wind speed.

**Historical data:** If the reading timestamp is >5 minutes old (flushed/buffered data), fetches hourly historical data from Open-Meteo instead of current.

## Flushed Data Handling

When the MQTT connection breaks (server restart, network issue), the WT410M gateway **buffers readings locally**. When reconnected, it **flushes** all buffered readings with their original timestamps.

**Detection:** `delay_sec = (now - reading_ts) > 300` (>5 min old = flushed)

**Handling:**
1. Track flush range per inverter: `{inverter_id: (min_ts, max_ts)}`
2. Every 60s, trigger recalculation:
   - Delete old yield_curve and PR records for the flushed range
   - Send PG NOTIFY with `flush_recalc: true` flag
   - Event listener on backend re-runs calculations for the gap

## dailyPowerYield Counter Behavior

The `dailyPowerYield` (formerly `Daily power yields`) is a **cumulative counter** from the inverter:

- Counts up throughout the day (0 → ~2000 kWh for SG320HX)
- **Resets to 0 at ~00:13 IST** (not exactly midnight)
- Before reset, holds **yesterday's final value** for ~13 minutes
- With new firmware: **decimal precision** (e.g., 1098.9 kWh, was integer 1099 before)

**Important for calculations:**
- `MAX(dailyPowerYield)` must exclude pre-reset stale values
- The reset point is detected as `first reading WHERE dailyPowerYield = 0`
- Actual energy = MAX after reset point (not raw MAX of the day)

## Database Tables

### inverter_readings (production)
One row per inverter per minute. All scaled values.
- Indexes: `inverterId`, `inverterSn`, `timestamp`

### inverter_realtime_log (raw archive)
Full JSONB payload as received from gateway.
- Indexes: `(gateway_imei, dtm DESC)`, `(gateway_imei, seq)`

### weather_readings
One row per minute for the plant (Open-Meteo data).
- Columns: `plantId`, `timestamp`, `irradiance`, `ambientTemperature`, `windSpeed`

## Service Management

### MQTT Server (184.33.25.240)

```bash
# SSH
ssh -i goodenergies.pem ubuntu@184.33.25.240

# Service status
sudo systemctl status mqtt-subscriber

# View logs
sudo journalctl -u mqtt-subscriber --no-pager --since '5 minutes ago'

# View raw payloads
sudo journalctl -u mqtt-subscriber --no-pager --since '2 minutes ago' | grep '\[RAW\]'

# View production writes
sudo journalctl -u mqtt-subscriber --no-pager --since '2 minutes ago' | grep '\[PROD\]'

# View errors
sudo journalctl -u mqtt-subscriber --no-pager --since '1 hour ago' | grep -i error

# Restart
sudo systemctl restart mqtt-subscriber

# Mosquitto broker logs
sudo tail -30 /var/log/mosquitto/mosquitto.log
```

### Backend Server (100.23.111.31)

```bash
# SSH
ssh -i goodenergies.pem ubuntu@100.23.111.31

# Services (managed by supervisor)
sudo supervisorctl status

# Key services:
#   goodenergies              — FastAPI backend (port 8000)
#   goodenergies_event_listener — PG NOTIFY listener, runs calculation jobs

# Restart API
sudo supervisorctl restart goodenergies

# Restart event listener
sudo supervisorctl restart goodenergies_event_listener

# View event listener logs
tail -50 /var/log/goodenergies/event_listener.out.log
```

## File Structure

```
mqtt_service/
├── main.py                    # Entry point (python main.py subscribe)
├── config/
│   └── device_registry.json   # IMEI:SID → inverter mapping
├── subscriber/
│   ├── mqtt_subscriber.py     # MQTT client, subscribes to topics
│   ├── event_handler.py       # Message parsing, routing, flush detection
│   └── db_writer.py           # DB writes: readings, weather, raw logs
├── gateway/                   # WT410M parser (for structured gateway messages)
├── simulator/                 # Test data generator
├── api/                       # Local test API
├── calculator/                # Local yield/PR calculator (test only)
└── docs/                      # Additional documentation
```

## Known Issues & Fixes

### Gateway Firmware Bugs (handled in event_handler.py)

1. **`reserved` field contains binary garbage** — Stripped before JSON parsing with printable-byte filter + regex
2. **Truncated field names** — `"String 23 current":0,0}` (missing next field) → Fixed with regex `re.sub(r',\s*(-?\d+)\s*\}', r' }', text)`
3. **Duplicate `reserved` keys** — Old format had 3 fields all named `"reserved"`. New format uses `reserved1`, `reserved2`, `reserved3`

### Counter Reset Issue

`MAX(dailyPowerYield)` picks up yesterday's stale value (before ~00:13 IST reset), inflating energy/PR calculations. Fixed in `yield_curve_service.py` with reset-aware query that finds the first zero reading and takes MAX only after that point.

### PR Cumulative State Loss on Job Restart

The PR 15-min calculator tracks cumulative state (`baseline_yield`, `cum_actual`, etc.) in memory. When the job restarts, this state is lost, causing `cumulativeActualEnergy = 0`. Fixed by recovering state from the last existing PR 15-min record for the current IST day before processing new readings.
