-- ============================================================
-- Migration: Widen mqtt_test_readings for 3-slave simulator
-- ============================================================
-- The simulator (modbus_simulator/inverter_sim.py) now emits 3 slaves:
--   slave 1 = inverter   (existing columns)
--   slave 2 = meter      (3-phase currents + meterPower + loadPower)
--   slave 3 = load       (3-phase voltages & currents)
--
-- This migration:
--   1. Ensures the base mqtt_test_readings table exists.
--   2. Adds columns for slave 2 + slave 3 fields.
--   3. Adds device_type to distinguish rows (inverter / meter / load).
--   4. DELETES all existing rows (per user request — clean slate).
--
-- Run:
--   psql "$DATABASE_URL" -f mqtt_service/database/widen_test_readings_for_3slaves.sql
--
-- Idempotent: safe to re-run (uses IF EXISTS / IF NOT EXISTS guards).
-- ============================================================

BEGIN;

-- 1. Ensure base table exists (no-op if already there).
-- This matches the columns currently used by db_writer.py.
CREATE TABLE IF NOT EXISTS mqtt_test_readings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gateway_uid     VARCHAR(64),
    slave_id        INT,
    inverter_sn     VARCHAR(100),
    timestamp       TIMESTAMPTZ NOT NULL,
    "dailyPowerYield" FLOAT,
    "activePower"   FLOAT,
    frequency       FLOAT,
    "rCurrent"      FLOAT,
    "faultId"       INT,
    raw_payload     JSONB,
    source          VARCHAR(32),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Add new columns (idempotent — IF NOT EXISTS requires PG 9.6+).
ALTER TABLE mqtt_test_readings
    ADD COLUMN IF NOT EXISTS device_type      VARCHAR(20),       -- 'inverter' | 'meter' | 'load'
    -- slave 2 (meter) extras
    ADD COLUMN IF NOT EXISTS "sCurrent"       FLOAT,
    ADD COLUMN IF NOT EXISTS "tCurrent"       FLOAT,
    ADD COLUMN IF NOT EXISTS "meterPowerW"    FLOAT,             -- combined uint32
    ADD COLUMN IF NOT EXISTS "loadPowerW"     FLOAT,             -- combined uint32
    -- slave 3 (load / 3-phase)
    ADD COLUMN IF NOT EXISTS "phaseA_voltage" FLOAT,
    ADD COLUMN IF NOT EXISTS "phaseB_voltage" FLOAT,
    ADD COLUMN IF NOT EXISTS "phaseC_voltage" FLOAT,
    ADD COLUMN IF NOT EXISTS "phaseA_current" FLOAT,
    ADD COLUMN IF NOT EXISTS "phaseB_current" FLOAT,
    ADD COLUMN IF NOT EXISTS "phaseC_current" FLOAT;

-- 3. Helpful indexes (skip if already present).
CREATE INDEX IF NOT EXISTS idx_mqtt_test_readings_timestamp ON mqtt_test_readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_mqtt_test_readings_gateway   ON mqtt_test_readings(gateway_uid);
CREATE INDEX IF NOT EXISTS idx_mqtt_test_readings_slave     ON mqtt_test_readings(slave_id);
CREATE INDEX IF NOT EXISTS idx_mqtt_test_readings_devtype   ON mqtt_test_readings(device_type);

-- 4. Clear existing data (user requested clean slate).
--    Using DELETE (not TRUNCATE) to keep things conservative and to allow
--    rollback within this transaction if anything else fails.
DELETE FROM mqtt_test_readings;

COMMIT;

-- Verify
SELECT
    COUNT(*) AS row_count_after,
    (SELECT column_name FROM information_schema.columns
       WHERE table_name = 'mqtt_test_readings' AND column_name = 'meterPowerW') AS new_col_check
FROM mqtt_test_readings;
