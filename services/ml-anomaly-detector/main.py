import json
import logging
import os
import time

import joblib
import numpy as np
from kafka import KafkaConsumer, KafkaProducer

from processor import extract_features

INPUT_TOPIC = os.getenv("ML_INPUT_TOPIC", "metrics_enriched")
OUTPUT_TOPIC = os.getenv("ML_OUTPUT_TOPIC", "ml_output")
BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
MODEL_PATH = os.getenv("MODEL_PATH", "model.pkl")


def start_ml_service():
    logging.info("ML Anomaly Detector started")
    logging.info("Kafka broker: %s", BROKER)
    logging.info("Consuming: %s -> Producing: %s", INPUT_TOPIC, OUTPUT_TOPIC)

    model = joblib.load(MODEL_PATH)
    logging.info("Model loaded successfully from %s", MODEL_PATH)

    consumer = None
    producer = None

    while True:
        try:
            if consumer is None:
                consumer = KafkaConsumer(
                    INPUT_TOPIC,
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
                enriched = msg.value

                features = np.array([extract_features(enriched)])
                score = float(model.decision_function(features)[0])
                is_anomaly = bool(model.predict(features)[0] == -1)

                cpu = float(enriched.get("cpu_avg_5", 0))
                mem = float(enriched.get("memory_avg_5", 0))
                lat = float(enriched.get("latency_avg_5", 0))

                anomaly_type = "NONE"
                severity = "info"
                if is_anomaly:
                    # Primary mapping (per your project conventions)
                    if cpu > 80:
                        anomaly_type = "CPU_SPIKE"
                        severity = "warn"
                    elif lat > 200:
                        anomaly_type = "LATENCY_SPIKE"
                        severity = "error"
                    elif mem > 85:
                        anomaly_type = "MEMORY_SPIKE"
                        severity = "warn"
                    else:
                        anomaly_type = "UNKNOWN_ANOMALY"
                        severity = "warn"

                result = {
                    "timestamp": enriched.get("timestamp"),
                    "service": enriched.get("service", "unknown"),
                    "severity": severity,
                    "anomaly_type": anomaly_type,
                    "anomaly_score": score,
                    "is_anomaly": is_anomaly,
                    "cpu_avg_5": cpu,
                    "memory_avg_5": mem,
                    "latency_avg_5": lat,
                }

                producer.send(OUTPUT_TOPIC, result)
                logging.info(
                    "ML output: service=%s type=%s score=%.4f",
                    result.get("service"),
                    anomaly_type,
                    score,
                )

        except Exception as e:
            logging.exception("ML service error; will recreate Kafka clients: %s", e)
            consumer = None
            producer = None
            time.sleep(2)

if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    start_ml_service()
