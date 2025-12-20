import json
from kafka import KafkaConsumer, KafkaProducer
from rules import get_remediation_action

BROKER = "localhost:9092"
INCIDENT_TOPIC = "incidents"
ACTION_TOPIC = "actions"

def start_remediator():
    print("Auto-Remediator started...")

    consumer = KafkaConsumer(
        INCIDENT_TOPIC,
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
        for msg_batch in consumer.poll(timeout_ms=200).values():
            for record in msg_batch:
                incident = record.value
                action = get_remediation_action(incident)

                remediation_event = {
                    "timestamp": incident.get("timestamp"),
                    "incident_type": incident.get("type"),
                    "service": incident.get("service"),
                    "action": action,
                    "severity": incident.get("severity"),
                    "details": incident
                }

                print(" Generated remediation:", remediation_event)
                producer.send(ACTION_TOPIC, remediation_event)

if __name__ == "__main__":
    start_remediator()
