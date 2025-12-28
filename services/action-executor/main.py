import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("action-executor")


EVENT_ACTION_INTENT = "ACTION_INTENT"
EVENT_ACTION_RESULT = "ACTION_RESULT"
EVENT_ACTION_APPROVAL = "ACTION_APPROVAL"


PASSTHROUGH_FIELDS = [
    "incident_id",
    "test_id",
    "correlation_id",
    "rule_id",
    "source",
    "source_event_id",
]


@dataclass
class PendingAction:
    action: Dict[str, Any]
    expires_at: float


class ActionExecutor:
    def __init__(self):
        self.broker = os.getenv("KAFKA_BROKER", "kafka:9092")
        self.actions_topic = os.getenv("ACTIONS_TOPIC", "actions")
        self.group_id = os.getenv("GROUP_ID", "action-executor")
        self.auto_offset_reset = os.getenv("AUTO_OFFSET_RESET", "latest")

        self.require_approval = os.getenv("REQUIRE_APPROVAL", "false").lower() == "true"
        self.publish_results = os.getenv("PUBLISH_RESULTS", "true").lower() == "true"

        # Dedup executed actions to avoid repeated execution / loops.
        self.executed_dedup_ttl_sec = int(os.getenv("EXECUTED_DEDUP_TTL_SEC", "600"))
        self._executed: Dict[str, float] = {}  # action_id -> seen_at

        # Pending approvals (only relevant when require_approval=True)
        self.pending_ttl_sec = int(os.getenv("PENDING_APPROVAL_TTL_SEC", "900"))
        self._pending: Dict[str, PendingAction] = {}

        self.producer = KafkaProducer(
            bootstrap_servers=[self.broker],
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        logger.info(
            "Starting action-executor: broker=%s actions_topic=%s group_id=%s require_approval=%s publish_results=%s",
            self.broker,
            self.actions_topic,
            self.group_id,
            self.require_approval,
            self.publish_results,
        )

    # ------------------------------ utilities ------------------------------

    def _now(self) -> float:
        return time.time()

    def _is_duplicate(self, action_id: str) -> bool:
        if not action_id:
            return False
        now = self._now()
        # Prune old
        for k, ts in list(self._executed.items()):
            if now - ts > self.executed_dedup_ttl_sec:
                del self._executed[k]
        return action_id in self._executed

    def _mark_executed(self, action_id: str) -> None:
        if action_id:
            self._executed[action_id] = self._now()

    def _passthrough(self, src: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k in PASSTHROUGH_FIELDS:
            if k in src and src.get(k) is not None:
                out[k] = src.get(k)
        return out

    def _emit_action_result(
        self,
        *,
        action: Dict[str, Any],
        status: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.publish_results:
            # Still log for visibility.
            logger.info(
                "Action result (not published): action_id=%s status=%s message=%s",
                action.get("action_id"),
                status,
                message,
            )
            return

        payload: Dict[str, Any] = {
            "event_type": EVENT_ACTION_RESULT,
            "action_id": action.get("action_id"),
            "action_type": action.get("action_type"),
            "target_service": action.get("target_service"),
            "status": status,
            "message": message,
            "ts": int(self._now() * 1000),
            **self._passthrough(action),
        }
        if extra:
            payload.update(extra)

        self.producer.send(self.actions_topic, payload)
        self.producer.flush(2)

    # ------------------------------ approvals ------------------------------

    def _store_pending(self, action: Dict[str, Any]) -> None:
        action_id = action.get("action_id")
        if not action_id:
            return
        self._pending[action_id] = PendingAction(
            action=action,
            expires_at=self._now() + self.pending_ttl_sec,
        )

    def _expire_pending(self) -> None:
        if not self.require_approval:
            return
        now = self._now()
        for action_id, pending in list(self._pending.items()):
            if now >= pending.expires_at:
                logger.info("Pending approval expired: action_id=%s", action_id)
                self._emit_action_result(
                    action=pending.action,
                    status="EXPIRED",
                    message=f"Approval timeout ({self.pending_ttl_sec}s) exceeded",
                )
                del self._pending[action_id]
                self._mark_executed(action_id)

    def _handle_approval_event(self, ev: Dict[str, Any]) -> None:
        if not self.require_approval:
            return

        action_id = ev.get("action_id")
        decision_raw = (ev.get("decision") or "").strip().upper()
        decision = {
            "APPROVE": "APPROVE",
            "APPROVED": "APPROVE",
            "YES": "APPROVE",
            "Y": "APPROVE",
            "DENY": "DENY",
            "DENIED": "DENY",
            "REJECT": "DENY",
            "REJECTED": "DENY",
            "NO": "DENY",
            "N": "DENY",
        }.get(decision_raw)

        if not action_id or not decision:
            logger.info("Ignoring malformed approval event: %s", ev)
            return

        pending = self._pending.get(action_id)
        if not pending:
            logger.info("Approval received for unknown/non-pending action_id=%s; ignoring", action_id)
            return

        if decision == "DENY":
            logger.info("Action denied: action_id=%s", action_id)
            self._emit_action_result(
                action=pending.action,
                status="DENIED",
                message="Denied by approver",
                extra={"approver": ev.get("approver")},
            )
            del self._pending[action_id]
            self._mark_executed(action_id)
            return

        # Approved
        logger.info("Action approved: action_id=%s", action_id)
        approved_action = dict(pending.action)
        approved_action["status"] = "APPROVED"
        approved_action["approver"] = ev.get("approver")
        del self._pending[action_id]
        self._execute_intent(approved_action)

    # ------------------------------ execution ------------------------------

    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate remediation execution."""
        action_type = action.get("action_type")
        service = action.get("target_service")

        # In a real platform this would call Kubernetes (scale / rollout restart / etc).
        # Here we simulate side effects.
        if action_type == "SCALE_UP":
            return {
                "status": "EXECUTED",
                "message": f"Scaled up {service}",
                "executed_action": action_type,
            }
        if action_type == "ROLLING_RESTART":
            return {
                "status": "EXECUTED",
                "message": f"Performed rolling restart on {service}",
                "executed_action": action_type,
            }
        if action_type == "CLEAR_CACHE":
            return {
                "status": "EXECUTED",
                "message": f"Cleared cache for {service}",
                "executed_action": action_type,
            }
        if action_type == "RATE_LIMIT":
            return {
                "status": "EXECUTED",
                "message": f"Applied rate-limiting for {service}",
                "executed_action": action_type,
            }
        if action_type == "FAILOVER":
            return {
                "status": "EXECUTED",
                "message": f"Triggered failover for {service}",
                "executed_action": action_type,
            }

        return {
            "status": "FAILED",
            "message": f"Unsupported action_type: {action_type}",
            "executed_action": action_type,
        }

    def _execute_intent(self, action: Dict[str, Any]) -> None:
        action_id = action.get("action_id")
        if not action_id:
            logger.info("Skipping intent without action_id: %s", action)
            return

        if self._is_duplicate(action_id):
            logger.info("Skipping duplicate action: action_id=%s", action_id)
            return

        if self.require_approval and action.get("status") != "APPROVED":
            # Store and wait for an approval event.
            logger.info(
                "Action requires approval: action_id=%s type=%s target=%s status=%s",
                action_id,
                action.get("action_type"),
                action.get("target_service"),
                action.get("status"),
            )
            self._store_pending(action)
            self._emit_action_result(
                action=action,
                status="PENDING_APPROVAL",
                message="Awaiting human approval",
            )
            return

        result = self.execute_action(action)
        logger.info(
            "Executed action: action_id=%s type=%s target=%s status=%s",
            action_id,
            action.get("action_type"),
            action.get("target_service"),
            result.get("status"),
        )
        self._emit_action_result(
            action=action,
            status=result.get("status", "UNKNOWN"),
            message=result.get("message", ""),
            extra={"executed_action": result.get("executed_action")},
        )
        self._mark_executed(action_id)

    # ------------------------------ main loop ------------------------------

    def run(self) -> None:
        consumer = KafkaConsumer(
            self.actions_topic,
            bootstrap_servers=[self.broker],
            group_id=self.group_id,
            auto_offset_reset=self.auto_offset_reset,
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

        while True:
            # Periodically expire pending approvals.
            self._expire_pending()

            for msg in consumer.poll(timeout_ms=1000, max_records=50).values():
                for record in msg:
                    try:
                        ev = record.value
                    except Exception:
                        continue

                    ev_type = ev.get("event_type")

                    if ev_type == EVENT_ACTION_INTENT:
                        # Accept and act on intents only.
                        self._execute_intent(ev)
                        continue

                    if ev_type == EVENT_ACTION_APPROVAL:
                        self._handle_approval_event(ev)
                        continue

                    # Ignore results/other events to avoid loops.
                    continue


if __name__ == "__main__":
    ActionExecutor().run()
