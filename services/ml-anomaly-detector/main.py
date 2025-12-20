import json
import joblib
import numpy as np
from kafka import KafkaConsumer, KafkaProducer
from processor import extract_features

INPUT_TOPIC = "metrics.enriched"
OUTPUT_TOPIC = "ml.output"
BROKER = "localhost:9092"

def start_ml_service():
    print("🚀 ML Anomaly Detector started...")

    model = joblib.load("model.pkl")
    print("Model loaded successfully.")

    consumer = KafkaConsumer(
        INPUT_TOPIC,
        bootstrap_servers=BROKER,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8"))
    )

    producer = KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8")
    )

    for msg in consumer:
        enriched = msg.value
        print("📥 Received enriched metrics:", enriched)

        features = np.array([extract_features(enriched)])
        score = model.decision_function(features)[0]  # anomaly score
        is_anomaly = model.predict(features)[0] == -1

        result = {
            "timestamp": enriched["timestamp"],
            "cpu_avg_5": enriched["cpu_avg_5"],
            "memory_avg_5": enriched["memory_avg_5"],
            "latency_avg_5": enriched["latency_avg_5"],
            "anomaly_score": float(score),
            "is_anomaly": bool(is_anomaly)
        }

        producer.send(OUTPUT_TOPIC, result)
        print("📤 Sent anomaly result:", result)

if __name__ == "__main__":
    start_ml_service()
