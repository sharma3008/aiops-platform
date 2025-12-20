# rules.py

def get_remediation_action(incident):
    incident_type = incident.get("type", "")
    service = incident.get("service", "unknown-service")

    # Rule-based remediation
    if incident_type == "AUTH_FAILURE":
        return f"restart {service}"

    if incident_type == "CACHE_STORM":
        return f"clear_cache for {service}"

    if incident_type == "DB_LATENCY":
        return "restart_db_connection_pool"

    if incident_type == "REQUEST_TIMEOUT":
        return f"restart {service}"

    if incident_type == "ERROR_BURST":
        return f"investigate_errors in {service}"

    if incident_type == "LATENCY_SPIKE":
        return f"scale_up_pods for {service}"

    if incident_type == "MEMORY_LEAK":
        return f"restart {service} (memory leak suspected)"

    if incident_type == "HIGH_CPU_ANOMALY":
        return f"add_cpu_resources to {service}"

    return "no_action_required"
