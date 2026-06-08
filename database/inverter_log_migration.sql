-- Two-table split for WT410M Modbus payloads on iot1/* and iot2/* topics.
--
-- Variant A (full snapshot)  -> inverter_full_log
--   Identified by presence of "Device type code" key in the modbus entry.
--   Contains the complete register dump (MPPT 1-12, strings, energy totals, etc.).
--
-- Variant B (realtime delta) -> inverter_realtime_log
--   Smaller payloads with hot fields (Total active power, reactive, PF, frequency).
--
-- Both store the modbus entry as JSONB so new gateway fields don't need migrations.

CREATE TABLE IF NOT EXISTS inverter_full_log (
    id            BIGSERIAL PRIMARY KEY,
    gateway_imei  TEXT NOT NULL,
    uid           INT,
    sid           INT,
    dtm           TIMESTAMPTZ,
    seq           BIGINT,
    topic         TEXT,
    data          JSONB NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inverter_full_imei_dtm
    ON inverter_full_log (gateway_imei, dtm DESC);
CREATE INDEX IF NOT EXISTS idx_inverter_full_seq
    ON inverter_full_log (gateway_imei, seq);

CREATE TABLE IF NOT EXISTS inverter_realtime_log (
    id            BIGSERIAL PRIMARY KEY,
    gateway_imei  TEXT NOT NULL,
    uid           INT,
    sid           INT,
    dtm           TIMESTAMPTZ,
    seq           BIGINT,
    topic         TEXT,
    data          JSONB NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inverter_realtime_imei_dtm
    ON inverter_realtime_log (gateway_imei, dtm DESC);
CREATE INDEX IF NOT EXISTS idx_inverter_realtime_seq
    ON inverter_realtime_log (gateway_imei, seq);
