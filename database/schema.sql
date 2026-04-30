-- GoodEnergies MQTT Test Database Schema
-- Mirrors production schema with test_ prefix

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================
-- Table: test_plants
-- Plant metadata and configuration
-- ============================================
CREATE TABLE test_plants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_id VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    address TEXT,
    capacity DECIMAL(10,2),                    -- MW
    installed_capacity_mw DECIMAL(10,2),
    grid_contracted_capacity_mw DECIMAL(10,2),
    latitude DECIMAL(10,6),
    longitude DECIMAL(10,6),
    timezone VARCHAR(50) DEFAULT 'Asia/Kolkata',
    poll_interval INT DEFAULT 900,              -- seconds (15 min default)
    status VARCHAR(20) DEFAULT 'active',
    -- Solar configuration
    poa_factor DECIMAL(5,4) DEFAULT 1.0,
    azimuth_deg DECIMAL(5,2) DEFAULT 180,
    tilt_deg DECIMAL(5,2) DEFAULT 15,
    -- Financial
    ppa_tariff_per_kwh DECIMAL(8,4),
    emission_factor DECIMAL(8,6) DEFAULT 0.82,
    -- Thresholds
    irradiance_active_threshold DECIMAL(8,2) DEFAULT 200,  -- W/m²
    -- Timestamps
    commissioning_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- Table: test_inverters
