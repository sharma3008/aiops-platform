import json
import logging
import os
import random
import time
from datetime import datetime, timezone

from kafka import KafkaProducer

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
TOPIC = os.getenv("METRICS_RAW_TOPIC", "metrics_raw")
INTERVAL_SEC = float(os.getenv("METRICS_INTERVAL_SEC", "2"))

# Optional failure injection for demo
# Values: none | cpu_spike | latency_spike | memory_spike
FAILURE_MODE = os.getenv("FAILURE_MODE", "none").strip().lower()

SERVICES = [s.strip() for s in os.getenv("SERVICES", "payments,orders,auth,db").split(",") if s.strip()]

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        retries=5,
        linger_ms=50,
    )


def generate_metrics(service: str, i: int) -> dict:
    cpu = round(random.uniform(5, 95), 2)
    mem = round(random.uniform(10, 99), 2)
    latency = round(random.uniform(1, 300), 2)

    # Failure injection with periodic patterns (predictable for demos)
    if FAILURE_MODE == "cpu_spike" and service == "payments" and i % 5 == 0:
        cpu = round(random.uniform(85, 99), 2)
    elif FAILURE_MODE == "latency_spike" and service == "orders" and i % 5 == 0:
        latency = round(random.uniform(210, 320), 2)
    elif FAILURE_MODE == "memory_spike" and service == "db" and i % 6 == 0:
        mem = round(random.uniform(86, 99), 2)

    return {
        "timestamp": _iso_utc_now(),
        "service": service,
        "severity": "info",  # metrics are informational by default; processors can escalate
        "cpu_percent": cpu,
        "memory_percent": mem,
        "disk_io": random.randint(100, 2000),  # MB/s
        "network_latency_ms": latency,
        "request_rate": random.randint(50, 300),  # req/s
        "error_rate": round(random.uniform(0, 5), 2),  # %
    }


if __name__ == "__main__":
    logging.info("Metrics generator started")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Producing to topic: %s", TOPIC)
    logging.info("FAILURE_MODE=%s interval=%ss", FAILURE_MODE, INTERVAL_SEC)

    producer = None
    i = 0

    while True:
        i += 1
        svc = SERVICES[i % len(SERVICES)]
        metrics = generate_metrics(svc, i)

        try:
            if producer is None:
                producer = _new_producer()
            producer.send(TOPIC, metrics)
            logging.info(
                "Sent metrics: service=%s cpu=%.2f mem=%.2f latency=%.2f",
                svc,
                metrics["cpu_percent"],
                metrics["memory_percent"],
                metrics["network_latency_ms"],
            )
        except Exception as e:
            logging.exception("Kafka produce failed; will retry: %s", e)
            producer = None
            time.sleep(2)
            continue

        time.sleep(INTERVAL_SEC)
