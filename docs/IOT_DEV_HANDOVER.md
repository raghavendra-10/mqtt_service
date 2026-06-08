# WT410M Gateway → MQTT Service Handover

Everything the IoT/gateway developer needs to publish data into the GoodEnergies MQTT subscriber.

---

## TL;DR — Where Data Lands

| Payload variant | Discriminator | PostgreSQL table |
|---|---|---|
| **Full snapshot** (all registers, ~80+ fields) | modbus entry **contains** key `"Device type code"` | **`inverter_full_log`** |
| **Realtime delta** (hot fields only) | modbus entry **does not** contain `"Device type code"` | **`inverter_realtime_log`** |

Both tables share the same shape: `id, gateway_imei, uid, sid, dtm, seq, topic, data (JSONB), received_at`.

The full register payload is preserved verbatim in the `data` JSONB column — no field is dropped.

---

## 1. MQTT Broker Connection

| Setting | Value |
|---|---|
| **Host / IP** | _**TBD — share the public IP or DNS of the broker host**_ |
| **Port** | `1883` (plaintext MQTT) |
| **WebSocket port** | `9001` (only if needed) |
| **TLS** | Not enabled yet |
| **Username** | _(none — anonymous allowed)_ |
| **Password** | _(none)_ |
| **QoS** | `1` recommended (at-least-once) |
| **Keepalive** | `60` seconds |
| **Client ID** | Anything unique per gateway, e.g. `wt410m-{IMEI}` |
| **Clean session** | `true` |
| **Retain** | `false` (do not set retain on event messages) |

⚠️ **Action item before handover:** the broker currently runs on `localhost` (Mosquitto in Docker). For a real device to reach it you must either:
- Run Mosquitto on a public host (EC2, VPS) and open port `1883`, or
- Use a managed broker (HiveMQ Cloud, EMQX Cloud, AWS IoT Core), or
- Put the gateway on the same LAN/VPN as the broker host.

---

## 2. Topic

Publish to **anything under `iot1/...` or `iot2/...`**. The service subscribes to `iot1/#` and `iot2/#` (multi-level wildcard).

Recommended pattern (already used by Wiman default firmware):

```
iot1/{IMEI}/event/
```

Examples that all work:
- `iot1/860710086613836/event/`
- `iot1/860710086613836/log`
- `iot1/test`
- `iot2/anything`

**The IMEI is read from the JSON payload (`data.imei`), not from the topic** — so the topic shape is flexible.

---

## 3. Payload Format (JSON)

Standard "Wiman / WT410M JSON Format". One message per polling cycle.

```json
{
  "data": {
    "imei": "860710086613836",
    "uid": 1,
    "dtm": "20260507143005",
    "seq": 495,
    "msg": "log",
    "modbus": [
      { "sid": 1, "stat": 0, "rcnt": 25, "Device type code": 0, "...": "..." }
    ]
  }
}
```

### Field meanings

| Field | Meaning | Required |
|---|---|---|
| `data.imei` | Gateway IMEI (15 digits) | **yes** |
| `data.uid` | Numeric gateway ID | recommended |
| `data.dtm` | Timestamp `yyyymmddHHMMSS` (IST) | **yes** |
| `data.seq` | Monotonic sequence number per gateway | **yes** |
| `data.msg` | Always `"log"` for telemetry | yes |
| `data.modbus[]` | Array — one entry per Modbus slave polled | **yes** |

### `modbus[]` entry

| Field | Meaning |
|---|---|
| `sid` | Modbus slave ID (1, 2, …) |
| `stat` | `0` = success, `21` = no response (slave offline). **We only persist `stat=0` entries.** |
| `rcnt` | Register count read |
| `<Field name>: <value>` | Named registers — see §4 |

---

## 4. Two Payload Variants — IMPORTANT

The subscriber routes each `modbus[]` entry to **one of two tables** based on payload contents:

### Variant A — Full Snapshot → `inverter_full_log`

Identified by **the presence of the key `"Device type code"`** in the modbus entry.

Use this for:
- Periodic full register dumps (e.g. once a minute)
- Includes nameplate fields, MPPT 1–12, strings, energy totals, fault codes, etc.

Expected fields (subset — full list in [`config/register_map.json`](../config/register_map.json)):

```
Device type code, Nominal active pow, Output type, Daily power yields,
Total power yields, Total running time, Internal temperatu,
Total apparent pow, MPPT 1..12 voltage/current, Total DC power,
A-B / B-C / C-A line voltage, Phase A/B/C current, Total active power,
Total reactive pow, Power factor, Grid frequency, Work state, Meter power,
Daily/Total export/import/direct energy, String 1..7 current, ...
```

