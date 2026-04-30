#!/usr/bin/env python3
"""
MQTT Sniffer - Phase 1 Discovery Tool

Subscribes to ALL topics on the broker, prints every message to console,
and saves raw payloads to a JSON log file for analysis.

NO database writes. NO parsing. Just raw observation.

Usage:
    python sniffer.py                        # Connect to localhost:1883
    python sniffer.py --host 192.168.1.100   # Connect to specific broker
    python sniffer.py --host broker.example.com --port 1883 --user myuser --password mypass

The WT410M gateway publishes to topics like:  iot1/{IMEI}/event/
This tool subscribes to '#' (all topics) so you see EVERYTHING.

Output:
    - Console: colored real-time log of every message
    - File: mqtt_sniff_log.json (append mode, one JSON object per line)
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# Output file for raw message log
LOG_FILE = os.path.join(os.path.dirname(__file__), "mqtt_sniff_log.json")

message_count = 0
topics_seen = set()


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"\n{'='*70}")
        print(f"  CONNECTED to MQTT broker")
        print(f"  Subscribing to ALL topics (#)")
        print(f"  Log file: {LOG_FILE}")
        print(f"{'='*70}")
        print(f"\nWaiting for messages...\n")

        # Subscribe to EVERYTHING
        client.subscribe("#", 1)

        # Also specifically subscribe to WT410M patterns
        client.subscribe("iot1/#", 1)
        client.subscribe("iot2/#", 1)
    else:
        errors = {1: "Bad protocol", 2: "Bad client ID", 3: "Server unavailable",
                  4: "Bad credentials", 5: "Not authorized"}
        print(f"\nCONNECTION FAILED: {errors.get(rc, f'Unknown error (rc={rc})')}")
        print("Check your broker host, port, username, and password.")
        sys.exit(1)


def on_message(client, userdata, msg):
    global message_count, topics_seen
    message_count += 1

    topic = msg.topic
    topics_seen.add(topic)
    timestamp = datetime.now().isoformat()

    # Try to decode payload
    try:
        payload_str = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        payload_str = msg.payload.hex()

    # Try to parse as JSON
    payload_json = None
    try:
        payload_json = json.loads(payload_str)
        payload_display = json.dumps(payload_json, indent=2)
    except json.JSONDecodeError:
        payload_display = payload_str

    # Print to console
    print(f"{'─'*70}")
    print(f"  #{message_count} | {timestamp}")
    print(f"  Topic:   {topic}")
    print(f"  QoS:     {msg.qos}")
    print(f"  Retain:  {msg.retain}")
    print(f"  Size:    {len(msg.payload)} bytes")
    print(f"  Payload:")
    for line in payload_display.split("\n"):
        print(f"    {line}")
    print()

    # Save to log file (one JSON line per message)
    log_entry = {
        "n": message_count,
        "ts": timestamp,
        "topic": topic,
        "qos": msg.qos,
        "retain": msg.retain,
        "size": len(msg.payload),
        "payload_raw": payload_str,
        "payload_json": payload_json,
    }

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"\nDisconnected unexpectedly (rc={rc}). Reconnecting...")


def main():
    parser = argparse.ArgumentParser(description="MQTT Sniffer - see raw gateway messages")
    parser.add_argument("--host", default=os.getenv("MQTT_BROKER_HOST", "localhost"),
                        help="MQTT broker host (default: from .env or localhost)")
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_BROKER_PORT", "1883")),
                        help="MQTT broker port (default: 1883)")
    parser.add_argument("--user", default=os.getenv("MQTT_USERNAME", ""),
                        help="MQTT username")
    parser.add_argument("--password", default=os.getenv("MQTT_PASSWORD", ""),
                        help="MQTT password")
    parser.add_argument("--client-id", default="goodenergies_sniffer",
                        help="MQTT client ID")
    args = parser.parse_args()

    print(f"\n  MQTT Sniffer - GoodEnergies")
    print(f"  Broker: {args.host}:{args.port}")
    print(f"  Auth:   {'yes (' + args.user + ')' if args.user else 'no'}")
    print(f"  Log:    {LOG_FILE}")
    print()

    client = mqtt.Client(client_id=args.client_id)

    if args.user:
        client.username_pw_set(args.user, args.password)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(args.host, args.port, keepalive=60)
        client.loop_forever()
    except ConnectionRefusedError:
        print(f"\nCould not connect to {args.host}:{args.port}")
        print("Is the MQTT broker running? Try: docker-compose up -d")
    except KeyboardInterrupt:
        print(f"\n\n{'='*70}")
        print(f"  Sniffer stopped")
        print(f"  Total messages:  {message_count}")
        print(f"  Unique topics:   {len(topics_seen)}")
        if topics_seen:
            print(f"  Topics seen:")
            for t in sorted(topics_seen):
                print(f"    - {t}")
        print(f"  Log saved to:    {LOG_FILE}")
        print(f"{'='*70}\n")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
