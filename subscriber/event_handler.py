"""
Event Handler - Processes incoming MQTT messages from WT410M gateways and simulators.

Supports two message sources:
  1. WT410M Gateway: iot1/{IMEI}/event/ → Modbus register JSON → parsed via register map
  2. Simulator: goodenergies/plants/{id}/inverters/{id}/telemetry → direct JSON

Both are normalized and written to the production database.
"""
import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

from .db_writer import (
    save_weather_reading,
    save_inverter_reading,
    save_mqtt_message,
)

logger = logging.getLogger("event_handler")


class EventHandler:
    """
    Handles incoming MQTT messages from gateways and simulators.

    Event-driven flow:
    1. Message arrives → parse and validate
    2. If gateway message → parse via WT410M parser + register map
    3. Save to production database
    """

    def __init__(self, gateway_parser=None):
        self.gateway_parser = gateway_parser
        self.loop = None
        self.stats = {
            "weather_processed": 0,
            "inverter_processed": 0,
            "gateway_messages": 0,
            "simulator_messages": 0,
            "errors": 0,
        }

    def _get_loop(self):
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        return self.loop

    def _run_async(self, coro):
        return self._get_loop().run_until_complete(coro)

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Main entry point - dispatches to gateway or simulator handler."""
        try:
            # WT410M gateway message
            if self.gateway_parser and self.gateway_parser.is_gateway_message(topic):
                return self._handle_gateway_message(topic, payload)

            # Simulator / direct JSON
            data = json.loads(payload.decode("utf-8"))
            message_type = self._get_message_type(topic)

            if message_type == "weather":
                return self._handle_simulator_weather(topic, data)
            elif message_type == "telemetry":
                return self._handle_simulator_telemetry(topic, data)
            else:
                logger.warning(f"Unknown topic: {topic}")
                return False

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from {topic}: {e}")
            self.stats["errors"] += 1
            return False
        except Exception as e:
            logger.error(f"Error handling message from {topic}: {e}", exc_info=True)
            self.stats["errors"] += 1
            return False

    # ---- WT410M Gateway ----

    def _handle_gateway_message(self, topic: str, payload: bytes) -> bool:
        parsed = self.gateway_parser.parse_payload(topic, payload)
        if not parsed:
            self.stats["errors"] += 1
            return False

        self.stats["gateway_messages"] += 1
        gateway_uid = parsed["gateway_uid"]
        timestamp = parsed["timestamp"]
        slaves = parsed["slaves"]

        self._run_async(save_mqtt_message(topic, parsed.get("raw", {}), processed=False))

        devices = self.gateway_parser.resolve_devices(gateway_uid, slaves)
        if not devices:
            logger.warning(f"No device mapping for gateway {gateway_uid}")
            return False

        success = False
        for plant_id, inverter_id, inverter_sn, slave_id, device_type, data in devices:
            data = dict(data)  # don't mutate the parser's dict
            self._combine_uint32_pairs(data)

            has_weather = "irradiance" in data and "ambientTemperature" in data

            if has_weather or device_type == "weather":
                weather_data = {
                    "_gateway_uid": gateway_uid,
                    "_slave_id": slave_id,
                    "plantId": plant_id,
                    "timestamp": timestamp,
                    "irradiance": data.get("irradiance", 0),
                    "ambientTemperature": data.get("ambientTemperature", 25),
                    "windSpeed": data.get("windSpeed"),
                }
                rid = self._run_async(save_weather_reading(weather_data))
                if rid:
                    self.stats["weather_processed"] += 1
                    success = True
                continue

            reading_data = {
                "_gateway_uid": gateway_uid,
                "_slave_id": slave_id,
                "inverterId": inverter_id,
                "inverterSn": inverter_sn,
                "timestamp": timestamp,
                "device_type": device_type,
                **data,
            }
            if device_type == "inverter":
                reading_data.setdefault("dailyPowerYield", 0.0)
                reading_data.setdefault("activePower", 0.0)
                reading_data.setdefault("faultId", 0)

            rid = self._run_async(save_inverter_reading(reading_data))
            if rid:
                self.stats["inverter_processed"] += 1
                logger.info(
                    f"[GW:{gateway_uid[:12]}] {device_type} slave={slave_id} | "
                    f"sn={inverter_sn}"
                )
                success = True

        self._run_async(save_mqtt_message(topic, parsed.get("raw", {}), processed=True))
        return success

    @staticmethod
    def _combine_uint32_pairs(data: Dict) -> None:
        """
        Combine little-endian uint32 register pairs into single fields.

        The simulator stores 32-bit values as low+high 16-bit register pairs
        (inverter_sim.py:69-74). The register map names them e.g.
        meterPowerLow / meterPowerHigh. Combine into meterPowerW = (hi<<16)|lo.
        """
        pairs = [
            ("meterPowerLow", "meterPowerHigh", "meterPowerW"),
            ("loadPowerLow",  "loadPowerHigh",  "loadPowerW"),
        ]
        for lo_key, hi_key, out_key in pairs:
            if lo_key in data and hi_key in data:
                try:
                    lo = int(data.pop(lo_key))
                    hi = int(data.pop(hi_key))
                    data[out_key] = float((hi << 16) | (lo & 0xFFFF))
                except (ValueError, TypeError):
                    pass

    # ---- Simulator ----

    def _get_message_type(self, topic: str) -> Optional[str]:
        parts = topic.split("/")
        if len(parts) >= 4 and parts[-1] == "weather":
            return "weather"
        elif len(parts) >= 6 and parts[-1] == "telemetry":
            return "telemetry"
        return None

    def _handle_simulator_weather(self, topic: str, data: Dict) -> bool:
        try:
            parts = topic.split("/")
            plant_id = parts[2] if len(parts) >= 3 else ""

            weather_data = {
                "plantId": data.get("plantId", data.get("plant_id", plant_id)),
                "timestamp": data.get("timestamp", ""),
                "irradiance": data.get("irradiance", 0),
                "ambientTemperature": data.get("ambient_temperature", data.get("ambientTemperature", 25)),
                "windSpeed": data.get("wind_speed", data.get("windSpeed")),
            }

            rid = self._run_async(save_weather_reading(weather_data))
            if rid:
                self.stats["weather_processed"] += 1
                self.stats["simulator_messages"] += 1
                logger.info(f"[SIM] Weather | Irr: {weather_data['irradiance']} W/m2")
                return True
            return False
        except Exception as e:
            logger.error(f"Simulator weather error: {e}")
            self.stats["errors"] += 1
            return False

    def _handle_simulator_telemetry(self, topic: str, data: Dict) -> bool:
        try:
            parts = topic.split("/")
            inverter_id = parts[4] if len(parts) >= 5 else data.get("inverter_id", "")

            reading_data = {
                "inverterId": data.get("inverter_id", data.get("inverterId", inverter_id)),
                "inverterSn": data.get("inverter_sn", data.get("inverterSn", "")),
                "timestamp": data.get("timestamp", ""),
                "activePower": data.get("active_power", data.get("activePower", 0)),
                "dailyPowerYield": data.get("daily_power_yield", data.get("dailyPowerYield", 0)),
                "totalPowerYield": data.get("total_power_yield", data.get("totalPowerYield", 0)),
                "totalDcPower": data.get("total_dc_power", data.get("totalDcPower")),
                "reactivePowerKvar": data.get("reactive_power_kvar", data.get("reactivePowerKvar")),
                "faultId": data.get("fault_code", data.get("faultId", 0)),
            }

            # Map nested AC electrical
            ac = data.get("AC_ELECTRICAL", {})
            if ac:
                reading_data.update({
                    "rCurrent": ac.get("r_current"),
                    "yCurrent": ac.get("y_current"),
                    "bCurrent": ac.get("b_current"),
                    "ryAcVolt": ac.get("ry_ac_volt"),
                    "ybAcVolt": ac.get("yb_ac_volt"),
                    "brAcVolt": ac.get("br_ac_volt"),
                    "frequency": ac.get("frequency"),
                    "powerFactor": ac.get("power_factor"),
                })

            # Map MPPT channels
            mppt = data.get("MPPT_CHANNELS", {})
            for i in range(1, 21):
                v = mppt.get(f"mppt{i}_voltage")
                c = mppt.get(f"mppt{i}_current")
                if v is not None:
                    reading_data[f"mppt{i}Voltage"] = v
                if c is not None:
                    reading_data[f"mppt{i}Current"] = c

            # Map PV strings
            pv = data.get("PV_STRINGS", {})
            for i in range(1, 41):
                c = pv.get(f"pv{i}_current")
                if c is not None:
                    reading_data[f"pv{i}Current"] = c

            reading_data = {k: v for k, v in reading_data.items() if v is not None}

            rid = self._run_async(save_inverter_reading(reading_data))
            if rid:
                self.stats["inverter_processed"] += 1
                self.stats["simulator_messages"] += 1
                logger.info(
                    f"[SIM] Inverter | {reading_data.get('inverterSn', '?')} | "
                    f"Power: {reading_data.get('activePower', 0):.1f} kW"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Simulator telemetry error: {e}")
            self.stats["errors"] += 1
            return False

    def get_stats(self) -> Dict:
        return self.stats.copy()


# Global singleton
event_handler: Optional[EventHandler] = None


def init_event_handler(gateway_parser=None):
    global event_handler
    event_handler = EventHandler(gateway_parser=gateway_parser)
    return event_handler


def handle_mqtt_message(topic: str, payload: bytes) -> bool:
    if event_handler is None:
        raise RuntimeError("Call init_event_handler() first")
    return event_handler.handle_message(topic, payload)