### Variant B — Realtime Delta → `inverter_realtime_log`

**Any modbus entry without `"Device type code"`** routes here.

Use this for:
- High-frequency polls of just the hot fields (e.g. every 5–10 s)
- Smaller payloads, less bandwidth

Minimum recommended fields:

```
Total active power, Total reactive pow, Power factor, Grid frequency
```

You can include more fields — they'll be saved in the JSONB blob regardless.

---

## 5. Naming Conventions for Register Fields

- **Use the exact field names from the WT410M "JSON Format Standard"** (e.g. `"Total active power"`, `"Power factor"`, `"Grid frequency"`).
- Keys are case-sensitive.
- If a name is truncated by gateway firmware (e.g. `"Total reactive pow"`, `"Internal temperatu"`), keep them as-is — we store them verbatim in JSONB.
- **The discriminator key is literally `"Device type code"` (with that exact spelling and capitalization).** Don't rename it or the variant routing breaks.

---

## 6. What Happens On Our Side

1. Subscriber receives the message on `iot1/#` or `iot2/#`.
2. Validates JSON shape and that `data.modbus[]` exists.
3. For each modbus entry with `stat == 0`:
   - Classifies by `"Device type code"` presence.
   - Inserts a row into `inverter_full_log` or `inverter_realtime_log` with:
     - `gateway_imei`, `uid`, `sid`, `dtm`, `seq`, `topic`
     - `data` (the entire modbus entry as JSONB)
     - `received_at` (server time)
4. Logs `[FULL]` or `[REALTIME]` to console with the row id.

`stat != 0` entries are skipped (gateway alive, slave offline).

---

## 7. Quick Sanity Test

Before pointing the real gateway, verify with `mosquitto_pub`:

```bash
# Variant A — Full
mosquitto_pub -h <BROKER_IP> -t iot1/test -m '{
  "data": {
    "imei":"860710086613836","uid":1,"dtm":"20260507143005","seq":1,"msg":"log",
    "modbus":[{"sid":1,"stat":0,"rcnt":25,"Device type code":0,
               "Total active power":3922722816,"Power factor":-0,
               "Grid frequency":100}]
  }
}'

# Variant B — Realtime
mosquitto_pub -h <BROKER_IP> -t iot1/test -m '{
  "data": {
    "imei":"860710086613836","uid":1,"dtm":"20260507143010","seq":2,"msg":"log",
    "modbus":[{"sid":1,"stat":0,"rcnt":4,
               "Total active power":3922722816,"Total reactive pow":-661913596,
               "Power factor":-0,"Grid frequency":100}]
  }
}'
```

You should see `[FULL]` then `[REALTIME]` log lines on the subscriber, and rows in the corresponding tables.

---

## 8. Open Questions to Resolve With the IoT Dev

- [ ] Which broker host/IP will the gateway point to?
- [ ] Will multiple gateways publish? Confirm IMEI is unique per device.
- [ ] Polling cadence: how often does Variant A fire vs. Variant B?
- [ ] Is the gateway clock NTP-synced? (`dtm` is trusted as the canonical timestamp.)
- [ ] Are there device types other than inverters (meter, weather sensor) on the same gateway? If so, which `sid` is which?
- [ ] Do they need TLS / auth before going to production?

---

## 9. Deployment Plan (to be done)

Currently this service runs locally. Production target is an **existing EC2 instance**.

Pending tasks before the IoT dev can point a real gateway:

- [ ] Deploy the MQTT service (subscriber + Mosquitto broker) onto the EC2 host.
- [ ] Open inbound TCP `1883` on the EC2 security group (and `9001` only if WebSocket is needed).
- [ ] Decide on auth: enable `password_file` in `mosquitto.conf` and disable `allow_anonymous` before exposing publicly.
- [ ] Optional but recommended: TLS on port `8883` with a Let's Encrypt cert (or AWS-managed cert via NLB).
- [ ] Set `DATABASE_URL` in the EC2 `.env` to the dev RDS (already reachable from `us-west-2`).
- [ ] Run the migration on RDS (already applied as of 2026-05-07).
- [ ] Run subscriber under `systemd` or `pm2` with auto-restart.
- [ ] Hand the EC2 broker host/port (and creds, if enabled) to the IoT dev — fill in §1 above.

