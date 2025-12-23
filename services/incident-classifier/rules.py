"""Log-based rules.

These are intentionally lightweight keyword rules to augment the ML pipeline.
Burst-style detection is handled in main.py via a sliding window.
"""


def classify_from_logs(log: dict) -> dict | None:
    msg = (log.get("message") or "").lower()
    service = log.get("service") or "unknown"
    sev = (log.get("severity") or "info").lower()

    if "authentication failed" in msg:
        return {
            "incident_type": "AUTH_FAILURE",
            "severity": "error" if sev in {"info", "warn"} else sev,
            "details": {"service": service},
        }

    if "db connection slow" in msg or "db latency" in msg:
        return {
            "incident_type": "DB_LATENCY",
            "severity": "warn" if sev == "info" else sev,
            "details": {"service": service},
        }

    if "payment request timeout" in msg or "request timeout" in msg:
        return {
            "incident_type": "REQUEST_TIMEOUT",
            "severity": "error" if sev in {"info", "warn"} else sev,
            "details": {"service": service},
        }

    if "cache miss" in msg or "cache storm" in msg:
        return {
            "incident_type": "CACHE_STORM",
            "severity": "warn" if sev == "info" else sev,
            "details": {"service": service},
        }

    return None
