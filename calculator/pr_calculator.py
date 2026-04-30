"""
Performance Ratio Calculator - IEC 61724 Variant-B Implementation

This module calculates Performance Ratio (PR) for solar PV systems
using the IEC 61724 Variant-B method with temperature correction.
"""
import logging
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("pr_calculator")


@dataclass
class PRResult:
    """Result of PR calculation"""
    pr_actual: float              # Actual Performance Ratio (0-1+)
    pr_expected: float            # Expected/Reference PR (0-1)
    pr_percentage: float          # PR as percentage (0-100+)
    actual_yield: float           # Actual energy (kWh)
    expected_yield: float         # Expected energy (kWh)
    module_temperature: float     # Calculated module temp (°C)
    temperature_loss: float       # Temperature loss factor


@dataclass
class DailyPRSummary:
    """Daily PR summary statistics"""
    date: datetime
    avg_pr: float
    max_pr: float
    min_pr: float
    total_actual_yield: float
    total_expected_yield: float
    data_points: int


class PRCalculator:
    """
    Calculates Performance Ratio using IEC 61724 Variant-B method.

    IEC 61724 Variant-B Formula:
    1. Module Temperature: T_mod = T_amb + (G/800) × (NOCT - 20)
    2. Temperature Loss: L_temp = 1 + γ × (T_mod - 25)
       where γ = temperature coefficient of Pmax (typically -0.45%/°C)
    3. Expected Power: P_exp = P_rated × (G/1000) × L_temp × η
    4. Performance Ratio: PR = E_actual / E_expected

    This method accounts for:
    - Irradiance variations
    - Temperature effects on module performance
    - System efficiency losses
    """

    def __init__(
        self,
        dc_capacity_kwp: float = 550.0,
        efficiency: float = 0.98,
        temp_coeff_pmax: float = -0.0045,
        noct: float = 45.0,
        stc_temp: float = 25.0,
    ):
        """
        Initialize PR Calculator.

        Args:
            dc_capacity_kwp: DC capacity in kWp
            efficiency: Expected system efficiency (0-1)
            temp_coeff_pmax: Temperature coefficient of Pmax (%/°C, negative)
            noct: Nominal Operating Cell Temperature (°C)
            stc_temp: Standard Test Condition temperature (°C)
        """
        self.dc_capacity = dc_capacity_kwp
        self.efficiency = efficiency
        self.temp_coeff = temp_coeff_pmax
        self.noct = noct
        self.stc_temp = stc_temp

        # Track daily calculations
        self._daily_data: List[PRResult] = []
        self._current_date = None

    def calculate(
        self,
        actual_yield: float,
        irradiance: float,
        ambient_temp: float,
        interval_seconds: int = 900,
    ) -> Optional[PRResult]:
        """
        Calculate PR for a single interval.

        Args:
            actual_yield: Actual energy produced (kWh)
            irradiance: Solar irradiance (W/m²)
            ambient_temp: Ambient temperature (°C)
            interval_seconds: Interval duration (seconds)

        Returns:
            PRResult or None if irradiance below threshold
        """
        # Skip calculation below minimum irradiance
        if irradiance < 50:
            return None

        # Step 1: Calculate module temperature
        module_temp = self._calculate_module_temperature(ambient_temp, irradiance)

        # Step 2: Calculate temperature loss factor
        temp_loss = self._calculate_temperature_loss(module_temp)

        # Step 3: Calculate expected yield
        expected_yield = self._calculate_expected_yield(
            irradiance=irradiance,
            temp_loss=temp_loss,
            interval_seconds=interval_seconds,
        )

        # Step 4: Calculate PR
        if expected_yield > 0:
            pr_actual = actual_yield / expected_yield
        else:
            pr_actual = 0.0

        # Expected PR (theoretical maximum with perfect conditions)
        pr_expected = self.efficiency * temp_loss

        # PR as percentage
        pr_percentage = (pr_actual / pr_expected * 100) if pr_expected > 0 else 0

        # Clamp to reasonable range (can exceed 100% in favorable conditions)
        pr_percentage = max(0, min(150, pr_percentage))

        result = PRResult(
            pr_actual=pr_actual,
            pr_expected=pr_expected,
            pr_percentage=pr_percentage,
            actual_yield=actual_yield,
            expected_yield=expected_yield,
            module_temperature=module_temp,
            temperature_loss=temp_loss,
        )

        self._daily_data.append(result)

        return result

    def _calculate_module_temperature(
        self,
        ambient_temp: float,
        irradiance: float,
    ) -> float:
        """
        Calculate module temperature using NOCT method.

        Formula: T_mod = T_amb + (G/800) × (NOCT - 20)

        The factor 800 W/m² is the irradiance at which NOCT is measured.
        """
        return ambient_temp + (irradiance / 800) * (self.noct - 20)

    def _calculate_temperature_loss(self, module_temp: float) -> float:
        """
        Calculate temperature loss factor.

        Formula: L_temp = 1 + γ × (T_mod - T_stc)

        Where:
        - γ = temperature coefficient (typically -0.45%/°C = -0.0045)
        - T_stc = Standard Test Condition temperature (25°C)

        Result is typically < 1 when T_mod > 25°C (power decreases with heat)
        """
        return 1 + self.temp_coeff * (module_temp - self.stc_temp)

    def _calculate_expected_yield(
        self,
        irradiance: float,
        temp_loss: float,
        interval_seconds: int,
    ) -> float:
        """
        Calculate expected energy yield.

        Formula: E_exp = P_dc × (G/1000) × L_temp × η × hours

        Where:
        - P_dc = DC capacity (kWp)
        - G = Irradiance (W/m²)
        - L_temp = Temperature loss factor
        - η = System efficiency
        """
        # Expected power (kW)
        p_expected = (
            self.dc_capacity
            * (irradiance / 1000)
            * temp_loss
            * self.efficiency
        )

        # Convert to energy (kWh)
        interval_hours = interval_seconds / 3600
        expected_yield = p_expected * interval_hours

        return max(0, expected_yield)

    def get_daily_summary(self, date: datetime = None) -> Optional[DailyPRSummary]:
        """
        Get PR summary for a day.

        Args:
            date: Date to summarize (default: current day)

        Returns:
            DailyPRSummary or None if no data
        """
        if not self._daily_data:
            return None

        pr_values = [r.pr_percentage for r in self._daily_data if r.pr_percentage > 0]
        actual_yields = [r.actual_yield for r in self._daily_data]
        expected_yields = [r.expected_yield for r in self._daily_data]

        if not pr_values:
            return None

        return DailyPRSummary(
            date=date or datetime.now(),
            avg_pr=sum(pr_values) / len(pr_values),
            max_pr=max(pr_values),
            min_pr=min(pr_values),
            total_actual_yield=sum(actual_yields),
            total_expected_yield=sum(expected_yields),
            data_points=len(self._daily_data),
        )

    def reset_daily_data(self):
        """Reset daily data for new day"""
        self._daily_data = []


