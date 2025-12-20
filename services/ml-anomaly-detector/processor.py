def extract_features(msg):
    """
    Takes enriched metric from metrics.enriched and extracts ML features.
    """
    return [
        msg.get("cpu_avg_5", 0),
        msg.get("memory_avg_5", 0),
        msg.get("latency_avg_5", 0),
    ]
