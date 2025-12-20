# classifier.py

from rules import classify_from_logs

def classify_incident(log=None, anomaly=None):
    """
    Combine log-based rules + ML anomaly results.
    """

    # 1. Check logs first (rule based)
    if log:
        rule_incident = classify_from_logs(log)
        if rule_incident:
            return {
                "type": rule_incident,
                "source": "log",
                "service": log.get("service"),
                "severity": log.get("severity"),
                "details": log
            }

    # 2. Check ML anomaly
    if anomaly and anomaly.get("is_anomaly"):
        cpu = anomaly["cpu_avg_5"]
        mem = anomaly["memory_avg_5"]
        lat = anomaly["latency_avg_5"]

        if cpu > 80:
            return {
                "type": "HIGH_CPU_ANOMALY",
                "source": "ml",
                "severity": 3,
                "details": anomaly
            }

        if mem > 85:
            return {
                "type": "MEMORY_LEAK",
                "source": "ml",
                "severity": 3,
                "details": anomaly
            }

        if lat > 200:
            return {
                "type": "LATENCY_SPIKE",
                "source": "ml",
                "severity": 3,
                "details": anomaly
            }

        # fallback
        return {
            "type": "UNKNOWN_ANOMALY",
            "source": "ml",
            "severity": 2,
            "details": anomaly
        }

    return None
