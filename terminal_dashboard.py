#!/usr/bin/env python3
"""
Terminal Dashboard - Real-time display of MQTT test data
"""
import os
import sys
import time
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "goodenergies_mqtt_test"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "test123"),
}


def clear_screen():
    os.system('clear' if os.name != 'nt' else 'cls')


def get_stats():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*) as count FROM test_inverter_readings")
        readings = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) as count FROM test_weather_readings")
        weather = cur.fetchone()["count"]

        cur.execute("SELECT COUNT(*) as count FROM test_yield_calculations")
        calculations = cur.fetchone()["count"]
    conn.close()
    return readings, weather, calculations


def get_latest_weather():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT irradiance, ambient_temperature, wind_speed, humidity, timestamp
            FROM test_weather_readings
            ORDER BY timestamp DESC LIMIT 1
        """)
        result = cur.fetchone()
    conn.close()
    return result


def get_inverter_data():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT ON (ir.inverter_id)
                i.name, i.serial_number,
                ir.active_power, ir.daily_power_yield, ir.total_power_yield,
                ir.timestamp
            FROM test_inverter_readings ir
            JOIN test_inverters i ON ir.inverter_id = i.id
            ORDER BY ir.inverter_id, ir.timestamp DESC
        """)
        result = cur.fetchall()
    conn.close()
    return result


def get_yield_calculations():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT ON (yc.inverter_id)
                i.name,
                yc.actual_yield, yc.expected_yield, yc.performance_ratio_pct,
                yc.irradiance, yc.module_temp, yc.timestamp
            FROM test_yield_calculations yc
            JOIN test_inverters i ON yc.inverter_id = i.id
            ORDER BY yc.inverter_id, yc.timestamp DESC
        """)
        result = cur.fetchall()
    conn.close()
    return result


def create_bar(value, max_value, width=30, char='█'):
    """Create ASCII bar chart"""
    if max_value == 0:
        return ' ' * width
    filled = int((value / max_value) * width)
    return char * filled + '░' * (width - filled)


def format_power_bar(power, max_power=500):
    """Format power with color bar"""
    bar = create_bar(power, max_power, 25)
    return f"{bar} {power:6.1f} kW"


def format_pr_bar(pr):
    """Format PR with color indication"""
    bar = create_bar(min(pr, 100), 100, 20)
    status = "🟢" if pr > 80 else "🟡" if pr > 60 else "🔴"
    return f"{bar} {pr:5.1f}% {status}"


def display_dashboard():
    clear_screen()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    readings, weather_count, calculations = get_stats()
    weather = get_latest_weather()
    inverters = get_inverter_data()
    yields = get_yield_calculations()

    print("=" * 80)
    print("         ⚡ GOODENERGIES MQTT TEST DASHBOARD ⚡")
    print("=" * 80)
    print(f"  Last refresh: {now}                    Auto-refresh: 3 seconds")
    print("=" * 80)

    # Stats row
    print("\n┌─────────────────────────────────────────────────────────────────────────────┐")
    print(f"│  📊 STATISTICS                                                              │")
    print("├─────────────────────────────────────────────────────────────────────────────┤")
    print(f"│  Inverter Readings: {readings:<8}  Weather Readings: {weather_count:<8}  Calculations: {calculations:<6} │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")

    # Weather section
    print("\n┌─────────────────────────────────────────────────────────────────────────────┐")
    print(f"│  ☀️  WEATHER CONDITIONS                                                      │")
    print("├─────────────────────────────────────────────────────────────────────────────┤")
    if weather:
        irr = float(weather['irradiance'] or 0)
        temp = float(weather['ambient_temperature'] or 0)
        wind = float(weather['wind_speed'] or 0)
        humid = float(weather['humidity'] or 0)

        irr_bar = create_bar(irr, 1000, 20)
        temp_bar = create_bar(temp, 45, 15)

        print(f"│  Irradiance:    {irr_bar} {irr:7.1f} W/m²                          │")
        print(f"│  Temperature:   {temp_bar} {temp:7.1f} °C                               │")
        print(f"│  Wind Speed:    {wind:5.1f} m/s      Humidity: {humid:5.1f} %                        │")
    else:
        print("│  No weather data yet...                                                   │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")

    # Inverters section
    print("\n┌─────────────────────────────────────────────────────────────────────────────┐")
    print(f"│  ⚡ INVERTER STATUS                                                         │")
    print("├──────────┬────────────────────────────────────┬─────────────┬───────────────┤")
    print("│ Inverter │ Active Power                       │ Daily Yield │ Total Yield   │")
    print("├──────────┼────────────────────────────────────┼─────────────┼───────────────┤")

    if inverters:
        for inv in inverters:
            name = inv['name']
            power = float(inv['active_power'] or 0)
            daily = float(inv['daily_power_yield'] or 0)
            total = float(inv['total_power_yield'] or 0)

            power_bar = format_power_bar(power)
            print(f"│ {name:<8} │ {power_bar} │ {daily:8.2f} kWh│ {total:10.0f} kWh│")
    else:
        print("│          │ No inverter data yet...            │             │               │")
    print("└──────────┴────────────────────────────────────┴─────────────┴───────────────┘")

    # Yield calculations section
    print("\n┌─────────────────────────────────────────────────────────────────────────────┐")
    print(f"│  📊 YIELD CALCULATIONS (Event-Driven)                                       │")
    print("├──────────┬──────────────┬──────────────┬─────────────────────────┬──────────┤")
    print("│ Inverter │ Actual Yield │ Expected     │ Performance Ratio       │ Mod Temp │")
    print("├──────────┼──────────────┼──────────────┼─────────────────────────┼──────────┤")

    if yields:
        for y in yields:
            name = y['name']
            actual = float(y['actual_yield'] or 0)
            expected = float(y['expected_yield'] or 0)
            pr = float(y['performance_ratio_pct'] or 0)
            mod_temp = float(y['module_temp'] or 0)

            pr_bar = format_pr_bar(pr)
            print(f"│ {name:<8} │ {actual:8.4f} kWh │ {expected:8.4f} kWh │ {pr_bar} │ {mod_temp:5.1f} °C │")
    else:
        print("│          │ No calculations yet...                                         │")
    print("└──────────┴──────────────┴──────────────┴─────────────────────────┴──────────┘")

    # Data flow diagram
    print("\n┌─────────────────────────────────────────────────────────────────────────────┐")
    print("│  📡 DATA FLOW (Event-Driven Architecture)                                   │")
    print("├─────────────────────────────────────────────────────────────────────────────┤")
    print("│                                                                             │")
    print("│   Simulator ──MQTT──▶ Mosquitto ──MQTT──▶ Subscriber ──▶ Database          │")
    print("│   (Publisher)         (Broker)            (Handler)       │                │")
    print("│                                               │           │                │")
    print("│                                               ▼           ▼                │")
    print("│                                         Yield Calc ──▶ Calculations        │")
    print("│                                                                             │")
    print("└─────────────────────────────────────────────────────────────────────────────┘")

    print("\n  Press Ctrl+C to exit")


def main():
    print("Starting Terminal Dashboard...")
    print("Connecting to database...")

    try:
        while True:
            try:
                display_dashboard()
                time.sleep(3)
            except psycopg2.Error as e:
                print(f"\nDatabase error: {e}")
                print("Retrying in 3 seconds...")
                time.sleep(3)
    except KeyboardInterrupt:
        print("\n\nDashboard stopped.")


if __name__ == "__main__":
    main()
