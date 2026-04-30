# MQTT IoT Test Environment for GoodEnergies

A standalone test environment to simulate MQTT-based data collection from solar plant data loggers. This replaces scheduler-based polling with an **event-driven architecture**.

## Architecture

```
┌─────────────────┐     MQTT      ┌─────────────────┐     Event      ┌─────────────────┐
│  Data Simulator │ ────────────► │ Mosquitto Broker│ ─────────────► │  MQTT Subscriber │
│  (Publisher)    │               │   (Docker)      │                │  + Calculator    │
└─────────────────┘               └─────────────────┘                └────────┬────────┘
                                                                              │
                                                                              ▼
                                                                     ┌─────────────────┐
                                                                     │  Test Database  │
                                                                     │  (PostgreSQL)   │
                                                                     └─────────────────┘
```

## Key Concepts

### Event-Driven vs Scheduler-Based

| Scheduler-Based (Old) | Event-Driven (MQTT) |
|----------------------|---------------------|
| Poll every 15 minutes | React when data arrives |
| Wasteful if no data | Only process when needed |
| Fixed intervals | Real-time processing |
| Centralized control | Decoupled components |

### MQTT Publish/Subscribe Pattern

- **Publisher** (Data Logger): Sends telemetry data to MQTT topics
- **Broker** (Mosquitto): Routes messages to subscribers
- **Subscriber**: Receives messages and triggers processing

### Topics

```
goodenergies/
├── plants/{plant_id}/
│   ├── weather                 # Weather telemetry
│   └── inverters/{inverter_id}/
│       └── telemetry          # Inverter readings
```

## Quick Start

### 1. Start Infrastructure

```bash
cd mqtt_test
docker-compose up -d
```

Wait for PostgreSQL to initialize:
```bash
docker-compose logs -f postgres_test
# Wait until you see "database system is ready to accept connections"
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Everything Together

```bash
python main.py all
```

This starts:
- MQTT Subscriber (processes messages)
- API Server (http://localhost:8001)
- Data Publisher (simulates data every 15 seconds)

### 4. View Data

Open http://localhost:8001/docs for interactive API documentation.

Or use curl:
```bash
# Check stats
curl http://localhost:8001/stats

# List plants
curl http://localhost:8001/plants

# Get yield curve data
curl "http://localhost:8001/inverters/b1b2c3d4-e5f6-7890-abcd-ef1234567891/yield-curve"
```

## Running Components Separately

```bash
# Terminal 1: Start subscriber
python main.py subscriber

# Terminal 2: Start publisher
python main.py publisher

# Terminal 3: Start API
python main.py api
```

## Directory Structure

```
mqtt_test/
├── docker-compose.yml          # Mosquitto + PostgreSQL
├── .env                        # Configuration
├── requirements.txt            # Python dependencies
├── main.py                     # Entry point
├── README.md                   # This file
│
├── database/
│   ├── schema.sql              # Test tables
│   └── seed_data.sql           # Sample data
│
├── simulator/                  # Data generation
│   ├── config.py               # Settings
│   ├── data_generator.py       # Realistic solar data
│   └── mqtt_publisher.py       # MQTT publisher
│
├── subscriber/                 # Event processing
│   ├── mqtt_subscriber.py      # MQTT listener
│   ├── event_handler.py        # Message handler
│   └── db_writer.py            # Database operations
│
├── calculator/                 # Yield/PR calculations
│   ├── yield_calculator.py     # Energy yield
│   └── pr_calculator.py        # Performance Ratio
│
└── api/                        # REST API
    └── endpoints.py            # FastAPI routes
```

## Data Generation

The simulator generates realistic solar data:

- **Sun Curve**: Power output follows sunrise (6 AM) → peak (12 PM) → sunset (6 PM)
- **Weather**: Irradiance up to 1000 W/m², temperature 25-38°C
- **Inverters**: 3 × 500 kVA inverters with MPPT channels
- **Noise**: 2-3% random variation for realism

## Yield Calculation (IEC 61724 Variant-B)

```
1. Module Temperature: T_mod = T_amb + (Irradiance/800) × (NOCT - 20)
2. Temperature Loss: L_temp = 1 + γ × (T_mod - 25)
3. Expected Power: P_exp = P_dc × (Irradiance/1000) × L_temp × η
4. Performance Ratio: PR = Actual_Energy / Expected_Energy
```

Calculations are triggered **immediately** when inverter data arrives (event-driven).

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Health check |
| `GET /plants` | List all plants |
| `GET /plants/{id}/inverters` | List inverters |
| `GET /inverters/{id}/readings` | Inverter readings |
| `GET /inverters/{id}/yield` | Yield calculations |
| `GET /inverters/{id}/yield-curve` | Chart data |
| `GET /plants/{id}/weather` | Weather readings |
| `GET /stats` | Overall statistics |
| `GET /mqtt-messages` | Debug MQTT messages |
| `DELETE /data/clear` | Clear all test data |

## Configuration

Environment variables in `.env`:

```env
# MQTT
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883

# Database
DB_HOST=localhost
DB_PORT=5433
DB_NAME=goodenergies_mqtt_test
DB_USER=postgres
DB_PASSWORD=test123

# Simulator
SIMULATOR_INTERVAL_SECONDS=15
SIMULATOR_PLANT_ID=PLANT001
```

## Troubleshooting

### MQTT Connection Refused
```bash
# Check if Mosquitto is running
docker-compose ps
docker-compose logs mosquitto
```

### Database Connection Error
```bash
# Check if PostgreSQL is ready
docker-compose logs postgres_test
# Should see "database system is ready to accept connections"
```

### Reset Everything
```bash
# Stop and remove containers
docker-compose down -v

# Start fresh
docker-compose up -d
```

## Learning Outcomes

After using this test environment, you will understand:

1. **MQTT Publish/Subscribe Pattern** - How IoT devices communicate
2. **Event-Driven Architecture** - Processing data when it arrives
3. **Real-time Calculations** - Immediate yield/PR calculations
4. **Solar Plant Monitoring** - Telemetry data structure and flow
5. **Performance Ratio** - IEC 61724 calculation method
