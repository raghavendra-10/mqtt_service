# MQTT Service — Changes Required for New Simulator Output

**Trigger:** `modbus_simulator/inverter_sim.py` now emits **3 slaves** (inverter / meter / load) with new register addresses that don't line up with the old `register_map.json`.

---

## 1. Address mismatch (off-by-one)

The old config used **1-based** addresses (as printed in inverter manuals); the simulator uses **0-based** addresses (because `zero_mode=True` in pymodbus, `inverter_sim.py:118`). Result: every register was off by one.

| Field            | Old map (1-based) | New sim (0-based) |
|------------------|-------------------|-------------------|
| dailyPowerYield  | 5003              | **5002**          |
| activePower      | 5008              | **5007**          |
| frequency        | 5017              | **5016**          |
| rCurrent (slv 1) | 5022              | **5021**          |
| faultId          | 5035              | **5034**          |

→ **Fixed:** added per-slave map `*:1` to `register_map.json` with 0-based addresses. Old `default` retained as legacy fallback.

---

## 2. New slaves (2 = meter, 3 = load)

The simulator now exposes:

- **Slave 2** — 3-phase currents (5022/5023/5024), plus 32-bit meterPower (5083+5084) and loadPower (5091+5092).
- **Slave 3** — 3-phase voltages and currents (5011–5016).

→ **Fixed:** added `*:2` and `*:3` maps in `register_map.json`. Added slaves 2 & 3 to `TEST_GATEWAY` in `device_registry.json`.

---

## 3. **OPEN ISSUE — uint32 not actually combined**

In `gateway/wt410m_parser.py:466-467`:

```python
elif data_type == "uint32":
    # Two consecutive 16-bit registers combined
    value = int(raw_value) * scale + offset
```

The comment says "Two consecutive 16-bit registers combined", but the code doesn't combine anything — it just scales the single register. So `meterPower = 3000 W` arrives as **two separate fields** `meterPowerLow = 3000`, `meterPowerHigh = 0` instead of one combined `meterPower = 3000`.

The simulator stores 32-bit values in **little-endian word order** (`inverter_sim.py:69-74`):
```python
data[5083] = meter_power & 0xFFFF        # low word
data[5084] = (meter_power >> 16) & 0xFFFF # high word
```

### Fix needed in parser (`_apply_register_map`)

Either:
- **Option A** — Add a register-map entry of type `"uint32"` that names the *low* address and references the high address explicitly:
  ```json
  "5083": {"field": "meterPower", "type": "uint32", "high_addr": "5084", "scale": 1, "byte_order": "little"}
  ```
  Then in `_apply_register_map`, when type is `uint32`, look up `registers[high_addr]` and combine: `(hi << 16) | lo`.
- **Option B** — Keep low/high split in config (current state) and combine in `event_handler` / DB writer.

Option A is cleaner. Until then, downstream consumers will see `meterPowerLow` / `meterPowerHigh` and need to combine themselves.

---

## 4. **OPEN ISSUE — fallback path ignores per-slave maps**

In `_parse_wiman_format` (`wt410m_parser.py:178-184`), when the gateway sends `rval` **without an `addr` field**, the parser falls back to:

```python
reg_map = self.register_map.get("default", {})
sorted_addrs = sorted(k for k in reg_map.keys() if k.isdigit())
```

This **only reads the `default` map** — it ignores `*:1`, `*:2`, `*:3`. So if your WT410M gateway is configured to send register arrays without explicit addresses, slaves 2 and 3 will be misparsed.

### Fix needed

Replace the fallback to use the slave-specific map:
```python
reg_map = (
    self.register_map.get(f"*:{sid}")
    or self.register_map.get("default")
    or {}
)
sorted_addrs = sorted(k for k in reg_map.keys() if k.isdigit())
```

**Recommended:** Configure the WT410M to include `addr` in each modbus block — the array-without-address path is fragile and should be considered a last-resort fallback.

---

## 5. Dynamic "tick" registers

The simulator's `5007` (slave 1), `5022` (slave 2), `5011` (slave 3) cycle 0–59 (×0.1) every 3 s as a **liveness heartbeat**, not real measurements. We mapped:

- `5007` → `activePower` (slave 1) — values will sweep 0–59 W, **not realistic**
- `5022` → `rCurrent` (slave 2) — values sweep 0–5.9 A
- `5011` → `phaseA_tick` (slave 3) — kept as a debug/tick field

If you need realistic activePower for testing dashboards, either patch the simulator to compute power from irradiance, or use the existing `simulator/data_generator.py` instead of the RTU simulator.

---

## 6. Files touched

- `mqtt_service/config/register_map.json` — added `*:1`, `*:2`, `*:3` blocks; kept `default` as legacy.
- `mqtt_service/config/device_registry.json` — added slaves 2 & 3 to `TEST_GATEWAY`.

## 7. Files NOT yet touched (need code change)

- `mqtt_service/gateway/wt410m_parser.py` — uint32 combination (issue #3) and fallback per-slave map (issue #4).

---

## Quick verification

After these changes, run:
```
python main.py sniff
```
…and confirm payloads arrive parsed as e.g. `{"dailyPowerYield": 2700.0, "frequency": 50.0, ...}` instead of raw register addresses.
