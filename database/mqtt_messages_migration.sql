-- Optional: Create mqtt_messages table in production DB for debugging
-- Run this manually if you want MQTT message logging:
--   psql -h YOUR_DB_HOST -U postgres -d goodenergies -f mqtt_messages_migration.sql

CREATE TABLE IF NOT EXISTS mqtt_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic VARCHAR(255) NOT NULL,
    payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processing_error TEXT,
    received_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mqtt_messages_topic ON mqtt_messages(topic);
CREATE INDEX IF NOT EXISTS idx_mqtt_messages_received ON mqtt_messages(received_at);
