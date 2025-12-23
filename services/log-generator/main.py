import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from kafka import KafkaProducer

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
TOPIC = os.getenv("LOGS_RAW_TOPIC", "logs_raw")

# Optional failure injection for demo purposes
# Values: none | auth_fail | db_slow | payment_timeout | cache_miss | error_burst
FAILURE_MODE = os.getenv("FAILURE_MODE", "none").strip().lower()
INTERVAL_SEC = float(os.getenv("LOG_INTERVAL_SEC", "1"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

def _new_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        retries=5,
        linger_ms=50,
    )

services = ["payments", "orders", "auth", "db"]

logging.info("Kafka broker: %s", BROKER)
logging.info("Producing to topic: %s", TOPIC)
logging.info("FAILURE_MODE=%s interval=%ss", FAILURE_MODE, INTERVAL_SEC)

producer = None
i = 0
while True:
    i += 1
    svc = services[i % len(services)]

    # Base log
    level = "ERROR" if i % 10 == 0 else "INFO"
    message = f"synthetic log #{i}"

    # Failure injection (deterministic-ish patterns so you can demo incidents)
    if FAILURE_MODE == "auth_fail" and svc == "auth" and i % 3 == 0:
        level = "ERROR"
        message = "authentication failed for user_id=123"
    elif FAILURE_MODE == "db_slow" and svc == "db" and i % 4 == 0:
        level = "WARN"
        message = "db connection slow: p95=850ms"
    elif FAILURE_MODE == "payment_timeout" and svc == "payments" and i % 4 == 0:
        level = "ERROR"
        message = "payment request timeout: upstream=gateway"
    elif FAILURE_MODE == "cache_miss" and svc == "orders" and i % 5 == 0:
        level = "WARN"
        message = "cache miss rate elevated"
    elif FAILURE_MODE == "error_burst" and svc == random.choice(services):
        # probabilistic burst
        if random.random() < 0.35:
            level = "ERROR"
            message = "synthetic error burst event"

    log = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "service": svc,
        "level": level,
        "message": message,
        "trace_id": f"trace-{i}",
    }

    try:
        if producer is None:
            producer = _new_producer()
        producer.send(TOPIC, log)
        logging.info("Sent log: service=%s level=%s message=%s", svc, level, message)
    except Exception as e:
        logging.exception("Kafka produce failed; will retry: %s", e)
        producer = None
        time.sleep(2)
        continue

    time.sleep(INTERVAL_SEC)
