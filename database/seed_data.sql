-- GoodEnergies MQTT Test - Seed Data
-- Sample plant with 3 inverters for testing

-- ============================================
-- Insert Test Plant
-- ============================================
INSERT INTO test_plants (
    id,
    plant_id,
    name,
    location,
    address,
    capacity,
    installed_capacity_mw,
    grid_contracted_capacity_mw,
    latitude,
    longitude,
    timezone,
    poll_interval,
    status,
    poa_factor,
    azimuth_deg,
    tilt_deg,
    ppa_tariff_per_kwh,
    emission_factor,
    irradiance_active_threshold,
    commissioning_date
) VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'PLANT001',
    'Test Solar Plant - Bangalore',
    'Bangalore, Karnataka',
    '123 Solar Park Road, Electronic City, Bangalore 560100',
    1.5,                          -- 1.5 MW capacity
    1.5,
    1.5,
    12.9716,                      -- Bangalore latitude
    77.5946,                      -- Bangalore longitude
    'Asia/Kolkata',
    900,                          -- 15 min poll interval
    'active',
    1.0,
    180,                          -- South facing
    15,                           -- 15 degree tilt
    4.50,                         -- PPA tariff Rs 4.50/kWh
    0.82,                         -- Emission factor
    200,                          -- 200 W/m² threshold
    '2024-01-15 00:00:00'
);

-- ============================================
-- Insert Test Inverters (3 inverters)
-- ============================================

-- Inverter 1 - 500 kW
INSERT INTO test_inverters (    
    id,
    plant_id,
    name,
    serial_number,
    model,
    oem,
    location,
    ac_capacity_kva,
    dc_capacity_kwp,
    expected_efficiency,
    temp_coeff_pmax,
    noct,
    number_of_mppt,
    number_of_strings,
    panel_vmp_stc,
    panel_imp_stc,
    panel_vmp_temp_coeff,
    panel_rating_w,
    panels_per_string,
    strings_per_mppt,
    status
) VALUES (
    'b1b2c3d4-e5f6-7890-abcd-ef1234567891',
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'INV-001',
    'SG500-001',
    'SG500MX',
    'Sungrow',
    'Block A',
    500,                          -- 500 kVA AC
    550,                          -- 550 kWp DC
    0.98,
    -0.0045,
    45,
    4,                            -- 4 MPPT channels
    16,                           -- 16 strings total
    38.5,                         -- Panel Vmp at STC
    9.2,                          -- Panel Imp at STC
    -0.0029,                      -- Vmp temp coefficient
    400,                          -- 400W panel
    26,                           -- 26 panels per string
    4,                            -- 4 strings per MPPT
    'online'
);

-- Inverter 2 - 500 kW
INSERT INTO test_inverters (
    id,
    plant_id,
    name,
    serial_number,
    model,
    oem,
    location,
    ac_capacity_kva,
    dc_capacity_kwp,
    expected_efficiency,
    temp_coeff_pmax,
    noct,
    number_of_mppt,
    number_of_strings,
    panel_vmp_stc,
    panel_imp_stc,
    panel_vmp_temp_coeff,
    panel_rating_w,
    panels_per_string,
    strings_per_mppt,
    status
) VALUES (
    'b1b2c3d4-e5f6-7890-abcd-ef1234567892',
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'INV-002',
    'SG500-002',
    'SG500MX',
    'Sungrow',
    'Block A',
    500,
    550,
    0.98,
    -0.0045,
    45,
    4,
    16,
    38.5,
    9.2,
    -0.0029,
    400,
    26,
    4,
    'online'
);

-- Inverter 3 - 500 kW
INSERT INTO test_inverters (
    id,
    plant_id,
    name,
    serial_number,
    model,
    oem,
    location,
    ac_capacity_kva,
    dc_capacity_kwp,
    expected_efficiency,
    temp_coeff_pmax,
    noct,
    number_of_mppt,
    number_of_strings,
    panel_vmp_stc,
    panel_imp_stc,
    panel_vmp_temp_coeff,
    panel_rating_w,
    panels_per_string,
    strings_per_mppt,
    status
) VALUES (
    'b1b2c3d4-e5f6-7890-abcd-ef1234567893',
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'INV-003',
    'SG500-003',
    'SG500MX',
    'Sungrow',
    'Block B',
    500,
    550,
    0.98,
    -0.0045,
    45,
    4,
    16,
    38.5,
    9.2,
    -0.0029,
    400,
    26,
    4,
    'online'
);

-- Verify seed data
SELECT 'Plants:' as info, COUNT(*) as count FROM test_plants
UNION ALL
SELECT 'Inverters:', COUNT(*) FROM test_inverters;
