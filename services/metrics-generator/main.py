import json
import time
import random
from datetime import datetime
from kafka import KafkaProducer

# Kafka setup
producer = KafkaProducer(
    bootstrap_servers="localhost:9092",
    value_serializer=lambda v: json.dumps(v).encode("utf-8")
)

def generate_metrics():
    return {
        "timestamp": str(datetime.utcnow()),
        "cpu_percent": round(random.uniform(5, 95), 2),
        "memory_percent": round(random.uniform(10, 99), 2),
        "disk_io": random.randint(100, 2000),            # MB/s
        "network_latency_ms": round(random.uniform(1, 300), 2),
        "request_rate": random.randint(50, 300),         # req/s
        "error_rate": round(random.uniform(0, 5), 2)     # %
    }

if __name__ == "__main__":
    print("Metrics generator started...")
    while True:
        metrics = generate_metrics()
        producer.send("metrics.raw", metrics)
        print("Sent:", metrics)
        time.sleep(2)    # send every 2 sec