-- Inverter metadata and specifications
-- ============================================
CREATE TABLE test_inverters (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_id UUID REFERENCES test_plants(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    serial_number VARCHAR(100) UNIQUE NOT NULL,
    model VARCHAR(100),
    oem VARCHAR(50) DEFAULT 'Sungrow',
    location VARCHAR(255),
    -- Capacity specs
    ac_capacity_kva INT,
    dc_capacity_kwp INT,
    -- Performance specs
    expected_efficiency DECIMAL(5,4) DEFAULT 0.98,
    temp_coeff_pmax DECIMAL(6,5) DEFAULT -0.0045,
    noct DECIMAL(5,2) DEFAULT 45,
    -- MPPT configuration
    number_of_mppt INT DEFAULT 2,
    number_of_strings INT DEFAULT 8,
    -- Panel specs (for loss calculation)
    panel_vmp_stc DECIMAL(6,2),
    panel_imp_stc DECIMAL(6,3),
    panel_vmp_temp_coeff DECIMAL(8,6),
    panel_rating_w INT,
    panels_per_string INT,
    strings_per_mppt INT,
    -- Status
    status VARCHAR(20) DEFAULT 'online',
    power_output DECIMAL(10,2) DEFAULT 0,
    temperature DECIMAL(5,2),
    last_updated TIMESTAMP,
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ============================================
-- Table: test_inverter_readings
-- Time-series inverter telemetry data
-- ============================================
CREATE TABLE test_inverter_readings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inverter_id UUID REFERENCES test_inverters(id) ON DELETE CASCADE,
    inverter_sn VARCHAR(100),
    timestamp TIMESTAMP NOT NULL,

    -- Power metrics
    active_power DECIMAL(10,2),                 -- kW (instantaneous)
    total_active_power DECIMAL(10,2),           -- kW
    total_dc_power DECIMAL(10,2),               -- kW
    daily_power_yield DECIMAL(10,2),            -- kWh (cumulative today)
    total_power_yield DECIMAL(12,2),            -- kWh (lifetime)
    reactive_power_kvar DECIMAL(10,2),

    -- AC electrical readings
    r_current DECIMAL(8,3),                     -- Phase R current (A)
    y_current DECIMAL(8,3),                     -- Phase Y current (A)
    b_current DECIMAL(8,3),                     -- Phase B current (A)
    ry_ac_volt DECIMAL(8,2),                    -- RY voltage (V)
    yb_ac_volt DECIMAL(8,2),                    -- YB voltage (V)
    br_ac_volt DECIMAL(8,2),                    -- BR voltage (V)
    frequency DECIMAL(5,2),                     -- Hz
    power_factor DECIMAL(5,3),

    -- MPPT readings (4 channels for testing)
    mppt1_voltage DECIMAL(8,2),
    mppt1_current DECIMAL(8,3),
    mppt2_voltage DECIMAL(8,2),
    mppt2_current DECIMAL(8,3),
    mppt3_voltage DECIMAL(8,2),
    mppt3_current DECIMAL(8,3),
    mppt4_voltage DECIMAL(8,2),
    mppt4_current DECIMAL(8,3),

    -- PV String currents (8 strings for testing)
    pv1_current DECIMAL(8,3),
    pv2_current DECIMAL(8,3),
    pv3_current DECIMAL(8,3),
    pv4_current DECIMAL(8,3),
    pv5_current DECIMAL(8,3),
    pv6_current DECIMAL(8,3),
    pv7_current DECIMAL(8,3),
    pv8_current DECIMAL(8,3),

    -- Diagnostics
    fault_code INT DEFAULT 0,

    -- Calculated losses (populated by event handler)
    loss_temperature DECIMAL(10,4),
    loss_inverter_efficiency DECIMAL(10,4),
    loss_clipping DECIMAL(10,4),
    loss_total DECIMAL(10,4),

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for fast queries
CREATE INDEX idx_test_readings_inverter_ts ON test_inverter_readings(inverter_id, timestamp);
CREATE INDEX idx_test_readings_timestamp ON test_inverter_readings(timestamp);

-- ============================================
-- Table: test_weather_readings
-- Weather station telemetry data
-- ============================================
CREATE TABLE test_weather_readings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    plant_id UUID REFERENCES test_plants(id) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,

    -- Weather metrics
    irradiance DECIMAL(8,2),                    -- W/m² (GHI or POA)
    ambient_temperature DECIMAL(5,2),           -- °C
    module_temperature DECIMAL(5,2),            -- °C (if sensor available)
    wind_speed DECIMAL(5,2),                    -- m/s
    humidity DECIMAL(5,2),                      -- %

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for fast queries
CREATE INDEX idx_test_weather_plant_ts ON test_weather_readings(plant_id, timestamp);

-- ============================================
-- Table: test_yield_calculations
-- Event-driven yield/PR calculations
-- ============================================
CREATE TABLE test_yield_calculations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inverter_id UUID REFERENCES test_inverters(id) ON DELETE CASCADE,
    plant_id UUID REFERENCES test_plants(id) ON DELETE CASCADE,
    timestamp TIMESTAMP NOT NULL,

    -- Yield metrics
    actual_yield DECIMAL(10,4),                 -- kWh (interval)
    expected_yield DECIMAL(10,4),               -- kWh (interval)
    cumulative_actual DECIMAL(12,4),            -- kWh (day total)
    cumulative_expected DECIMAL(12,4),          -- kWh (day total)

    -- Performance Ratio
    pr_actual DECIMAL(6,4),
    pr_expected DECIMAL(6,4),
    performance_ratio_pct DECIMAL(6,2),         -- (actual/expected) * 100

    -- Calculation inputs (for debugging)
    irradiance DECIMAL(8,2),
    ambient_temp DECIMAL(5,2),
    module_temp DECIMAL(5,2),                   -- Calculated via NOCT formula

    -- Processing metadata
    calculation_time_ms INT,                    -- How long calculation took
    triggered_by VARCHAR(50) DEFAULT 'mqtt',    -- mqtt, backfill, manual

    -- Metadata
    created_at TIMESTAMP DEFAULT NOW()
);

-- Index for fast queries
CREATE INDEX idx_test_yield_inverter_ts ON test_yield_calculations(inverter_id, timestamp);
CREATE INDEX idx_test_yield_plant_ts ON test_yield_calculations(plant_id, timestamp);

-- ============================================
-- Table: test_mqtt_messages
-- Log of all MQTT messages (for debugging)
-- ============================================
CREATE TABLE test_mqtt_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    topic VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    qos INT DEFAULT 1,
    retained BOOLEAN DEFAULT FALSE,
    processed BOOLEAN DEFAULT FALSE,
    processing_error TEXT,
    received_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP
);

-- Index for monitoring
CREATE INDEX idx_test_mqtt_topic ON test_mqtt_messages(topic);
CREATE INDEX idx_test_mqtt_received ON test_mqtt_messages(received_at);
CREATE INDEX idx_test_mqtt_processed ON test_mqtt_messages(processed);
