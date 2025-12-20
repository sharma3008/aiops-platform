import json
from kafka import KafkaConsumer, KafkaProducer
from classifier import classify_incident

BROKER = "localhost:9092"
LOG_TOPIC = "logs.parsed"
ML_TOPIC = "ml.output"
OUT_TOPIC = "incidents"

def start_classifier():
    print("🚨 Incident Classifier started...")

    log_consumer = KafkaConsumer(
        LOG_TOPIC,
        bootstrap_servers=BROKER,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8"))
    )

    ml_consumer = KafkaConsumer(
        ML_TOPIC,
        bootstrap_servers=BROKER,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8"))
    )

    producer = KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8")
    )

    while True:
        # Logs
        for msg_batch in log_consumer.poll(timeout_ms=100).values():
            for record in msg_batch:
                log = record.value
                inc = classify_incident(log=log)
                if inc:
                    producer.send(OUT_TOPIC, inc)
                    print("📤 Incident (log):", inc)

        # ML anomalies
        for msg_batch in ml_consumer.poll(timeout_ms=100).values():
            for record in msg_batch:
                anomaly = record.value
                inc = classify_incident(anomaly=anomaly)
                if inc:
                    producer.send(OUT_TOPIC, inc)
                    print("📤 Incident (ML):", inc)

if __name__ == "__main__":
    start_classifier()