def calculate_pr(
    actual_yield: float,
    irradiance: float,
    ambient_temp: float,
    dc_capacity: float = 550.0,
    efficiency: float = 0.98,
    temp_coeff: float = -0.0045,
    noct: float = 45.0,
    interval_seconds: int = 900,
) -> Optional[Dict]:
    """
    Convenience function to calculate PR for an interval.

    Args:
        actual_yield: Actual energy produced (kWh)
        irradiance: Solar irradiance (W/m²)
        ambient_temp: Ambient temperature (°C)
        dc_capacity: DC capacity (kWp)
        efficiency: System efficiency
        temp_coeff: Temperature coefficient (%/°C)
        noct: NOCT (°C)
        interval_seconds: Interval (seconds)

    Returns:
        Dict with PR calculation results or None
    """
    calculator = PRCalculator(
        dc_capacity_kwp=dc_capacity,
        efficiency=efficiency,
        temp_coeff_pmax=temp_coeff,
        noct=noct,
    )

    result = calculator.calculate(
        actual_yield=actual_yield,
        irradiance=irradiance,
        ambient_temp=ambient_temp,
        interval_seconds=interval_seconds,
    )

    if result:
        return {
            "pr_actual": result.pr_actual,
            "pr_expected": result.pr_expected,
            "pr_percentage": result.pr_percentage,
            "actual_yield": result.actual_yield,
            "expected_yield": result.expected_yield,
            "module_temperature": result.module_temperature,
            "temperature_loss": result.temperature_loss,
        }

    return None
