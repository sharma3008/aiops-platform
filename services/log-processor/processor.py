def normalize_log(log: dict) -> dict:
    level = (log.get("level") or "INFO").upper()

    severity_map = {
        "DEBUG": "debug",
        "INFO": "info",
        "WARN": "warn",
        "WARNING": "warn",
        "ERROR": "error",
        "CRITICAL": "critical",
        "FATAL": "critical",
    }

    service = log.get("service") or "unknown"
    message = log.get("message") or ""

    return {
        "timestamp": log.get("timestamp"),
        "service": service,
        "message": message,
        "severity": severity_map.get(level, "info"),
        "trace_id": log.get("trace_id"),
        "original_level": level,
    }
