# processor.py

from window import SlidingWindow

cpu_window = SlidingWindow(max_size=5)
memory_window = SlidingWindow(max_size=5)
latency_window = SlidingWindow(max_size=5)

def enrich_metrics(raw):
    cpu = raw["cpu_percent"]
    memory = raw["memory_percent"]
    latency = raw["network_latency_ms"]

    # Add values to sliding windows
    cpu_window.add(cpu)
    memory_window.add(memory)
    latency_window.add(latency)

    enriched = {
        "timestamp": raw["timestamp"],
        "cpu_percent": cpu,
        "cpu_avg_5": cpu_window.avg(),
        "memory_percent": memory,
        "memory_avg_5": memory_window.avg(),
        "network_latency_ms": latency,
        "latency_avg_5": latency_window.avg(),

        # Derived additional features
        "cpu_to_memory_ratio": round(cpu / memory, 4) if memory > 0 else 0,
        "is_high_cpu": cpu > 80,
        "is_high_memory": memory > 85,
        "is_high_latency": latency > 200,
    }

    return enriched
