# processor.py

from window import SlidingWindow

cpu_window = SlidingWindow(max_size=5)
memory_window = SlidingWindow(max_size=5)
latency_window = SlidingWindow(max_size=5)

def enrich_metrics(raw: dict) -> dict:
    """Enrich raw metrics with short-window aggregates and derived features.

    Contract (minimum): timestamp (ISO8601), service, severity.
    """
    cpu = float(raw.get("cpu_percent", 0))
    memory = float(raw.get("memory_percent", 0))
    latency = float(raw.get("network_latency_ms", 0))
    service = raw.get("service") or "unknown"
    ts = raw.get("timestamp")

    # Add values to sliding windows
    cpu_window.add(cpu)
    memory_window.add(memory)
    latency_window.add(latency)

    is_high_cpu = cpu > 80
    is_high_memory = memory > 85
    is_high_latency = latency > 200

    # Escalate severity for dashboards and downstream routing.
    # Note: this does not create incidents; it is a signal.
    severity = "info"
    if is_high_cpu or is_high_memory:
        severity = "warn"
    if is_high_latency:
        severity = "error"

    enriched = {
        "timestamp": ts,
        "service": service,
        "severity": severity,
        "cpu_percent": cpu,
        "cpu_avg_5": cpu_window.avg(),
        "memory_percent": memory,
        "memory_avg_5": memory_window.avg(),
        "network_latency_ms": latency,
        "latency_avg_5": latency_window.avg(),

        # Derived additional features
        "cpu_to_memory_ratio": round(cpu / memory, 4) if memory > 0 else 0,
        "is_high_cpu": is_high_cpu,
        "is_high_memory": is_high_memory,
        "is_high_latency": is_high_latency,
    }

    return enriched
