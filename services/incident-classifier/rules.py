# rules.py

def classify_from_logs(log):
    """
    Rule-based simple pattern matching.
    """
    msg = log.get("message", "").lower()
    level = log.get("severity", 0)
    service = log.get("service", "")

    if "authentication failed" in msg:
        return "AUTH_FAILURE"

    if "db connection slow" in msg:
        return "DB_LATENCY"

    if "payment request timeout" in msg:
        return "REQUEST_TIMEOUT"

    if "cache miss" in msg:
        return "CACHE_STORM"

    # Any severity 4 log = error burst
    if level >= 4:
        return "ERROR_BURST"

    return None
