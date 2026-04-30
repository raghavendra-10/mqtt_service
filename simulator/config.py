"""
Simulator Configuration
Contains settings for generating realistic solar plant data
"""
import os
from dotenv import load_dotenv

load_dotenv()

# MQTT Settings
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "goodenergies_simulator")
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))

# Simulator Settings
INTERVAL_SECONDS = int(os.getenv("SIMULATOR_INTERVAL_SECONDS", "15"))
PLANT_ID = os.getenv("SIMULATOR_PLANT_ID", "PLANT001")
START_HOUR = int(os.getenv("SIMULATOR_START_HOUR", "6"))
END_HOUR = int(os.getenv("SIMULATOR_END_HOUR", "18"))

# Test Inverter IDs (must match seed_data.sql)
INVERTER_IDS = [
    {"id": "b1b2c3d4-e5f6-7890-abcd-ef1234567891", "serial": "SG500-001", "name": "INV-001"},
    {"id": "b1b2c3d4-e5f6-7890-abcd-ef1234567892", "serial": "SG500-002", "name": "INV-002"},
    {"id": "b1b2c3d4-e5f6-7890-abcd-ef1234567893", "serial": "SG500-003", "name": "INV-003"},
]

# Inverter Specifications (for realistic data generation)
INVERTER_SPECS = {
    "ac_capacity_kva": 500,
    "dc_capacity_kwp": 550,
    "efficiency": 0.98,
    "number_of_mppt": 4,
    "number_of_strings": 16,
}

# Weather/Irradiance Parameters (for Bangalore)
LOCATION = {
    "latitude": 12.9716,
    "longitude": 77.5946,
    "timezone": "Asia/Kolkata",
}

# Peak irradiance at solar noon (W/m²)
PEAK_IRRADIANCE = 1000

# Temperature range (°C)
TEMP_MIN = 25
TEMP_MAX = 38

# Noise factors (for realism)
NOISE_POWER = 0.02      # 2% noise in power readings
NOISE_VOLTAGE = 0.01    # 1% noise in voltage readings
NOISE_IRRADIANCE = 0.03 # 3% noise in irradiance readings
