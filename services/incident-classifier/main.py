import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Deque, Dict

from kafka import KafkaConsumer, KafkaProducer

from classifier import classify_incident

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
LOGS_TOPIC = os.getenv("LOGS_PARSED_TOPIC", "logs_parsed")
ML_TOPIC = os.getenv("ML_OUTPUT_TOPIC", "ml_output")
OUT_TOPIC = os.getenv("INCIDENTS_TOPIC", "incidents")

# Simple burst detection (log-based) — counts error/critical logs per service.
BURST_WINDOW_SEC = int(os.getenv("ERROR_BURST_WINDOW_SEC", "30"))
BURST_THRESHOLD = int(os.getenv("ERROR_BURST_THRESHOLD", "8"))


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_classifier() -> None:
    logging.info("Incident Classifier started")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Consuming topics: %s, %s -> Producing: %s", LOGS_TOPIC, ML_TOPIC, OUT_TOPIC)

    # In-memory window for burst detection (kept per service)
    error_windows: Dict[str, Deque[float]] = defaultdict(lambda: deque())

    consumer = None
    producer = None

    while True:
        try:
            if consumer is None:
                consumer = KafkaConsumer(
                    LOGS_TOPIC,
                    ML_TOPIC,
                    bootstrap_servers=BROKER,
                    auto_offset_reset=os.getenv("AUTO_OFFSET_RESET", "latest"),
                    enable_auto_commit=True,
                    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                )

            if producer is None:
                producer = KafkaProducer(
                    bootstrap_servers=BROKER,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    retries=5,
                    linger_ms=50,
                )

            for msg in consumer:
                payload = msg.value
                topic = msg.topic

                incident = None

                if topic == LOGS_TOPIC:
                    # Update burst window for error/critical logs
                    service = payload.get("service") or "unknown"
                    sev = (payload.get("severity") or "info").lower()
                    now = time.time()

                    if sev in {"error", "critical"}:
                        w = error_windows[service]
                        w.append(now)
                        # evict old
                        cutoff = now - BURST_WINDOW_SEC
                        while w and w[0] < cutoff:
                            w.popleft()

                        if len(w) >= BURST_THRESHOLD:
                            incident = {
                                "timestamp": payload.get("timestamp") or _iso_utc_now(),
                                "service": service,
                                "severity": "critical",
                                "incident_type": "ERROR_RATE_SPIKE",
                                "source": "logs_parsed",
                                "details": {
                                    "window_sec": BURST_WINDOW_SEC,
                                    "count": len(w),
                                    "threshold": BURST_THRESHOLD,
                                    "sample_log": payload,
                                },
                            }
                            # Reset window to avoid spamming
                            w.clear()

                    # Also run keyword rules (auth failure, db slow, etc.)
                    if incident is None:
                        incident = classify_incident(log=payload)

                elif topic == ML_TOPIC:
                    incident = classify_incident(anomaly=payload)

                if incident:
                    producer.send(OUT_TOPIC, incident)
                    logging.info(
                        "Published incident: type=%s service=%s severity=%s",
                        incident.get("incident_type"),
                        incident.get("service"),
                        incident.get("severity"),
                    )

        except Exception as e:
            logging.exception("Incident classifier error; will recreate Kafka clients: %s", e)
            consumer = None
            producer = None
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    start_classifier()
