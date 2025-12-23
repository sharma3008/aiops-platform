"""Incident classifier.

Produces records compatible with:
  - Kafka topic: incidents
  - Vector -> Loki labels: service, severity, topic, source=aiops

Output schema:
  timestamp: ISO8601
  service: str
  severity: str (info|warn|error|critical)
  incident_type: str (CPU_SPIKE|LATENCY_SPIKE|ERROR_RATE_SPIKE|...)
  source: str
  details: dict
"""

from rules import classify_from_logs


def classify_incident(log: dict | None = None, anomaly: dict | None = None) -> dict | None:
    """Combine log-based keyword rules + ML anomaly results."""

    # 1) Log-based keyword rules
    if log:
        rule_incident = classify_from_logs(log)
        if rule_incident:
            return {
                "timestamp": log.get("timestamp"),
                "service": log.get("service") or "unknown",
                "severity": rule_incident.get("severity", "error"),
                "incident_type": rule_incident["incident_type"],
                "source": "logs_parsed",
                "details": {"log": log, **rule_incident.get("details", {})},
            }

    # 2) ML anomaly results (already mapped to CPU_SPIKE/LATENCY_SPIKE/MEMORY_SPIKE where possible)
    if anomaly and anomaly.get("is_anomaly"):
        incident_type = anomaly.get("anomaly_type") or "UNKNOWN_ANOMALY"

        # Severity should be a string (for Loki labels). Keep it if already string.
        sev = anomaly.get("severity")
        if not isinstance(sev, str):
            sev = "warn"

        return {
            "timestamp": anomaly.get("timestamp"),
            "service": anomaly.get("service") or "unknown",
            "severity": sev,
            "incident_type": incident_type,
            "source": "ml_output",
            "details": {"anomaly": anomaly},
        }

    return None
