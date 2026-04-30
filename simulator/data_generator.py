"""
Data Generator for Solar Plant Simulation
Generates realistic inverter and weather data following sun curves
"""
import math
import random
from datetime import datetime
from typing import Dict, List, Tuple
import pytz

from .config import (
    PEAK_IRRADIANCE, TEMP_MIN, TEMP_MAX,
    NOISE_POWER, NOISE_VOLTAGE, NOISE_IRRADIANCE,
    INVERTER_SPECS, START_HOUR, END_HOUR, LOCATION
)


class SolarDataGenerator:
    """Generates realistic solar plant telemetry data"""

    def __init__(self):
        self.timezone = pytz.timezone(LOCATION["timezone"])
        self.daily_yield_accumulator = {}  # Track cumulative daily yield per inverter
        self.total_yield_accumulator = {}  # Track lifetime yield per inverter
        self._last_date = None

    def _get_sun_position(self, hour: float) -> float:
        """
        Calculate sun position factor (0 to 1) based on time of day.
        Returns 0 before sunrise/after sunset, peaks at 1.0 at solar noon.
        """
        # Solar noon is approximately 12:30 PM in India
        solar_noon = 12.5
        day_length = END_HOUR - START_HOUR  # 12 hours

        if hour < START_HOUR or hour > END_HOUR:
            return 0.0

        # Cosine curve centered on solar noon
        # Maps 6AM-6PM to 0 -> peak -> 0
        normalized_time = (hour - START_HOUR) / day_length  # 0 to 1
        sun_angle = math.pi * normalized_time  # 0 to pi
        position = math.sin(sun_angle)

        return max(0, position)

    def _add_noise(self, value: float, noise_factor: float) -> float:
        """Add random noise to a value"""
        noise = random.gauss(0, noise_factor * value)
        return max(0, value + noise)

    def _reset_daily_if_needed(self, current_time: datetime, inverter_id: str):
        """Reset daily accumulator at midnight"""
        current_date = current_time.date()

        if self._last_date is None:
            self._last_date = current_date

        if current_date != self._last_date:
            # New day - reset daily yields
            self.daily_yield_accumulator = {}
            self._last_date = current_date

        # Initialize if not present
        if inverter_id not in self.daily_yield_accumulator:
            self.daily_yield_accumulator[inverter_id] = 0.0
        if inverter_id not in self.total_yield_accumulator:
            self.total_yield_accumulator[inverter_id] = random.uniform(100000, 500000)  # Random lifetime yield

    def generate_weather_data(self, timestamp: datetime = None) -> Dict:
        """
        Generate weather reading (irradiance, temperature, wind speed)

        Returns:
            Dictionary with weather telemetry
        """
        if timestamp is None:
            timestamp = datetime.now(self.timezone)

        hour = timestamp.hour + timestamp.minute / 60.0
        sun_position = self._get_sun_position(hour)

        # Irradiance follows sun position
        base_irradiance = PEAK_IRRADIANCE * sun_position
        irradiance = self._add_noise(base_irradiance, NOISE_IRRADIANCE)

        # Temperature rises through the day, peaks around 2-3 PM
        temp_hour_offset = (hour - 6) / 12  # 0 at 6 AM, 1 at 6 PM
        temp_factor = math.sin(math.pi * temp_hour_offset * 0.8)  # Peak at ~2 PM
        temperature = TEMP_MIN + (TEMP_MAX - TEMP_MIN) * max(0, temp_factor)
        temperature = self._add_noise(temperature, 0.02)

        # Wind speed (random, typically 1-5 m/s)
        wind_speed = random.uniform(1, 5)

        return {
            "timestamp": timestamp.isoformat(),
            "irradiance": round(irradiance, 2),
            "ambient_temperature": round(temperature, 2),
            "wind_speed": round(wind_speed, 2),
            "humidity": round(random.uniform(40, 70), 2),
        }

    def generate_inverter_data(
        self,
        inverter_id: str,
        inverter_serial: str,
        timestamp: datetime = None,
        irradiance: float = None
    ) -> Dict:
        """
        Generate inverter telemetry data based on irradiance

        Args:
            inverter_id: UUID of inverter
            inverter_serial: Serial number
            timestamp: Timestamp of reading (default: now)
            irradiance: Current irradiance in W/m² (if None, will be calculated)

        Returns:
            Dictionary with inverter telemetry
        """
        if timestamp is None:
            timestamp = datetime.now(self.timezone)

        self._reset_daily_if_needed(timestamp, inverter_id)

        hour = timestamp.hour + timestamp.minute / 60.0

        # Calculate irradiance if not provided
        if irradiance is None:
            sun_position = self._get_sun_position(hour)
            irradiance = PEAK_IRRADIANCE * sun_position
            irradiance = self._add_noise(irradiance, NOISE_IRRADIANCE)

        # Calculate power output based on irradiance
        dc_capacity = INVERTER_SPECS["dc_capacity_kwp"]
        ac_capacity = INVERTER_SPECS["ac_capacity_kva"]
        efficiency = INVERTER_SPECS["efficiency"]

        # DC power = capacity * (irradiance / 1000) * efficiency_factor
        irradiance_factor = irradiance / 1000.0
        dc_power = dc_capacity * irradiance_factor

        # AC power with efficiency and clipping
        ac_power = dc_power * efficiency
        ac_power = min(ac_power, ac_capacity)  # Clip at AC capacity
        ac_power = self._add_noise(ac_power, NOISE_POWER)
        ac_power = max(0, ac_power)

        # Calculate energy yield for this interval (assuming 15-second interval for testing)
        interval_hours = 15 / 3600  # 15 seconds in hours
        interval_energy = ac_power * interval_hours

        # Update accumulators
        self.daily_yield_accumulator[inverter_id] += interval_energy
        self.total_yield_accumulator[inverter_id] += interval_energy

        # Generate MPPT data (4 channels)
        mppt_data = self._generate_mppt_data(dc_power, irradiance)

        # Generate string currents (8 strings)
        string_currents = self._generate_string_currents(dc_power)

        # Generate AC electrical readings
        ac_data = self._generate_ac_data(ac_power)

        return {
            "inverter_id": inverter_id,
            "inverter_sn": inverter_serial,
            "timestamp": timestamp.isoformat(),

            # Power metrics
            "active_power": round(ac_power, 2),
            "total_dc_power": round(dc_power, 2),
            "daily_power_yield": round(self.daily_yield_accumulator[inverter_id], 2),
            "total_power_yield": round(self.total_yield_accumulator[inverter_id], 2),

            # AC electrical
            **ac_data,

            # MPPT readings
            **mppt_data,

            # String currents
            **string_currents,

            # Diagnostics
            "fault_code": 0,  # No fault for normal operation
        }

    def _generate_mppt_data(self, total_dc_power: float, irradiance: float) -> Dict:
        """Generate MPPT channel voltage and current data"""
        num_mppt = INVERTER_SPECS["number_of_mppt"]
        power_per_mppt = total_dc_power / num_mppt if total_dc_power > 0 else 0

        mppt_data = {}
        for i in range(1, num_mppt + 1):
            # MPPT voltage typically 500-800V depending on strings
            base_voltage = 600 if irradiance > 100 else 0
            voltage = self._add_noise(base_voltage, NOISE_VOLTAGE) if irradiance > 100 else 0

            # Current calculated from power and voltage
            current = (power_per_mppt / voltage * 1000) if voltage > 0 else 0  # kW to W, then /V = A
            current = self._add_noise(current, NOISE_POWER)

            mppt_data[f"mppt{i}_voltage"] = round(max(0, voltage), 2)
            mppt_data[f"mppt{i}_current"] = round(max(0, current), 3)

        return mppt_data

    def _generate_string_currents(self, total_dc_power: float) -> Dict:
        """Generate PV string current data"""
        num_strings = INVERTER_SPECS["number_of_strings"]

        # Each string contributes roughly equal power
        # String current typically 8-12A at full power
        base_current = (total_dc_power / num_strings / 600 * 1000) if total_dc_power > 0 else 0

        string_data = {}
        for i in range(1, min(num_strings + 1, 9)):  # Only 8 strings in test schema
            current = self._add_noise(base_current, NOISE_POWER) if base_current > 0 else 0
            string_data[f"pv{i}_current"] = round(max(0, current), 3)

        return string_data

    def _generate_ac_data(self, ac_power: float) -> Dict:
        """Generate AC electrical readings"""
        # AC voltage typically 380-415V line-to-line
        base_voltage = 400 if ac_power > 0 else 0

        # AC current from power (3-phase)
        # P = sqrt(3) * V * I * PF
        # I = P / (sqrt(3) * V * PF)
        power_factor = 0.99
        ac_current = (ac_power * 1000) / (math.sqrt(3) * base_voltage * power_factor) if ac_power > 0 else 0

        return {
            "ry_ac_volt": round(self._add_noise(base_voltage, NOISE_VOLTAGE), 2),
            "yb_ac_volt": round(self._add_noise(base_voltage, NOISE_VOLTAGE), 2),
            "br_ac_volt": round(self._add_noise(base_voltage, NOISE_VOLTAGE), 2),
            "r_current": round(self._add_noise(ac_current, NOISE_POWER), 3),
            "y_current": round(self._add_noise(ac_current, NOISE_POWER), 3),
            "b_current": round(self._add_noise(ac_current, NOISE_POWER), 3),
            "frequency": round(self._add_noise(50.0, 0.001), 2),
            "power_factor": round(power_factor, 3),
            "reactive_power_kvar": round(ac_power * math.tan(math.acos(power_factor)), 2),
        }


# Singleton instance
generator = SolarDataGenerator()


def generate_weather(timestamp: datetime = None) -> Dict:
    """Generate weather data for current time"""
    return generator.generate_weather_data(timestamp)


def generate_inverter(
    inverter_id: str,
    inverter_serial: str,
    timestamp: datetime = None,
    irradiance: float = None
) -> Dict:
    """Generate inverter data for current time"""
    return generator.generate_inverter_data(inverter_id, inverter_serial, timestamp, irradiance)
