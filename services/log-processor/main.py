import json
import logging
import os
import time

from kafka import KafkaConsumer, KafkaProducer

from processor import normalize_log

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
RAW_TOPIC = os.getenv("LOGS_RAW_TOPIC", "logs_raw")
PROCESSED_TOPIC = os.getenv("LOGS_PARSED_TOPIC", "logs_parsed")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


def _new_consumer() -> KafkaConsumer:
    return KafkaConsumer(
        RAW_TOPIC,
        bootstrap_servers=BROKER,
        auto_offset_reset=os.getenv("AUTO_OFFSET_RESET", "earliest"),
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )


def _new_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        retries=5,
        linger_ms=50,
    )

def start_log_processor():
    logging.info("Starting Log Processor")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Consuming: %s -> Producing: %s", RAW_TOPIC, PROCESSED_TOPIC)

    consumer = None
    producer = None

    while True:
        try:
            if consumer is None:
                consumer = _new_consumer()
            if producer is None:
                producer = _new_producer()

            for msg in consumer:
                raw_log = msg.value
                processed = normalize_log(raw_log)
                producer.send(PROCESSED_TOPIC, processed)
                logging.info("Processed log: service=%s severity=%s", processed.get("service"), processed.get("severity"))

        except Exception as e:
            logging.exception("Log processor error; will recreate Kafka clients: %s", e)
            consumer = None
            producer = None
            time.sleep(2)

if __name__ == "__main__":
    start_log_processor()
