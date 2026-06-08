"""
MQTT Subscriber - Listens for messages from WT410M gateways and simulators.

Subscribes to:
  - iot1/+/event/#   (WT410M gateway primary client)
  - iot2/+/event/#   (WT410M gateway secondary client)
  - goodenergies/plants/+/weather                        (simulator)
  - goodenergies/plants/+/inverters/+/telemetry          (simulator)
"""
import os
import logging
from typing import List

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from .event_handler import handle_mqtt_message, event_handler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mqtt_subscriber")

MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "goodenergies_subscriber")
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))

GATEWAY_TOPICS = [
    ("iot1/#", MQTT_QOS),
    ("iot2/#", MQTT_QOS),
]

SIMULATOR_TOPICS = [
    ("goodenergies/plants/+/weather", MQTT_QOS),
    ("goodenergies/plants/+/inverters/+/telemetry", MQTT_QOS),
]


class MQTTSubscriber:

    def __init__(self, topics: List[tuple] = None, enable_simulator: bool = True):
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self.connected = False
        self.message_count = 0

        self.topics = topics or list(GATEWAY_TOPICS)
        if enable_simulator:
            self.topics.extend(SIMULATOR_TOPICS)

        if MQTT_USERNAME:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logger.info(f"Connected to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
            for topic, qos in self.topics:
                client.subscribe(topic, qos)
                logger.info(f"  Subscribed: {topic} (QoS {qos})")
        else:
            errors = {1: "Bad protocol", 2: "Bad client ID", 3: "Server unavailable",
                      4: "Bad credentials", 5: "Not authorized"}
            logger.error(f"Connection failed: {errors.get(rc, f'rc={rc}')}")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnect (rc={rc}), auto-reconnecting...")

    def _on_message(self, client, userdata, msg):
        self.message_count += 1
        try:
            handle_mqtt_message(msg.topic, msg.payload)
        except Exception as e:
            logger.error(f"Error processing {msg.topic}: {e}")

    def connect(self):
        logger.info(f"Connecting to {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
        self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=MQTT_KEEPALIVE)

    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

    def run(self):
        logger.info("=" * 60)
        logger.info("GoodEnergies MQTT Service")
        logger.info("=" * 60)
        logger.info(f"Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        logger.info(f"Auth: {'yes (' + MQTT_USERNAME + ')' if MQTT_USERNAME else 'no'}")
        for t, q in self.topics:
            logger.info(f"  -> {t} (QoS {q})")
        logger.info("Listening... (Ctrl+C to stop)")
        logger.info("-" * 60)
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            logger.info("\nShutdown")
            self._print_stats()

    def run_async(self):
        self.client.loop_start()

    def _print_stats(self):
        stats = event_handler.get_stats() if event_handler else {}
        logger.info(f"Messages: {self.message_count} | "
                     f"GW: {stats.get('gateway_messages',0)} | "
                     f"SIM: {stats.get('simulator_messages',0)} | "
                     f"Weather: {stats.get('weather_processed',0)} | "
                     f"Inverter: {stats.get('inverter_processed',0)} | "
                     f"Errors: {stats.get('errors',0)}")
