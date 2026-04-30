"""
Simple API for viewing MQTT test data

Run with: uvicorn api.endpoints:app --port 8001
"""
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="GoodEnergies MQTT Test API",
    description="API for viewing test data from MQTT-based solar plant monitoring",
    version="1.0.0",
)

# CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5433")),
    "database": os.getenv("DB_NAME", "goodenergies_mqtt_test"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "test123"),
}


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(**DB_CONFIG)


# Pydantic models
class Plant(BaseModel):
    id: str
    plant_id: str
    name: str
    location: Optional[str]
    capacity: Optional[float]
    installed_capacity_mw: Optional[float]


class Inverter(BaseModel):
    id: str
    plant_id: str
    name: str
    serial_number: str
    dc_capacity_kwp: Optional[int]
    ac_capacity_kva: Optional[int]
    status: str


class InverterReading(BaseModel):
    id: str
    inverter_id: str
    timestamp: datetime
    active_power: Optional[float]
    daily_power_yield: Optional[float]
    total_power_yield: Optional[float]


class WeatherReading(BaseModel):
    id: str
    plant_id: str
    timestamp: datetime
    irradiance: Optional[float]
    ambient_temperature: Optional[float]
    wind_speed: Optional[float]


class YieldCalculation(BaseModel):
    id: str
    inverter_id: str
    timestamp: datetime
    actual_yield: Optional[float]
    expected_yield: Optional[float]
    performance_ratio_pct: Optional[float]
    irradiance: Optional[float]


class Stats(BaseModel):
    total_readings: int
    total_weather: int
    total_calculations: int
    total_mqtt_messages: int
    latest_reading_time: Optional[datetime]


# API Endpoints
@app.get("/")
def root():
    """API root - health check"""
    return {
        "status": "ok",
        "service": "GoodEnergies MQTT Test API",
        "timestamp": datetime.now().isoformat(),
        "dashboard": "http://localhost:8001/dashboard",
    }


@app.get("/dashboard")
def dashboard():
    """Serve the dashboard HTML page"""
    dashboard_path = Path(__file__).parent.parent / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(dashboard_path, media_type="text/html")
    raise HTTPException(status_code=404, detail="Dashboard not found")


