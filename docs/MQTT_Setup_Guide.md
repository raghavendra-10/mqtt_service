# GoodEnergies MQTT Service - Setup Guide

## Overview

This guide covers the complete setup for connecting a solar inverter (simulated or real) to the GoodEnergies monitoring platform via a WT410M Modbus-to-MQTT data logger.

**Data Flow:**
```
Solar Inverter (or Laptop Simulator)
        |
        | RS485 (A+, B-, GND)
        v
WT410M Data Logger (Modbus RTU Master)
        |
        | 4G/LTE
        v
MQTT Broker: mqtt.goodenergies.in:1883
        |
        v
MQTT Subscriber Service (parses JSON, writes to DB)
        |
        v
PostgreSQL Database -> Dashboard
```

---

## PART 1: Modbus Inverter Simulator (Laptop as Slave)

### 1.1 What This Does

Your laptop acts as a fake solar inverter. It responds to Modbus RTU requests from the WT410M data logger over RS485, serving 5 test registers with realistic solar data.

### 1.2 Hardware Required

- Laptop (macOS/Linux/Windows)
- USB-to-RS485 adapter (CH340, CP2102, FTDI, or PL2303 chip)
- 2-3 wires (A+, B-, GND)
- WT410M data logger

### 1.3 Wiring Diagram

```
Laptop                              WT410M Data Logger
+----------+                        +------------------+
|          |   USB-to-RS485         |                  |
|   USB ---+-->[Adapter]---> A+ ----+-- RS485 A+       |
|          |             ---> B- ----+-- RS485 B-       |
|          |             ---> GND ---+-- GND            |
|          |                        |                  |
| (Slave)  |                        |  (Master/Poller) |
+----------+                        +------------------+
```

**Important:** Only 3 wires needed: A+, B-, and GND.

### 1.4 Software Installation

```bash
# Clone or copy the modbus_simulator folder to your laptop
cd modbus_simulator

# Create virtual environment
python3 -m venv venv
source venv/bin/activate        # macOS/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install pymodbus==3.5.4 pyserial
```

### 1.5 Running the Simulator

```bash
# Plug in your USB-to-RS485 adapter first, then:
python3 inverter_sim.py
```

It auto-detects the RS485 port. To specify manually:
```bash
python3 inverter_sim.py --port /dev/tty.usbserial-XXXX    # macOS
python3 inverter_sim.py --port /dev/ttyUSB0                # Linux
python3 inverter_sim.py --port COM3                         # Windows
```

### 1.6 What You Should See

```
============================================================
  MODBUS INVERTER SIMULATOR
============================================================
  Port:      /dev/tty.usbserial-14210
  Baud:      9600
  Slave ID:  1
  Registers:
    [5003] dailyPowerYield (kWh)
    [5008] activePower (kW)
    [5017] frequency (Hz)
    [5022] rCurrent (A)
    [5035] faultId

  Waiting for WT410M to poll...
  Ctrl+C to stop
============================================================

2026-04-21 12:30:05  Sun: 85% | Power: 382.5 kW | Daily: 2125.0 kWh | Current: 53.1 A | Freq: 50.00 Hz | Fault: 0
```

Register values update every 5 seconds following a sun curve (0% at 6AM/6PM, 100% at noon).

### 1.7 Registers Served

| Register | Field           | Scale | Unit | Example Raw | Example Real |
|----------|-----------------|-------|------|-------------|--------------|
| 5003     | dailyPowerYield | x0.1  | kWh  | 25000       | 2500.0 kWh   |
| 5008     | activePower     | x0.1  | kW   | 4500        | 450.0 kW     |
| 5017     | frequency       | x0.01 | Hz   | 5000        | 50.00 Hz     |
| 5022     | rCurrent        | x0.1  | A    | 625         | 62.5 A       |
| 5035     | faultId         | x1    | -    | 0           | 0 (no fault) |

### 1.8 Command Line Options

| Option       | Default | Description                    |
|--------------|---------|--------------------------------|
| `--port`     | auto    | Serial port path               |
| `--slave-id` | 1       | Modbus slave address (1-247)   |
| `--baud`     | 9600    | Baud rate                      |

---

## PART 2: WT410M Data Logger Configuration

### 2.1 RS485 / Modbus Polling Settings

Configure the WT410M to poll the inverter simulator (or real inverter):

| Setting          | Value    |
|------------------|----------|
| **Mode**         | Polling  |
| **Baud Rate**    | 9600     |
| **Data Bits**    | 8        |
| **Parity**       | None     |
| **Stop Bits**    | 1        |

### 2.2 Slave Register Configuration

Add these polling entries (Slave ID 1, Function Code 03 - Read Holding Registers):

| Slave ID | FC | Start Address | Count | Description        |
|----------|----|---------------|-------|--------------------|
| 1        | 03 | 5003          | 1     | Daily energy yield |
| 1        | 03 | 5008          | 1     | Active power       |
| 1        | 03 | 5017          | 1     | Grid frequency     |
| 1        | 03 | 5022          | 1     | Phase R current    |
| 1        | 03 | 5035          | 1     | Fault code         |

**Alternative:** If your gateway supports range polling, use Start Address: 5003, Count: 33 (covers all registers in one read).

### 2.3 Server IP Settings Tab

| Setting              | Current Value     | Change To                   |
|----------------------|-------------------|-----------------------------|
| **Connect Protocol** | MQTT              | MQTT (keep)                 |
| **Server IP/URL**    | www.iotwnet.in    | **mqtt.goodenergies.in**    |
| **Server Port**      | 1883              | **1883** (keep)             |
| **SSL Security**     | Unchecked         | Unchecked (keep)            |
| **IP Connect Mode**  | Always Online     | Always Online (keep)        |

