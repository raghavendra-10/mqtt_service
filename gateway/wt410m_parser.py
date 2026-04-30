"""
WT410M Gateway Parser - Parses Modbus-to-MQTT JSON payloads from Wiman WT410M Lite gateway.

The WT410M reads Modbus registers from solar inverters via RS485 and publishes
to MQTT in JSON format. This module parses those payloads and converts them
to the GoodEnergies production schema format.

Topic pattern from gateway: iot1/{IMEI}/event/
JSON format: "JSON Format Standard" as configured in the gateway UI.

Supported WT410M JSON formats:
  1. Standard Event Format (register-based)
  2. Key-Value Format (when gateway is configured with named registers)
"""
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

import pytz

logger = logging.getLogger("wt410m_parser")

IST = pytz.timezone("Asia/Kolkata")


class WT410MParser:
    """
    Parses WT410M gateway MQTT payloads into normalized inverter/weather readings.

    The gateway sends Modbus slave data in JSON. The exact format depends on
    the gateway's "Event Format" configuration. This parser supports multiple
    formats and uses a register map to translate raw register values to
    meaningful field names.
    """

    def __init__(self, register_map: Dict, device_registry: Dict):
        """
        Args:
            register_map: Maps Modbus register addresses to field names + scaling.
            device_registry: Maps gateway IMEI to plant/inverter UUIDs.
        """
        self.register_map = register_map
        self.device_registry = device_registry

    def parse_topic(self, topic: str) -> Optional[str]:
        """
        Extract gateway IMEI/UID from topic.

        Topic patterns:
          - iot1/{IMEI}/event/
          - iot2/{IMEI}/event/
          - goodenergies/plants/{plant_id}/inverters/{inverter_id}/telemetry  (simulator)
        """
        parts = topic.strip("/").split("/")

        # WT410M format: iot1/{IMEI}/event or iot2/{IMEI}/event
        if len(parts) >= 3 and parts[0] in ("iot1", "iot2") and parts[2] == "event":
            return parts[1]

        # Simulator format: goodenergies/plants/{plant_id}/inverters/{inverter_id}/telemetry
        if len(parts) >= 6 and parts[0] == "goodenergies" and parts[-1] == "telemetry":
            return None  # Not a gateway message

        return None

    def is_gateway_message(self, topic: str) -> bool:
        """Check if topic matches WT410M gateway pattern."""
        return self.parse_topic(topic) is not None

    def parse_payload(self, topic: str, raw_payload: bytes) -> Optional[Dict]:
        """
        Parse a raw MQTT payload from the WT410M gateway.

        Returns a normalized dict with:
          - gateway_uid: IMEI of the gateway
          - timestamp: ISO timestamp
          - slaves: dict of {slave_id: {field_name: value}}
          - raw: original payload for logging

        Returns None if parsing fails.
        """
        gateway_uid = self.parse_topic(topic)
        if not gateway_uid:
            return None

        try:
            data = json.loads(raw_payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to decode payload from {gateway_uid}: {e}")
            return None

        # Try different WT410M JSON formats
        parsed = (
            self._parse_wiman_format(data, gateway_uid)
            or self._parse_standard_format(data, gateway_uid)
            or self._parse_register_array_format(data, gateway_uid)
            or self._parse_flat_format(data, gateway_uid)
        )

        if parsed:
            parsed["raw"] = data
            return parsed

        logger.warning(f"Unknown payload format from gateway {gateway_uid}: {list(data.keys())}")
        return None

    def _parse_wiman_format(self, data: Dict, uid: str) -> Optional[Dict]:
        """
        Parse actual Wiman WT410M JSON payload.

        Real format from the gateway:
        {
            "data": {
                "imei": "860710086613836",
                "uid": 1,
                "dtm": "20260427141000",
                "seq": 11,
                "msg": "log",
                "modbus": [
                    {"sid": 1, "stat": 0, "blk": 1, "rcnt": 5, "rval": [25000, 4500, 5000, 625, 0]},
                    {"sid": 2, "stat": 21, "blk": 1, "rcnt": 0}
                ]
            }
        }

        stat: 0 = success, 21 = no response from slave
        rval: array of register values (only when stat=0)
        rcnt: number of registers read
        """
        inner = data.get("data")
        if not inner or not isinstance(inner, dict):
            return None

        if "modbus" not in inner:
            return None

        imei = inner.get("imei", uid)
        dtm = inner.get("dtm", "")

        # Parse dtm: "20260427141000" → ISO timestamp
        if len(dtm) >= 14:
            try:
                dt = datetime.strptime(dtm[:14], "%Y%m%d%H%M%S")
                dt = IST.localize(dt)
                timestamp = dt.isoformat()
            except ValueError:
                timestamp = datetime.now(IST).isoformat()
        else:
            timestamp = datetime.now(IST).isoformat()

        slaves = {}
        for entry in inner.get("modbus", []):
            sid = entry.get("sid")
            stat = entry.get("stat", -1)
            rcnt = entry.get("rcnt", 0)
            rval = entry.get("rval", [])

            if sid is None:
                continue

            if stat != 0:
                # stat 21 = no slave response, log but skip
                logger.debug(f"Slave {sid} stat={stat} (no response)")
                # Still create an entry so we know the gateway is alive
                slaves[sid] = {"_status": stat, "_no_data": True}
                continue

            if rcnt > 0 and rval:
                # rval array format — map by register address
                start_addr = entry.get("addr", entry.get("start", None))
                if start_addr is not None:
                    registers = {}
                    for i, val in enumerate(rval):
                        registers[str(start_addr + i)] = val
                    mapped = self._apply_register_map(sid, registers, imei)
                else:
                    reg_map = self.register_map.get("default", {})
                    sorted_addrs = sorted(k for k in reg_map.keys() if k.isdigit())
                    registers = {}
                    for i, val in enumerate(rval):
                        if i < len(sorted_addrs):
                            registers[sorted_addrs[i]] = val
                    mapped = self._apply_register_map(sid, registers, imei)
                slaves[sid] = mapped

            elif rcnt > 0:
                # Named fields format — gateway sends field names directly
                # e.g. {"sid":1,"stat":0,"rcnt":5,"Daily energy yield":261,"activePower":47,...}
                skip_keys = {"sid", "stat", "blk", "rcnt", "rval", "addr", "start"}
                named_data = {}
                # Map common gateway field names to our schema names
                field_aliases = {
                    "daily energy yield": "dailyPowerYield",
                    "dailyenergyyield": "dailyPowerYield",
                    "daily_energy_yield": "dailyPowerYield",
                    "activepower": "activePower",
                    "active_power": "activePower",
                    "rcurrent": "rCurrent",
                    "r_current": "rCurrent",
                    "faultid": "faultId",
                    "fault_id": "faultId",
                    "fault": "faultId",
                    "frequency": "frequency",
                    "freq": "frequency",
                }
                for key, val in entry.items():
                    if key.lower() in skip_keys:
                        continue
                    # Try alias lookup
                    normalized = field_aliases.get(key.lower().replace(" ", "").replace("_", ""), key)
                    named_data[normalized] = val

                if named_data:
                    slaves[sid] = named_data
                else:
                    slaves[sid] = {"_status": stat, "_no_data": True}

            else:
                slaves[sid] = {"_status": stat, "_no_data": True}

        # Filter out slaves with no data
        active_slaves = {k: v for k, v in slaves.items() if not v.get("_no_data")}

        if not active_slaves and slaves:
            # All slaves have no data (stat != 0) — still log the heartbeat
            logger.info(f"Gateway {imei} alive | {len(slaves)} slaves polled, none responding (no RS485 device connected)")
            return None

        if not active_slaves:
            return None

        return {
            "gateway_uid": imei,
            "timestamp": timestamp,
            "slaves": active_slaves,
        }

    def _parse_standard_format(self, data: Dict, uid: str) -> Optional[Dict]:
        """
        Parse WT410M "JSON Format Standard" payload.

        Expected format:
        {
            "UID": "86XXXXXXXXXX",
            "D": "20260421",
            "T": "123000",
            "S1": {"40001": 1234, "40002": 5678, ...},  // Slave 1 registers
            "S2": {"40001": ...},                         // Slave 2 registers
            ...
        }

        Or variant:
        {
            "uid": "86XXXXXXXXXX",
            "date": "2026-04-21",
            "time": "12:30:00",
            "slave_1": {...},
            ...
        }
        """
        # Check for UID field (case-insensitive)
        has_uid = any(k.upper() == "UID" for k in data.keys())
        has_slaves = any(k.upper().startswith("S") and k[1:].isdigit() for k in data.keys())

        if not (has_uid or has_slaves):
            return None

        # Extract timestamp
        timestamp = self._extract_timestamp(data)

        # Extract slave data
        slaves = {}
        for key, value in data.items():
            upper_key = key.upper()

            # Match S1, S2, ... or SLAVE_1, SLAVE_2, ...
            slave_id = None
            if upper_key.startswith("S") and upper_key[1:].isdigit():
                slave_id = int(upper_key[1:])
            elif upper_key.startswith("SLAVE_") and upper_key[6:].isdigit():
                slave_id = int(upper_key[6:])
            elif upper_key.startswith("SLAVE") and upper_key[5:].isdigit():
                slave_id = int(upper_key[5:])

            if slave_id is not None and isinstance(value, dict):
                mapped = self._apply_register_map(slave_id, value, uid)
                slaves[slave_id] = mapped

        if not slaves:
            return None

        return {
            "gateway_uid": uid,
            "timestamp": timestamp,
            "slaves": slaves,
        }

    def _parse_register_array_format(self, data: Dict, uid: str) -> Optional[Dict]:
        """
        Parse WT410M array-based register format.

        Expected format:
        {
            "uid": "86XXXXXXXXXX",
            "ts": 1713700200,
            "data": [
                {"slave_id": 1, "fc": 3, "addr": 40001, "count": 10, "values": [1234, 5678, ...]},
                {"slave_id": 1, "fc": 3, "addr": 40100, "count": 5, "values": [...]},
            ]
        }
        """
        if "data" not in data or not isinstance(data["data"], list):
            return None

        # Check if data items have slave_id and values
        first_item = data["data"][0] if data["data"] else {}
        if not ("slave_id" in first_item or "slaveId" in first_item):
            return None

        timestamp = self._extract_timestamp(data)

        # Build register map from array
        slaves = {}
        for item in data["data"]:
            slave_id = item.get("slave_id") or item.get("slaveId")
            if slave_id is None:
                continue

            start_addr = item.get("addr") or item.get("address", 0)
            values = item.get("values", [])

            # Build address → value map
            register_values = {}
            for i, val in enumerate(values):
                addr = str(start_addr + i)
                register_values[addr] = val

            mapped = self._apply_register_map(slave_id, register_values, uid)

            if slave_id not in slaves:
                slaves[slave_id] = {}
            slaves[slave_id].update(mapped)

        if not slaves:
            return None

        return {
            "gateway_uid": uid,
            "timestamp": timestamp,
            "slaves": slaves,
        }

    def _parse_flat_format(self, data: Dict, uid: str) -> Optional[Dict]:
        """
        Parse a simple flat key-value format.

        Expected format (when gateway is configured with custom key names):
        {
            "uid": "86XXXXXXXXXX",
            "timestamp": "2026-04-21T12:30:00",
            "active_power": 425.5,
            "daily_yield": 2450.75,
            "irradiance": 750.0,
            "temperature": 32.5,
            ...
        }

        This is a pass-through format where the gateway is pre-configured
        with meaningful field names.
        """
        # Check for common power-related keys
        power_keys = {"active_power", "activePower", "power", "daily_yield", "dailyPowerYield",
                      "daily_power_yield", "totalPowerYield", "total_power_yield"}
        weather_keys = {"irradiance", "ghi", "poa", "ambient_temperature", "ambientTemperature", "temperature"}

        has_power = bool(power_keys & set(data.keys()))
        has_weather = bool(weather_keys & set(data.keys()))

        if not (has_power or has_weather):
            return None

        timestamp = self._extract_timestamp(data)

        # Treat the entire payload as slave 1 data
        return {
            "gateway_uid": uid,
            "timestamp": timestamp,
            "slaves": {1: data},
        }

    def _extract_timestamp(self, data: Dict) -> str:
        """Extract and normalize timestamp from payload."""
        # Try various timestamp fields
        ts = (
            data.get("timestamp")
            or data.get("ts")
            or data.get("T")
            or data.get("time")
        )
        date_str = data.get("D") or data.get("date")

        if isinstance(ts, (int, float)):
            # Unix timestamp
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.astimezone(IST).isoformat()

        if date_str and isinstance(ts, str) and len(ts) <= 6:
            # D=20260421, T=123000 format
            try:
                combined = f"{date_str}{ts}".replace("-", "").replace(":", "")
                if len(combined) == 14:
                    dt = datetime.strptime(combined, "%Y%m%d%H%M%S")
                elif len(combined) == 12:
                    dt = datetime.strptime(combined, "%Y%m%d%H%M")
                else:
                    dt = datetime.now()
                dt = IST.localize(dt)
                return dt.isoformat()
            except ValueError:
                pass

        if isinstance(ts, str):
            return ts

        # Default to now
        return datetime.now(IST).isoformat()

    def _apply_register_map(self, slave_id: int, registers: Dict, gateway_uid: str) -> Dict:
        """
        Convert raw Modbus register values to named fields using the register map.

        Args:
            slave_id: Modbus slave ID
            registers: Dict of {register_address: raw_value}
            gateway_uid: Gateway IMEI for looking up device-specific maps

        Returns:
            Dict of {field_name: scaled_value}
        """
        result = {}

        # Look up register map for this gateway + slave
        map_key = f"{gateway_uid}:{slave_id}"
        reg_map = (
            self.register_map.get(map_key)
            or self.register_map.get(f"*:{slave_id}")
            or self.register_map.get("default")
            or {}
        )

        for addr, raw_value in registers.items():
            addr_str = str(addr)

            if addr_str in reg_map:
                mapping = reg_map[addr_str]
                field_name = mapping["field"]
                scale = mapping.get("scale", 1.0)
                offset = mapping.get("offset", 0)
                data_type = mapping.get("type", "float")

                try:
                    if data_type == "int":
                        value = int(raw_value) * scale + offset
                    elif data_type == "uint32":
                        # Two consecutive 16-bit registers combined
                        value = int(raw_value) * scale + offset
                    else:
                        value = float(raw_value) * scale + offset
                    result[field_name] = round(value, 4)
                except (ValueError, TypeError):
                    logger.warning(f"Bad value for register {addr_str}: {raw_value}")
            else:
                # Pass through if it's already a named field (flat format)
                if not addr_str.isdigit():
                    result[addr_str] = raw_value

        return result

    def resolve_devices(self, gateway_uid: str, slaves: Dict) -> List[Tuple[str, str, str, int, str, Dict]]:
        """
        Resolve gateway UID + slave IDs to production plant/device UUIDs.

        Returns list of (plant_id, inverter_id, inverter_sn, slave_id, device_type, data) tuples.
        device_type is one of: 'inverter', 'meter', 'load', 'weather' (from device_registry.json).
        """
        # Look up by exact IMEI first, then fall back to TEST_GATEWAY
        devices = self.device_registry.get(gateway_uid)
        if not devices:
            devices = self.device_registry.get("TEST_GATEWAY", {})
            if devices:
                logger.info(f"Using TEST_GATEWAY fallback for {gateway_uid}")

        results = []

        for slave_id, data in slaves.items():
            slave_key = str(slave_id)
            device = devices.get(slave_key)

            if device and isinstance(device, dict) and "type" in device:
                results.append((
                    device["plant_id"],
                    device.get("inverter_id", ""),
                    device.get("inverter_sn", ""),
                    int(slave_id) if str(slave_id).isdigit() else slave_id,
                    device["type"],
                    data,
                ))
            else:
                logger.warning(
                    f"No mapping for gateway {gateway_uid} slave {slave_id}. "
                    f"Add it to device_registry.json."
                )

        return results
