import json
import logging
import os
import time

from kafka import KafkaConsumer, KafkaProducer

from processor import enrich_metrics

RAW_TOPIC = os.getenv("METRICS_RAW_TOPIC", "metrics_raw")
OUT_TOPIC = os.getenv("METRICS_ENRICHED_TOPIC", "metrics_enriched")
BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

def start_metrics_processor():
    logging.info("Metrics Processor started")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Consuming: %s -> Producing: %s", RAW_TOPIC, OUT_TOPIC)

    consumer = None
    producer = None

    while True:
        try:
            if consumer is None:
                consumer = KafkaConsumer(
                    RAW_TOPIC,
                    bootstrap_servers=BROKER,
                    auto_offset_reset=os.getenv("AUTO_OFFSET_RESET", "earliest"),
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
                raw = msg.value
                enriched = enrich_metrics(raw)
                producer.send(OUT_TOPIC, enriched)
                logging.info(
                    "Enriched metrics: service=%s cpu=%.2f latency=%.2f",
                    enriched.get("service"),
                    enriched.get("cpu_percent", 0.0),
                    enriched.get("network_latency_ms", 0.0),
                )

        except Exception as e:
            logging.exception("Metrics processor error; will recreate Kafka clients: %s", e)
            consumer = None
            producer = None
            time.sleep(2)

if __name__ == "__main__":
    start_metrics_processor()
