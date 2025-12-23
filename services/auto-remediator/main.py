import json
import logging
import os
import time
from datetime import datetime, timezone

from kafka import KafkaConsumer, KafkaProducer

from rules import evaluate_rules

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INCIDENT_TOPIC = os.getenv("INCIDENTS_TOPIC", "incidents")
ACTION_TOPIC = os.getenv("ACTIONS_TOPIC", "actions")

# Basic de-dupe window to keep actions idempotent-ish in a demo (in-memory)
DEDUP_WINDOW_SEC = int(os.getenv("ACTION_DEDUP_WINDOW_SEC", "120"))


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def start_remediator() -> None:
    logging.info("Auto-Remediator started")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Consuming: %s -> Producing: %s", INCIDENT_TOPIC, ACTION_TOPIC)

    consumer = None
    producer = None

    # action_key -> last_emitted_epoch
    emitted: dict[str, float] = {}

    while True:
        try:
            if consumer is None:
                consumer = KafkaConsumer(
                    INCIDENT_TOPIC,
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
                incident = msg.value
                actions = evaluate_rules(incident)
                if not actions:
                    continue

                now = time.time()

                # Clear expired dedup keys
                expired = [k for k, t in emitted.items() if now - t > DEDUP_WINDOW_SEC]
                for k in expired:
                    emitted.pop(k, None)

                for action in actions:
                    action_key = action.get("action_key")
                    if action_key and action_key in emitted:
                        logging.info("Skipping duplicate action_key=%s", action_key)
                        continue

                    # Ensure required fields for Phase 4 contract
                    action_event = {
                        "timestamp": incident.get("timestamp") or _iso_utc_now(),
                        "service": action.get("target_service") or incident.get("service") or "unknown",
                        "severity": action.get("severity") or "info",
                        "action_type": action["action_type"],
                        "target_service": action.get("target_service") or incident.get("service") or "unknown",
                        "parameters": action.get("parameters") or {},
                        "source": "auto_remediator",
                        "incident": incident,
                    }

                    producer.send(ACTION_TOPIC, action_event)
                    if action_key:
                        emitted[action_key] = now

                    logging.info(
                        "Emitted action: type=%s target=%s",
                        action_event.get("action_type"),
                        action_event.get("target_service"),
                    )

        except Exception as e:
            logging.exception("Auto-remediator error; will recreate Kafka clients: %s", e)
            consumer = None
            producer = None
            time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    start_remediator()
