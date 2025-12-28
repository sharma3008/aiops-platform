import hashlib
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from kafka import KafkaConsumer, KafkaProducer

from rules import canonicalize_incident, evaluate_rules_with_diagnostics

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INCIDENT_TOPIC = os.getenv("INCIDENTS_TOPIC", "incidents")
ACTION_TOPIC = os.getenv("ACTIONS_TOPIC", "actions")

GROUP_ID = os.getenv("KAFKA_GROUP_ID", "auto-remediator")

DEDUP_TTL_SEC = int(os.getenv("DEDUP_TTL_SEC", "600"))
DEFAULT_COOLDOWN_SEC = int(os.getenv("DEFAULT_COOLDOWN_SEC", "180"))

REQUIRE_APPROVAL = os.getenv("REQUIRE_APPROVAL", "false").lower() in ("1", "true", "yes", "y")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")

_running = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_id(payload: Dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:24]


def _incident_id(incident: Dict[str, Any]) -> str:
    base = {
        "timestamp": incident.get("timestamp"),
        "service": incident.get("service"),
        "incident_type": incident.get("incident_type"),
        "source": incident.get("source"),
        "details": incident.get("details", {}),
    }
    return _hash_id(base)


def _action_id(incident_id: str, rule_id: str, action_type: str, target_service: str, parameters: Dict[str, Any]) -> str:
    base = {
        "incident_id": incident_id,
        "rule_id": rule_id,
        "action_type": action_type,
        "target_service": target_service,
        "parameters": parameters,
    }
    return _hash_id(base)


def _install_signal_handlers():
    def _handle(_sig, _frame):
        global _running
        _running = False

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


class TTLSet:
    """Simple in-memory TTL set for deduplication."""

    def __init__(self, ttl_sec: int):
        self.ttl_sec = ttl_sec
        self._store: Dict[str, float] = {}

    def add(self, key: str, now: Optional[float] = None):
        now = now or time.time()
        self._store[key] = now + self.ttl_sec

    def contains(self, key: str, now: Optional[float] = None) -> bool:
        now = now or time.time()
        exp = self._store.get(key)
        if exp is None:
            return False
        if exp < now:
            self._store.pop(key, None)
            return False
        return True

    def gc(self, now: Optional[float] = None):
        now = now or time.time()
        expired = [k for k, exp in self._store.items() if exp < now]
        for k in expired:
            self._store.pop(k, None)


class CooldownTracker:
    """Cooldown per (rule_id, target_service)."""

    def __init__(self):
        self._next_allowed: Dict[str, float] = {}

    def key(self, rule_id: str, target_service: str) -> str:
        return f"{rule_id}:{target_service}"

    def is_active(self, rule_id: str, target_service: str, now: float) -> bool:
        k = self.key(rule_id, target_service)
        t = self._next_allowed.get(k, 0.0)
        return now < t

    def activate(self, rule_id: str, target_service: str, cooldown_sec: int, now: float):
        k = self.key(rule_id, target_service)
        self._next_allowed[k] = now + max(0, int(cooldown_sec))


def main():
    global _running
    _install_signal_handlers()

    logging.info(
        "Starting auto-remediator: broker=%s incident_topic=%s action_topic=%s",
        BROKER,
        INCIDENT_TOPIC,
        ACTION_TOPIC,
    )

    dedup = TTLSet(ttl_sec=DEDUP_TTL_SEC)
    cooldowns = CooldownTracker()

    consumer = KafkaConsumer(
        INCIDENT_TOPIC,
        bootstrap_servers=BROKER,
        group_id=GROUP_ID,
        enable_auto_commit=True,
        auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "latest"),
        value_deserializer=lambda v: json.loads(v.decode("utf-8")) if v else {},
        consumer_timeout_ms=1000,
    )

    producer = KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        retries=5,
        linger_ms=10,
    )

    try:
        while _running:
            for msg in consumer:
                incident = canonicalize_incident(msg.value or {})
                now = time.time()

                incident_id = incident.get("incident_id") or _incident_id(incident)
                correlation_id = incident.get("correlation_id") or incident_id

                # Log alias normalization when it happens (makes demos/debugging clear).
                if incident.get("_service_alias_raw") and incident.get("_service_alias_canonical"):
                    logging.info(
                        "Service alias normalized: raw_service=%s canonical_service=%s incident_id=%s",
                        incident.get("_service_alias_raw"),
                        incident.get("_service_alias_canonical"),
                        incident_id,
                    )

                actions, diag = evaluate_rules_with_diagnostics(incident)
                if not actions:
                    if diag.get("matched_candidates") and diag.get("policy_rejects"):
                        logging.info(
                            "Policy rejected candidate actions: incident_id=%s correlation_id=%s service=%s incident_type=%s rejects=%s",
                            incident_id,
                            correlation_id,
                            incident.get("service"),
                            incident.get("incident_type"),
                            diag.get("policy_rejects"),
                        )
                    continue

                for a in actions:
                    rule_id = a.get("rule_id", "unknown_rule")
                    action_type = a.get("action_type", "UNKNOWN")
                    target_service = a.get("target_service") or incident.get("service") or "unknown"
                    params = a.get("parameters") or {}

                    # Apply templating if needed (supports "{service}" placeholder)
                    if isinstance(target_service, str):
                        target_service = target_service.format(service=incident.get("service", "unknown"))

                    # Determine cooldown
                    cooldown_sec = int(a.get("cooldown_sec", DEFAULT_COOLDOWN_SEC))
                    if cooldowns.is_active(rule_id, target_service, now):
                        logging.info(
                            "Cooldown active (rule=%s target=%s cd=%ss); skipping action_id=%s",
                            rule_id,
                            target_service,
                            cooldown_sec,
                            _action_id(incident_id, rule_id, action_type, target_service, params),
                        )
                        continue

                    action_id = _action_id(incident_id, rule_id, action_type, target_service, params)

                    if dedup.contains(action_id, now):
                        logging.info("Skipping duplicate action_id=%s", action_id)
                        continue

                    status = "APPROVED" if not REQUIRE_APPROVAL else "PROPOSED"

                    action_event = {
                        "event_type": "ACTION_INTENT",
                        "timestamp": incident.get("timestamp") or _now_iso(),
                        "service": incident.get("service", "unknown"),
                        "severity": "warn",
                        "action_id": action_id,
                        "correlation_id": correlation_id,
	                        # Propagate identifiers for runbooks / observability.
	                        "test_id": incident.get("test_id"),
	                        "trace_id": incident.get("trace_id"),
                        "incident_id": incident_id,
                        "rule_id": rule_id,
                        "status": status,
                        "action_type": action_type,
                        "target_service": target_service,
                        "parameters": params,
                        "source": "auto_remediator",
                        "incident": incident,
                    }

                    producer.send(ACTION_TOPIC, action_event)
                    producer.flush(5)

                    dedup.add(action_id, now)
                    cooldowns.activate(rule_id, target_service, cooldown_sec, now)

                    logging.info(
                        "Emitted action intent: action_id=%s type=%s target=%s status=%s",
                        action_id,
                        action_type,
                        target_service,
                        status,
                    )

            dedup.gc()
    finally:
        try:
            consumer.close()
        except Exception:
            pass
        try:
            producer.flush(2)
            producer.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
