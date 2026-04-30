"""
Yield Calculator - Calculates actual and expected energy yield

This module implements the same yield calculation logic as production,
designed for event-driven processing triggered by MQTT messages.
"""
import logging
from datetime import datetime
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("yield_calculator")


@dataclass
class InverterSpecs:
    """Inverter specifications for yield calculation"""
    dc_capacity_kwp: float = 550.0
    ac_capacity_kva: float = 500.0
    efficiency: float = 0.98
    temp_coeff_pmax: float = -0.0045  # %/°C
    noct: float = 45.0  # °C (Nominal Operating Cell Temperature)


@dataclass
class YieldResult:
    """Result of yield calculation"""
    actual_yield: float           # kWh - energy produced in interval
    expected_yield: float         # kWh - theoretical energy for interval
    cumulative_actual: float      # kWh - daily cumulative actual
    cumulative_expected: float    # kWh - daily cumulative expected
    interval_seconds: int         # Duration of interval


class YieldCalculator:
    """
    Calculates energy yield for solar inverters.

    Yield Calculation:
    - Actual Yield = daily_power_yield(t) - daily_power_yield(t-1)
    - Expected Yield = DC_capacity × (Irradiance/1000) × temp_loss × efficiency × hours

    The calculation is triggered immediately when new inverter data arrives,
    following an event-driven architecture.
    """

    def __init__(self, specs: InverterSpecs = None):
        self.specs = specs or InverterSpecs()
        self.cumulative_expected = 0.0
        self._last_date = None

    def calculate(
        self,
        current_daily_yield: float,
        previous_daily_yield: float,
        irradiance: float,
        ambient_temp: float,
        interval_seconds: int = 900,
        timestamp: datetime = None,
    ) -> YieldResult:
        """
        Calculate actual and expected yield for an interval.

        Args:
            current_daily_yield: Current cumulative daily yield (kWh)
            previous_daily_yield: Previous cumulative daily yield (kWh)
            irradiance: Solar irradiance (W/m²)
            ambient_temp: Ambient temperature (°C)
            interval_seconds: Time interval in seconds (default 15 min)
            timestamp: Timestamp of reading

        Returns:
            YieldResult with actual and expected yields
        """
        # Check for day reset
        if timestamp:
            current_date = timestamp.date()
            if self._last_date and current_date != self._last_date:
                self.cumulative_expected = 0.0
            self._last_date = current_date

        # Calculate actual yield (delta)
        actual_yield = current_daily_yield - previous_daily_yield

        # Handle day reset (yield counter resets at midnight)
        if actual_yield < 0:
            actual_yield = current_daily_yield

        # Calculate expected yield
        expected_yield = self._calculate_expected(
            irradiance=irradiance,
            ambient_temp=ambient_temp,
            interval_seconds=interval_seconds,
        )

        # Update cumulative expected
        self.cumulative_expected += expected_yield

        return YieldResult(
            actual_yield=actual_yield,
            expected_yield=expected_yield,
            cumulative_actual=current_daily_yield,
            cumulative_expected=self.cumulative_expected,
            interval_seconds=interval_seconds,
        )

    def _calculate_expected(
        self,
        irradiance: float,
        ambient_temp: float,
        interval_seconds: int,
    ) -> float:
        """
        Calculate expected yield based on irradiance and temperature.

        Uses IEC 61724 temperature correction method.
        """
        # Skip if below threshold
        if irradiance < 50:
            return 0.0

        # Module temperature (IEC method)
        t_module = ambient_temp + (irradiance / 800) * (self.specs.noct - 20)

        # Temperature loss factor
        temp_loss = 1 + self.specs.temp_coeff_pmax * (t_module - 25)

        # Expected power (kW)
        p_expected = (
            self.specs.dc_capacity_kwp
            * (irradiance / 1000)
            * temp_loss
            * self.specs.efficiency
        )

        # Clamp to AC capacity
        p_expected = min(p_expected, self.specs.ac_capacity_kva)

        # Convert to energy (kWh)
        interval_hours = interval_seconds / 3600
        expected_yield = p_expected * interval_hours

        return max(0, expected_yield)

    def calculate_module_temperature(
        self,
        ambient_temp: float,
        irradiance: float,
    ) -> float:
        """
        Calculate module temperature using NOCT method.

        Formula: T_module = T_ambient + (Irradiance/800) × (NOCT - 20)
        """
        return ambient_temp + (irradiance / 800) * (self.specs.noct - 20)


def calculate_interval_yield(
    current_yield: float,
    previous_yield: float,
    irradiance: float,
    ambient_temp: float,
    dc_capacity: float = 550.0,
    efficiency: float = 0.98,
    temp_coeff: float = -0.0045,
    noct: float = 45.0,
    interval_seconds: int = 900,
) -> Tuple[float, float, float]:
    """
    Convenience function to calculate yield for an interval.

    Args:
        current_yield: Current cumulative daily yield (kWh)
        previous_yield: Previous cumulative daily yield (kWh)
        irradiance: Solar irradiance (W/m²)
        ambient_temp: Ambient temperature (°C)
        dc_capacity: DC capacity (kWp)
        efficiency: Inverter efficiency
        temp_coeff: Temperature coefficient of Pmax (%/°C)
        noct: Nominal Operating Cell Temperature (°C)
        interval_seconds: Interval duration (seconds)

    Returns:
        Tuple of (actual_yield, expected_yield, module_temperature)
    """
    specs = InverterSpecs(
        dc_capacity_kwp=dc_capacity,
        efficiency=efficiency,
        temp_coeff_pmax=temp_coeff,
        noct=noct,
    )

    calculator = YieldCalculator(specs)
    result = calculator.calculate(
        current_daily_yield=current_yield,
        previous_daily_yield=previous_yield,
        irradiance=irradiance,
        ambient_temp=ambient_temp,
        interval_seconds=interval_seconds,
    )

    module_temp = calculator.calculate_module_temperature(ambient_temp, irradiance)

    return result.actual_yield, result.expected_yield, module_temp
