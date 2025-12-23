"""Rules engine (Phase 4 - initial version).

This is intentionally static and deterministic; later we can move these mappings into a YAML file.

Output actions follow the Phase 4 contract:
  - action_type
  - target_service
  - parameters

"""


def evaluate_rules(incident: dict) -> list[dict]:
    incident_type = incident.get("incident_type") or incident.get("type") or ""
    service = incident.get("service") or "unknown"

    actions: list[dict] = []

    def emit(action_type: str, parameters: dict | None = None, severity: str = "info") -> None:
        # action_key is used for simple de-dupe.
        actions.append(
            {
                "action_type": action_type,
                "target_service": service,
                "parameters": parameters or {},
                "severity": severity,
                "action_key": f"{incident_type}:{service}:{action_type}",
            }
        )

    # Example idempotent-ish actions (intent only; no executor yet)
    if incident_type == "AUTH_FAILURE":
        emit("RESTART_SERVICE", {"reason": "auth failures"}, severity="warn")
    elif incident_type == "CACHE_STORM":
        emit("CLEAR_CACHE", {"reason": "cache storm"}, severity="warn")
    elif incident_type == "DB_LATENCY":
        emit("RECYCLE_DB_POOL", {"reason": "db latency"}, severity="warn")
    elif incident_type == "REQUEST_TIMEOUT":
        emit("RESTART_SERVICE", {"reason": "request timeouts"}, severity="warn")
    elif incident_type == "ERROR_RATE_SPIKE":
        emit("ROLLING_RESTART", {"reason": "error rate spike"}, severity="critical")
    elif incident_type == "LATENCY_SPIKE":
        emit("SCALE_UP", {"replicas_delta": 1, "reason": "latency spike"}, severity="error")
    elif incident_type == "CPU_SPIKE":
        emit("SCALE_UP", {"replicas_delta": 1, "reason": "cpu spike"}, severity="warn")
    elif incident_type == "MEMORY_SPIKE":
        emit("ROLLING_RESTART", {"reason": "memory spike"}, severity="warn")

    return actions
