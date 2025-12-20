# processor.py

def normalize_log(log):
    """
    Takes a raw log dict and returns a normalized/enriched version.
    """

    level_map = {
        "DEBUG": 1,
        "INFO": 2,
        "WARN": 3,
        "ERROR": 4
    }

    return {
        "timestamp": log.get("timestamp"),
        "service": log.get("service"),
        "message": log.get("message"),
        "severity": level_map.get(log.get("level"), 0),
        "trace_id": log.get("trace_id"),
        "original_level": log.get("level"),
    }
