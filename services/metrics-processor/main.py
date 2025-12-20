import json
from kafka import KafkaConsumer, KafkaProducer
from processor import enrich_metrics

RAW_TOPIC = "metrics.raw"
OUT_TOPIC = "metrics.enriched"
BROKER = "localhost:9092"

def start_metrics_processor():
    print("🚀 Metrics Processor started... Listening for metrics...")

    consumer = KafkaConsumer(
        RAW_TOPIC,
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
        raw = msg.value
        print("📥 Received raw metrics:", raw)

        enriched = enrich_metrics(raw)

        producer.send(OUT_TOPIC, enriched)
        print("📤 Sent enriched metrics:", enriched)

if __name__ == "__main__":
    start_metrics_processor()