**Secondary Server IP2:** Leave disabled (unchecked).

### 2.4 Event Format Settings

| Setting                      | Value                    |
|------------------------------|--------------------------|
| **Event Push Interval (Sec)**| **0** (immediate)        |
| **Ping Msg Interval (Sec)**  | 180 (keep default)       |
| **Ping Message**             | PING (keep default)      |
| **Primary Event Format**     | **JSON Format Standard** |
| **Primary Data Logging**     | **Enable**               |
| **Secondary Event Format**   | JSON Format Standard     |
| **Secondary Data Logging**   | Disable                  |
| **Secondary Transparent**    | Unchecked                |

### 2.5 MQTT Client Tab (Primary Client)

| Setting                 | Value                        |
|-------------------------|------------------------------|
| **Broker IP/Domain**    | **mqtt.goodenergies.in**     |
| **Port**                | **1883**                     |
| **DeviceID/ClientID**   | Client-$UID (keep default)   |
| **Auth. Enable**        | **Unchecked** (no auth)      |
| **User Name**           | (leave as-is)                |
| **Password**            | (leave as-is)                |
| **QOS Level**           | **1**                        |

**Topics for Event/Configuration (keep defaults):**

| Setting                | Value                  |
|------------------------|------------------------|
| **Publish Event**      | iot1/$UID/event/       |
| **Subscribe Event Reply** | iot1/$UID/event_ack/ |
| **Publish Command Reply** | iot1/$UID/cmd_reply/ |
| **Subscribe Command**  | iot1/$UID/cmd_send/    |

> **Note:** `$UID` is automatically replaced by the gateway's IMEI number.

**Secondary Client:** Leave as default (not used).

### 2.6 Summary of Changes from Default

Only 2 fields need to change:

| Field          | Old Value      | New Value                |
|----------------|----------------|--------------------------|
| Server IP/URL  | www.iotwnet.in | **mqtt.goodenergies.in** |
| Broker IP/Domain | (empty)      | **mqtt.goodenergies.in** |

Everything else stays at default values.

---

## PART 3: Verifying the Setup

### 3.1 Expected Data Flow

Once everything is connected:

1. **Simulator** serves register values over RS485
2. **WT410M** polls every few seconds, reads registers
3. **WT410M** publishes JSON to `iot1/{IMEI}/event/` on `mqtt.goodenergies.in:1883`
4. **MQTT subscriber** receives, parses, and writes to database

### 3.2 Expected JSON from WT410M

The gateway publishes messages in this format:
```json
{
  "UID": "868XXXXXXXXXX",
  "D": "20260421",
  "T": "143000",
  "S1": {
    "5003": 25000,
    "5008": 4500,
    "5017": 5000,
    "5022": 625,
    "5035": 0
  }
}
```

Where:
- `UID` = Gateway IMEI number
- `D` = Date (YYYYMMDD)
- `T` = Time (HHMMSS)
- `S1` = Slave 1 register data (address: raw_value)

### 3.3 How to Verify MQTT Messages

From any machine with mosquitto-clients installed:
```bash
# Subscribe to all gateway messages
mosquitto_sub -h mqtt.goodenergies.in -p 1883 -t "iot1/#" -v
```

You should see messages appearing when the gateway polls.

### 3.4 How to Verify Database

After messages flow in, check the test table:
```sql
SELECT gateway_uid, inverter_sn, "activePower", "dailyPowerYield",
       frequency, "rCurrent", "faultId", created_at
FROM mqtt_test_readings
ORDER BY created_at DESC
LIMIT 10;
```

---

## PART 4: Troubleshooting

### Simulator Issues

| Problem | Solution |
|---------|----------|
| "No USB serial port found" | Plug in the RS485 adapter. Check with `ls /dev/tty.usb*` (macOS) or `ls /dev/ttyUSB*` (Linux) |
| Wrong port detected | Use `--port /dev/tty.usbserial-XXXX` explicitly |
| "Permission denied" on port | Run `sudo chmod 666 /dev/ttyUSB0` (Linux) or check System Preferences > Security (macOS) |
| Registers not being read | Verify baud rate matches (9600) and slave ID matches (1) |

### WT410M Issues

| Problem | Solution |
|---------|----------|
| Gateway not connecting to broker | Verify Server IP = mqtt.goodenergies.in, Port = 1883 |
| No MQTT messages appearing | Check Event Push Interval = 0, Event Format = JSON Format Standard |
| RS485 communication error | Check wiring: A+ to A+, B- to B-, GND to GND. Check baud rate = 9600 |
| Wrong register values | Verify Function Code = 03, Start Address and Count match table above |

### MQTT Broker Info

| Detail | Value |
|--------|-------|
| **Hostname** | mqtt.goodenergies.in |
| **IP Address** | 44.248.65.254 |
| **Port** | 1883 (TCP, no TLS) |
| **Authentication** | None (anonymous allowed) |
| **QoS** | 1 (at least once delivery) |

---

## Quick Reference Card

```
SIMULATOR:
  cd modbus_simulator
  python3 inverter_sim.py --slave-id 1 --baud 9600

WT410M CONFIG:
  Server IP:     mqtt.goodenergies.in
  Server Port:   1883
  Protocol:      MQTT
  Auth:          Disabled
  Event Format:  JSON Format Standard
  Push Interval: 0 (immediate)
  Modbus:        Slave 1, FC 03, Registers 5003/5008/5017/5022/5035

VERIFY:
  mosquitto_sub -h mqtt.goodenergies.in -p 1883 -t "iot1/#" -v
```
