import json
from kafka import KafkaConsumer, KafkaProducer
from processor import normalize_log

RAW_TOPIC = "logs.raw"
PROCESSED_TOPIC = "logs.parsed"
BROKER = "localhost:9092"

def start_log_processor():
    print("Starting Log Processor...")

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

    print(f"Listening on topic: {RAW_TOPIC}")

    for msg in consumer:
        raw_log = msg.value
        print("Received:", raw_log)

        processed = normalize_log(raw_log)

        producer.send(PROCESSED_TOPIC, processed)
        print("Processed & Sent:", processed)

if __name__ == "__main__":
    start_log_processor()
