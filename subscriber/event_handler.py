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
    save_inverter_log,
    save_production_reading,
    save_sensor_weather_reading,
    fetch_wind_and_update,
)

logger = logging.getLogger("event_handler")

# Load device registry for IMEI:sid → inverter mapping
def _load_device_registry() -> Dict:
    import os
    registry_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config", "device_registry.json"
    )
    try:
        with open(registry_path, "r") as f:
            import json as _json
            return _json.load(f)
    except Exception as e:
        logger.warning(f"Could not load device_registry.json: {e}")
        return {}

_DEVICE_REGISTRY = _load_device_registry()

# Plant coordinates for Open-Meteo fallback (used when physical sensor is not live)
_PLANT_COORDS = {
    "4e830dca-ee75-46eb-9799-8ce5cbb573ce": {"lat": 9.4729, "lon": 77.7047},  # Sivakasi
}

# Latest temperature from gateway sensor (SID 1), used to override Open-Meteo temperature
_gateway_temperature: Optional[float] = None


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
        # Flush recalculation removed: pr_scheduler.py detects gaps automatically
        # via its 1-minute gap-check query and recalculates without any intervention.

    def _get_loop(self):
        if self.loop is None or self.loop.is_closed():
            self.loop = asyncio.new_event_loop()
        return self.loop

    def _run_async(self, coro):
        return self._get_loop().run_until_complete(coro)

    def handle_message(self, topic: str, payload: bytes) -> bool:
        """Main entry point - dispatches to gateway or simulator handler."""
        try:
            # WT410M raw modbus log on iot1/* or iot2/* (topic shape may vary).
            # Routes each modbus entry to inverter_full_log or inverter_realtime_log
            # based on whether "Device type code" is present.
            if topic.startswith(("iot1/", "iot2/")):
                return self._handle_modbus_log(topic, payload)

            # WT410M gateway message
            if self.gateway_parser and self.gateway_parser.is_gateway_message(topic):
                return self._handle_gateway_message(topic, payload)

            # Simulator / direct JSON
            data = json.loads(payload.decode("latin-1"), strict=False)
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

    # ---- WT410M raw modbus log (two-table split) ----

    def _handle_modbus_log(self, topic: str, payload: bytes) -> bool:
        """
        Save WT410M payloads to inverter_full_log / inverter_realtime_log.

        Expected payload shape:
            {"data": {"imei": "...", "uid": 1, "dtm": "20260506221920",
                      "seq": 495, "msg": "log",
                      "modbus": [{"sid": 1, "stat": 0, "rcnt": N, ...fields...}]}}

        Each modbus entry with stat==0 is saved. Entries containing
        "Device type code" go to inverter_full_log; others to inverter_realtime_log.
        """
        try:
            # Gateway may embed raw binary in "reserved" fields; keep only
            # printable ASCII + common whitespace so JSON parsing succeeds.
            clean = bytes(b for b in payload if 0x20 <= b < 0x7F or b in (0x09, 0x0A, 0x0D))
            text = clean.decode("ascii")

            # Log full cleaned payload from datalogger
            logger.info(f"[RAW] {text[:2000]}")

            # Fix gateway firmware bugs before JSON parsing
            import re
            # 1. Strip "reserved" fields (reserved, reserved1, reserved2, reserved3, etc.)
            #    These are Modbus skip-word registers that contain binary garbage from
            #    the WT410M data logger. The logger reads continuous register blocks and
            #    includes reserved/unused registers in the payload. Their values are
            #    meaningless and often contain control characters that break JSON parsing.
            text = re.sub(r'"reserved\d*"\s*:\s*"[^"]*"', '"_reserved":""', text)

            # 2. Try parsing — use strict=False to tolerate any remaining control chars
            try:
                data = json.loads(text, strict=False)
            except json.JSONDecodeError:
                # 3. Fix truncated fields: gateway drops field names,
                #    leaving ":value,value" instead of ":value,"fieldname":value"
                #    Pattern: number followed by comma and number WITHOUT a quoted key after
                #    e.g. "String 23 current":0,0 } → "String 23 current":0 }
                text = re.sub(r',\s*(-?\d+)\s*\}', r' }', text)
                # Also handle multiple orphaned values: :0,0,0 }
                text = re.sub(r',\s*(-?\d+)\s*\}', r' }', text)
                try:
                    data = json.loads(text, strict=False)
                    logger.info(f"[FIX] Recovered malformed payload after cleanup")
                except json.JSONDecodeError as e2:
                    raise e2
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            if isinstance(e, json.JSONDecodeError):
                p = e.pos
                txt = clean.decode("ascii", errors="replace")
                logger.error(f"Bad payload on {topic}: {e} | around pos {p}: ...{txt[max(0,p-30):p+15]}...")
            else:
                logger.error(f"Bad payload on {topic}: {e}")
            self.stats["errors"] += 1
            return False

        inner = data.get("data") if isinstance(data, dict) else None
        if not isinstance(inner, dict) or "modbus" not in inner:
            logger.warning(f"Payload on {topic} has no data.modbus[]; skipping")
            return False

        imei = inner.get("imei", "")
        uid = inner.get("uid")
        dtm = inner.get("dtm")
        seq = inner.get("seq")
        modbus = inner.get("modbus", [])

        any_saved = False
        for entry in modbus:
            if not isinstance(entry, dict):
                continue
            if entry.get("stat", -1) != 0:
                continue

            sid = entry.get("sid")
            kind = "full" if "Device type code" in entry else "realtime"

            rid = self._run_async(save_inverter_log(
                kind=kind,
                gateway_imei=imei,
                topic=topic,
                uid=uid,
                sid=sid,
                dtm=dtm,
                seq=seq,
                data=entry,
            ))
            if rid:
                any_saved = True
                self.stats["gateway_messages"] += 1
                logger.info(
                    f"[{kind.upper()}] imei={imei} sid={sid} seq={seq} "
                    f"rcnt={entry.get('rcnt')} -> id={rid}"
                )

                # Also write to production inverter_readings table
                device = _DEVICE_REGISTRY.get(imei, {}).get(str(sid))
                if device and device.get("type") == "weather":
                    # Physical weather sensor — SID 30 (temp) or SID 91 (irr + body_temp)
                    # Save directly to weather_readings, bypasses Open-Meteo
                    global _gateway_temperature
                    def _first_not_none(entry, *keys):
                        for k in keys:
                            v = entry.get(k)
                            if v is not None:
                                return v
                        return None

                    raw_temp = _first_not_none(entry, "Temperature", "temperature", "AmbientTemp", "Temp")
                    raw_irr = _first_not_none(entry, "Irradiance", "irradiance", "GHI", "Irr", "SolarIrr")
                    raw_wind = _first_not_none(entry, "WindSpeed", "windSpeed", "Wind")
                    raw_body_temp = _first_not_none(entry, "Body_Temperature", "BodyTemperature", "body_temperature")

                    if raw_temp is not None:
                        _gateway_temperature = float(raw_temp)

                    if raw_irr is not None or raw_temp is not None:
                        # Physical sensor present — save directly, skip Open-Meteo
                        from .db_writer import _parse_dtm_as_utc
                        reading_ts = _parse_dtm_as_utc(dtm)
                        self._run_async(save_sensor_weather_reading(
                            plant_id=device["plant_id"],
                            reading_ts=reading_ts,
                            sensor_id=f"sid{sid}",
                            temperature=float(raw_temp) if raw_temp is not None else None,
                            irradiance=float(raw_irr) if raw_irr is not None else None,
                            wind_speed=float(raw_wind) if raw_wind is not None else None,
                            body_temperature=float(raw_body_temp) if raw_body_temp is not None else None,
                        ))
                        # Fetch wind speed from Open-Meteo and update same row
                        _coords = _PLANT_COORDS.get(device["plant_id"], {})
                        if _coords and reading_ts:
                            import datetime as _dt_mod
                            _save_ts = reading_ts.replace(second=0, microsecond=0)
                            self._run_async(fetch_wind_and_update(
                                plant_id=device["plant_id"],
                                lat=_coords["lat"],
                                lon=_coords["lon"],
                                save_ts=_save_ts,
                            ))
                        logger.info(
                            f"[SENSOR] sid={sid} temp={raw_temp}°C irr={raw_irr} W/m² body_temp={raw_body_temp}°C"
                        )
                    else:
                        logger.warning(f"[SENSOR] sid={sid} — no temperature or irradiance fields in payload")
                elif device and device.get("type") == "inverter" and device.get("active", True) is False:
                    logger.debug(f"[SKIP] sid={sid} sn={device.get('inverter_sn')} — marked inactive in device_registry")
                elif device and device.get("type") == "inverter":
                    prod_id = self._run_async(save_production_reading(
                        inverter_id=device["inverter_id"],
                        inverter_sn=device["inverter_sn"],
                        dtm=dtm,
                        data=entry,
                        sid=sid,
                    ))
                    if prod_id:
                        self.stats["inverter_processed"] += 1
                        logger.info(
                            f"[PROD] sid={sid} sn={device['inverter_sn']} -> id={prod_id}"
                        )

                        # Weather is now provided by physical sensors (SID 30 = temp, SID 91 = irradiance).
                        # Open-Meteo is disabled — sensor data is ground truth.
                        from .db_writer import _parse_dtm_as_utc
                        reading_ts = _parse_dtm_as_utc(dtm)

                        # Flushed/buffered data is handled automatically:
                        # pr_scheduler.py runs every 1 minute and detects any readings
                        # in inverter_readings that have no matching inverter_pr record,
                        # regardless of how old the timestamp is (looks back 7 days).
                        if reading_ts:
                            from datetime import datetime as _dt
                            delay_sec = (_dt.utcnow() - reading_ts).total_seconds()
                            if delay_sec > 300:
                                logger.info(
                                    f"[FLUSH] sid={sid} dtm={dtm} delay={int(delay_sec)}s — will be picked up by pr_scheduler gap detection"
                                )
                    else:
                        self.stats["errors"] += 1
            else:
                self.stats["errors"] += 1

        return any_saved

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
