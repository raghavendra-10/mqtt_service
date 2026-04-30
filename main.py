#!/usr/bin/env python3
"""
GoodEnergies MQTT Service - Main Entry Point

Connects to MQTT broker, subscribes to WT410M gateway topics,
parses Modbus register data, and writes to production database.

Usage:
    python main.py sniff           # Phase 1: See raw messages (NO DB writes)
    python main.py subscribe       # Phase 2: Parse + write to production DB
    python main.py simulator       # Run the data simulator (for testing)
    python main.py all             # Run subscriber + simulator together
    python main.py api             # Run the debug API server
"""
import sys
import json
import time
import signal
import logging
import threading
import os

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("main")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def load_config():
    """Load register map and device registry from config files."""
    register_map_path = os.path.join(CONFIG_DIR, "register_map.json")
    device_registry_path = os.path.join(CONFIG_DIR, "device_registry.json")

    register_map = {}
    device_registry = {}

    if os.path.exists(register_map_path):
        with open(register_map_path) as f:
            register_map = json.load(f)
        # Remove comment keys
        register_map = {k: v for k, v in register_map.items() if not k.startswith("_")}
        logger.info(f"Loaded register map: {len(register_map)} entries")
    else:
        logger.warning(f"No register map found at {register_map_path}")

    if os.path.exists(device_registry_path):
        with open(device_registry_path) as f:
            device_registry = json.load(f)
        # Remove comment keys
        device_registry = {k: v for k, v in device_registry.items() if not k.startswith("_")}
        logger.info(f"Loaded device registry: {len(device_registry)} gateways")
    else:
        logger.warning(f"No device registry found at {device_registry_path}")

    return register_map, device_registry


def run_subscriber():
    """Run the MQTT subscriber — connects to broker and processes messages."""
    from gateway.wt410m_parser import WT410MParser
    from subscriber.event_handler import init_event_handler
    from subscriber.mqtt_subscriber import MQTTSubscriber

    register_map, device_registry = load_config()

    # Initialize gateway parser
    parser = WT410MParser(register_map=register_map, device_registry=device_registry)

    # Initialize event handler with gateway parser
    init_event_handler(gateway_parser=parser)

    # Start subscriber
    subscriber = MQTTSubscriber(enable_simulator=True)
    try:
        subscriber.connect()
        subscriber.run()
    except Exception as e:
        logger.error(f"Subscriber error: {e}")
    finally:
        subscriber.disconnect()
        # Close DB pool
        import asyncio
        from subscriber.db_writer import close_pool
        asyncio.get_event_loop().run_until_complete(close_pool())


def run_simulator():
    """Run the data simulator (publishes mock data to local Mosquitto)."""
    from simulator.mqtt_publisher import main as publisher_main
    publisher_main()


def run_api():
    """Run the debug API server."""
    import uvicorn
    from api.endpoints import app
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("API_PORT", "8001")), log_level="info")


def run_all():
    """Run subscriber + simulator together (for testing)."""
    from gateway.wt410m_parser import WT410MParser
    from subscriber.event_handler import init_event_handler
    from subscriber.mqtt_subscriber import MQTTSubscriber
    from simulator.mqtt_publisher import MQTTPublisher
    from simulator.config import INTERVAL_SECONDS, PLANT_ID, INVERTER_IDS, LOCATION
    from simulator.data_generator import generate_weather, generate_inverter
    from datetime import datetime
    import pytz

    register_map, device_registry = load_config()
    parser = WT410MParser(register_map=register_map, device_registry=device_registry)
    init_event_handler(gateway_parser=parser)

    shutdown_event = threading.Event()

    def signal_handler(signum, frame):
        logger.info("\nShutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start subscriber in background
    subscriber = MQTTSubscriber(enable_simulator=True)
    subscriber.connect()
    subscriber.run_async()

    time.sleep(1)

    # Run simulator in foreground
    timezone = pytz.timezone(LOCATION["timezone"])
    publisher = MQTTPublisher()

    try:
        publisher.connect()
        logger.info("=" * 60)
        logger.info("MQTT Service running (subscriber + simulator)")
        logger.info("=" * 60)

        while not shutdown_event.is_set():
            timestamp = datetime.now(timezone)
            weather_data = generate_weather(timestamp)
            publisher.publish_weather(PLANT_ID, weather_data)

            for inverter in INVERTER_IDS:
                inverter_data = generate_inverter(
                    inverter_id=inverter["id"],
                    inverter_serial=inverter["serial"],
                    timestamp=timestamp,
                    irradiance=weather_data["irradiance"]
                )
                publisher.publish_inverter(PLANT_ID, inverter["id"], inverter_data)

            for _ in range(INTERVAL_SECONDS):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        publisher.disconnect()
        subscriber.disconnect()


def run_sniffer():
    """Run the sniffer — see raw MQTT messages, no DB writes."""
    from sniffer import main as sniffer_main
    sniffer_main()


def print_help():
    print("""
GoodEnergies MQTT Service
=========================

Usage: python main.py <command>

Commands:
    sniff        [START HERE] See raw MQTT messages (no DB writes)
                 Use this first to understand what your WT410M sends.

    subscribe    Parse gateway messages + write to production DB.
                 Only run after you've configured register_map.json
                 and device_registry.json from sniff output.

    simulator    Run the data simulator (local testing only)

    all          Run subscriber + simulator together

    api          Run the debug API server (http://localhost:8001)

Getting Started:
    Phase 1 - Discovery (YOU ARE HERE):
      1. Start Mosquitto:  docker-compose up -d
      2. Point WT410M gateway to your Mosquitto IP (Server IP Settings)
      3. Run:  python main.py sniff
      4. See what JSON the gateway sends, note the topic + field names
      5. Edit config/register_map.json based on real data
      6. Edit config/device_registry.json with IMEI → plant/inverter mapping

    Phase 2 - Production:
      7. Set DATABASE_URL in .env
      8. Run:  python main.py subscribe
""")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    command = sys.argv[1].lower()

    if command in ("sniff", "sniffer", "listen", "debug"):
        run_sniffer()
    elif command in ("subscribe", "sub", "subscriber"):
        run_subscriber()
    elif command in ("simulator", "sim", "publisher"):
        run_simulator()
    elif command == "api":
        run_api()
    elif command == "all":
        run_all()
    elif command in ("help", "-h", "--help"):
        print_help()
    else:
        print(f"Unknown command: {command}")
        print_help()
        sys.exit(1)