@app.get("/plants", response_model=List[Plant])
def get_plants():
    """Get all test plants"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id::text, plant_id, name, location, capacity, installed_capacity_mw
                FROM test_plants
                ORDER BY name
            """)
            plants = cur.fetchall()
        conn.close()
        return plants
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/plants/{plant_id}/inverters", response_model=List[Inverter])
def get_inverters(plant_id: str):
    """Get inverters for a plant"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT i.id::text, i.plant_id::text, i.name, i.serial_number,
                       i.dc_capacity_kwp, i.ac_capacity_kva, i.status
                FROM test_inverters i
                JOIN test_plants p ON i.plant_id = p.id
                WHERE p.plant_id = %s
                ORDER BY i.name
            """, (plant_id,))
            inverters = cur.fetchall()
        conn.close()
        return inverters
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/inverters/{inverter_id}/readings", response_model=List[InverterReading])
def get_inverter_readings(
    inverter_id: str,
    limit: int = Query(default=100, le=1000),
    since: Optional[datetime] = None,
):
    """Get recent inverter readings"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if since:
                cur.execute("""
                    SELECT id::text, inverter_id::text, timestamp,
                           active_power, daily_power_yield, total_power_yield
                    FROM test_inverter_readings
                    WHERE inverter_id = %s AND timestamp >= %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (inverter_id, since, limit))
            else:
                cur.execute("""
                    SELECT id::text, inverter_id::text, timestamp,
                           active_power, daily_power_yield, total_power_yield
                    FROM test_inverter_readings
                    WHERE inverter_id = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (inverter_id, limit))
            readings = cur.fetchall()
        conn.close()
        return readings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/plants/{plant_id}/weather", response_model=List[WeatherReading])
def get_weather_readings(
    plant_id: str,
    limit: int = Query(default=100, le=1000),
):
    """Get recent weather readings for a plant"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT w.id::text, w.plant_id::text, w.timestamp,
                       w.irradiance, w.ambient_temperature, w.wind_speed
                FROM test_weather_readings w
                JOIN test_plants p ON w.plant_id = p.id
                WHERE p.plant_id = %s
                ORDER BY w.timestamp DESC
                LIMIT %s
            """, (plant_id, limit))
            readings = cur.fetchall()
        conn.close()
        return readings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/inverters/{inverter_id}/yield", response_model=List[YieldCalculation])
def get_yield_calculations(
    inverter_id: str,
    limit: int = Query(default=100, le=1000),
):
    """Get yield calculations for an inverter"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id::text, inverter_id::text, timestamp,
                       actual_yield, expected_yield, performance_ratio_pct, irradiance
                FROM test_yield_calculations
                WHERE inverter_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (inverter_id, limit))
            calculations = cur.fetchall()
        conn.close()
        return calculations
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/inverters/{inverter_id}/yield-curve")
def get_yield_curve(
    inverter_id: str,
    date: Optional[str] = None,
):
    """
    Get yield curve data (actual vs expected) for charting.

    Returns data suitable for plotting actual vs expected yield over time.
    """
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if date:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            else:
                target_date = datetime.now().date()

            cur.execute("""
                SELECT
                    timestamp,
                    actual_yield,
                    expected_yield,
                    performance_ratio_pct,
                    irradiance,
                    cumulative_actual,
                    cumulative_expected
                FROM test_yield_calculations
                WHERE inverter_id = %s
                  AND DATE(timestamp) = %s
                ORDER BY timestamp ASC
            """, (inverter_id, target_date))

            data = cur.fetchall()

        conn.close()

        # Format for charting
        return {
            "inverter_id": inverter_id,
            "date": str(target_date),
            "data_points": len(data),
            "timestamps": [r["timestamp"].isoformat() for r in data],
            "actual_yield": [float(r["actual_yield"] or 0) for r in data],
            "expected_yield": [float(r["expected_yield"] or 0) for r in data],
            "cumulative_actual": [float(r["cumulative_actual"] or 0) for r in data],
            "cumulative_expected": [float(r["cumulative_expected"] or 0) for r in data],
            "pr_percentage": [float(r["performance_ratio_pct"] or 0) for r in data],
            "irradiance": [float(r["irradiance"] or 0) for r in data],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", response_model=Stats)
def get_stats():
    """Get overall statistics"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Count readings
            cur.execute("SELECT COUNT(*) as count FROM test_inverter_readings")
            readings = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) as count FROM test_weather_readings")
            weather = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) as count FROM test_yield_calculations")
            calculations = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) as count FROM test_mqtt_messages")
            messages = cur.fetchone()["count"]

            # Latest reading
            cur.execute("""
                SELECT MAX(timestamp) as latest FROM test_inverter_readings
            """)
            latest = cur.fetchone()["latest"]

        conn.close()

        return Stats(
            total_readings=readings,
            total_weather=weather,
            total_calculations=calculations,
            total_mqtt_messages=messages,
            latest_reading_time=latest,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/mqtt-messages")
def get_mqtt_messages(limit: int = Query(default=50, le=500)):
    """Get recent MQTT messages for debugging"""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id::text, topic, payload, processed, processing_error,
                       received_at, processed_at
                FROM test_mqtt_messages
                ORDER BY received_at DESC
                LIMIT %s
            """, (limit,))
            messages = cur.fetchall()
        conn.close()
        return messages
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/data/clear")
def clear_all_data():
    """Clear all test data (for fresh testing)"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE test_mqtt_messages CASCADE")
            cur.execute("TRUNCATE test_yield_calculations CASCADE")
            cur.execute("TRUNCATE test_weather_readings CASCADE")
            cur.execute("TRUNCATE test_inverter_readings CASCADE")
            conn.commit()
        conn.close()
        return {"status": "ok", "message": "All test data cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
