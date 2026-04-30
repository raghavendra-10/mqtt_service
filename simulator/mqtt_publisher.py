"""
MQTT Publisher - Simulates Data Logger
Publishes inverter and weather data to MQTT broker
"""
import json
import time
import logging
from datetime import datetime
import paho.mqtt.client as mqtt
import pytz

from .config import (
    MQTT_BROKER_HOST, MQTT_BROKER_PORT, MQTT_CLIENT_ID, MQTT_QOS,
    INTERVAL_SECONDS, PLANT_ID, INVERTER_IDS, LOCATION
)
from .data_generator import generate_weather, generate_inverter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mqtt_publisher")


class MQTTPublisher:
    """
    MQTT Publisher that simulates a data logger sending telemetry data.

    Topics:
    - goodenergies/plants/{plant_id}/weather
    - goodenergies/plants/{plant_id}/inverters/{inverter_id}/telemetry
    """

    def __init__(self):
        self.client = mqtt.Client(client_id=f"{MQTT_CLIENT_ID}_publisher")
        self.connected = False
        self.timezone = pytz.timezone(LOCATION["timezone"])

        # Set up callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to broker"""
        if rc == 0:
            self.connected = True
            logger.info(f"Connected to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}")
        else:
            logger.error(f"Failed to connect to broker. Return code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from broker"""
        self.connected = False
        if rc != 0:
            logger.warning(f"Unexpected disconnection. Return code: {rc}")
        else:
            logger.info("Disconnected from broker")

    def _on_publish(self, client, userdata, mid):
        """Callback when message is published"""
        logger.debug(f"Message {mid} published successfully")

    def connect(self):
        """Connect to MQTT broker"""
        try:
            logger.info(f"Connecting to MQTT broker at {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}...")
            self.client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=60)
            self.client.loop_start()

            # Wait for connection
            timeout = 10
            while not self.connected and timeout > 0:
                time.sleep(0.5)
                timeout -= 0.5

            if not self.connected:
                raise ConnectionError("Failed to connect to MQTT broker")

        except Exception as e:
            logger.error(f"Connection error: {e}")
            raise

    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("Disconnected from MQTT broker")

    def publish_weather(self, plant_id: str, data: dict):
        """
        Publish weather data to MQTT topic

        Topic: goodenergies/plants/{plant_id}/weather
        """
        topic = f"goodenergies/plants/{plant_id}/weather"
        payload = json.dumps(data)

        result = self.client.publish(topic, payload, qos=MQTT_QOS)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"📡 Weather → {topic} | Irradiance: {data['irradiance']} W/m², Temp: {data['ambient_temperature']}°C")
        else:
            logger.error(f"Failed to publish weather data: {result.rc}")

    def publish_inverter(self, plant_id: str, inverter_id: str, data: dict):
        """
        Publish inverter telemetry to MQTT topic

        Topic: goodenergies/plants/{plant_id}/inverters/{inverter_id}/telemetry
        """
        topic = f"goodenergies/plants/{plant_id}/inverters/{inverter_id}/telemetry"
        payload = json.dumps(data)

        result = self.client.publish(topic, payload, qos=MQTT_QOS)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(
                f"⚡ Inverter → {topic} | "
                f"Power: {data['active_power']:.1f} kW, "
                f"Daily Yield: {data['daily_power_yield']:.2f} kWh"
            )
        else:
            logger.error(f"Failed to publish inverter data: {result.rc}")

    def run_simulation(self):
        """
        Run the simulation loop - continuously publish data at configured interval
        """
        logger.info(f"Starting simulation for plant {PLANT_ID}")
        logger.info(f"Publishing every {INTERVAL_SECONDS} seconds")
        logger.info(f"Inverters: {[inv['serial'] for inv in INVERTER_IDS]}")
        logger.info("-" * 60)

        try:
            while True:
                timestamp = datetime.now(self.timezone)

                # Generate and publish weather data
                weather_data = generate_weather(timestamp)
                self.publish_weather(PLANT_ID, weather_data)

                # Generate and publish inverter data for each inverter
                for inverter in INVERTER_IDS:
                    inverter_data = generate_inverter(
                        inverter_id=inverter["id"],
                        inverter_serial=inverter["serial"],
                        timestamp=timestamp,
                        irradiance=weather_data["irradiance"]
                    )
                    self.publish_inverter(PLANT_ID, inverter["id"], inverter_data)

                logger.info(f"--- Published {len(INVERTER_IDS) + 1} messages at {timestamp.strftime('%H:%M:%S')} ---")

                # Wait for next interval
                time.sleep(INTERVAL_SECONDS)

        except KeyboardInterrupt:
            logger.info("Simulation stopped by user")


def main():
    """Main entry point for the publisher"""
    publisher = MQTTPublisher()

    try:
        publisher.connect()
        publisher.run_simulation()
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        publisher.disconnect()


if __name__ == "__main__":
    main()
