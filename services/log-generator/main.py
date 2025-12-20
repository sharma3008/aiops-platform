import json
import time
import random
from datetime import datetime
from kafka import KafkaProducer

LEVELS = ["INFO", "WARN", "ERROR", "DEBUG"]

producer = KafkaProducer(
    bootstrap_servers="localhost:9092",
    value_serializer=lambda v: json.dumps(v).encode("utf-8")
)

COMPONENTS = ["auth-service", "payment-service", "db-service", "api-gateway"]

def generate_log():
    return {
        "timestamp": str(datetime.utcnow()),
        "level": random.choice(LEVELS),
        "service": random.choice(COMPONENTS),
        "message": random.choice([
            "User authentication failed",
            "DB connection slow",
            "Cache miss occurred",
            "Payment request timeout",
            "User logged in successfully"
        ]),
        "trace_id": random.randint(100000, 999999)
    }

if __name__ == "__main__":
    print("Log generator started...")
    while True:
        log = generate_log()
        producer.send("logs.raw", log)
        print("Sent:", log)
        time.sleep(1)
