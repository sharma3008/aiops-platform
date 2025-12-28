#!/usr/bin/env bash
set -euo pipefail

# Phase 5: Human-in-the-loop approval + execution
#
# Validates:
#   1) Auto-remediator proposes an action (status != APPROVED) when REQUIRE_APPROVAL=true
#   2) Action-executor publishes ACTION_RESULT=PENDING_APPROVAL and does NOT execute
#   3) After an ACTION_APPROVAL event, action-executor executes and publishes ACTION_RESULT=EXECUTED
#
# Usage:
#   cd infra
#   REQUIRE_APPROVAL=true ./runbookphase5.sh

cd "$(dirname "$0")"

export REQUIRE_APPROVAL="${REQUIRE_APPROVAL:-true}"

echo "[phase5] REQUIRE_APPROVAL=${REQUIRE_APPROVAL}"

# Recreate + build so:
#  - REQUIRE_APPROVAL env substitution takes effect
#  - any local code changes are included in the image
docker compose up -d --build --force-recreate auto-remediator action-executor

# Quick runtime env sanity check (helps catch compose env-substitution issues).
echo "[phase5] runtime env check:"
echo -n "auto-remediator REQUIRE_APPROVAL="
docker compose exec -T auto-remediator sh -lc 'echo ${REQUIRE_APPROVAL:-<unset>}'
echo -n "action-executor REQUIRE_APPROVAL="
docker compose exec -T action-executor sh -lc 'echo ${REQUIRE_APPROVAL:-<unset>}'

echo "[phase5] Running approval-flow test via Kafka..."

# Run the test inside the action-executor container (it already has kafka-python installed).
docker compose exec -T action-executor python - <<'PY'
import json
import os
import sys
import time
import uuid

from kafka import KafkaConsumer, KafkaProducer

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INCIDENTS_TOPIC = os.getenv("INCIDENTS_TOPIC", "incidents")
ACTIONS_TOPIC = os.getenv("ACTIONS_TOPIC", "actions")

TEST_ID = f"phase5-{uuid.uuid4().hex[:8]}"
INCIDENT_ID = uuid.uuid4().hex
NOW_MS = int(time.time() * 1000)

producer = KafkaProducer(
    bootstrap_servers=[BROKER],
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

# Consumer: start at end so we only see new events emitted by this run.
gid = f"runbook-phase5-{uuid.uuid4().hex[:8]}"
consumer = KafkaConsumer(
    ACTIONS_TOPIC,
    bootstrap_servers=[BROKER],
    group_id=gid,
    enable_auto_commit=False,
    auto_offset_reset="latest",
    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
)

# Ensure partitions are assigned before seek_to_end().
for _ in range(15):
    consumer.poll(timeout_ms=500)
    parts = consumer.assignment()
    if parts:
        break
    time.sleep(0.2)

parts = consumer.assignment()
if not parts:
    print("[phase5][FAIL] consumer did not get partition assignment for actions topic")
    sys.exit(1)

consumer.seek_to_end()
print(f"[phase5] consumer assigned partitions={sorted(parts, key=lambda p: p.partition)} (seeked to end)")

# Trigger an incident that maps (via alias) to allowlisted service "payments".
incident = {
    "event_type": "INCIDENT",
    "incident_id": INCIDENT_ID,
    "service": "payment-service",  # alias -> payments
    "incident_type": "LATENCY_SPIKE",
    "severity": "error",
    "ts": NOW_MS,
    "test_id": TEST_ID,
}

producer.send(INCIDENTS_TOPIC, incident)
producer.flush()
print(f"[phase5] sent incident test_id={TEST_ID} incident_id={INCIDENT_ID}")


def matches_this_run(ev: dict) -> bool:
    # Preferred correlation (unique per run)
    if ev.get("incident_id") == INCIDENT_ID:
        return True

    # Backwards compatibility: some events embed the original incident under `incident`.
    inc = ev.get("incident") or {}
    if isinstance(inc, dict):
        if inc.get("incident_id") == INCIDENT_ID:
            return True
        if inc.get("test_id") == TEST_ID:
            return True

    # Last resort correlation
    return ev.get("test_id") == TEST_ID


# Wait for the action proposal + the executor's pending result.
action_id = None
pending_action_id = None
pending_deadline = time.time() + 30

while time.time() < pending_deadline and pending_action_id is None:
    records = consumer.poll(timeout_ms=1000)
    for _tp, msgs in records.items():
        for msg in msgs:
            ev = msg.value
            if not isinstance(ev, dict) or not matches_this_run(ev):
                continue

            et = ev.get("event_type")
            if et == "ACTION_INTENT":
                action_id = ev.get("action_id")
                st = ev.get("status")
                print(f"[phase5] observed ACTION_INTENT action_id={action_id} status={st}")

            if et == "ACTION_RESULT" and ev.get("status") == "PENDING_APPROVAL":
                pending_action_id = ev.get("action_id")
                print(f"[phase5] observed ACTION_RESULT=PENDING_APPROVAL action_id={pending_action_id}")
                break
        if pending_action_id:
            break

if not action_id and not pending_action_id:
    print("[phase5][FAIL] did not observe ACTION_INTENT for this incident")
    print("  Hint: check auto-remediator logs; also confirm docker-compose uses REQUIRE_APPROVAL env substitution.")
    sys.exit(1)

if not pending_action_id:
    # If we saw an ACTION_INTENT but no pending result, it usually means the executor executed immediately.
    print("[phase5][FAIL] did not observe ACTION_RESULT=PENDING_APPROVAL (executor may have executed immediately)")
    print("  Hint: confirm REQUIRE_APPROVAL=true for action-executor, and that ACTION_INTENT status != APPROVED.")
    sys.exit(1)

# Approve.
approval = {
    "event_type": "ACTION_APPROVAL",
    "action_id": pending_action_id,
    "decision": "APPROVE",
    "approver": "runbookphase5",
    "ts": int(time.time() * 1000),
    "test_id": TEST_ID,
}
producer.send(ACTIONS_TOPIC, approval)
producer.flush()
print("[phase5] sent approval")

# Wait for terminal result.
terminal_status = None
terminal_message = None
terminal_deadline = time.time() + 30
while time.time() < terminal_deadline and terminal_status is None:
    records = consumer.poll(timeout_ms=1000)
    for _tp, msgs in records.items():
        for msg in msgs:
            ev = msg.value
            if not isinstance(ev, dict):
                continue
            if ev.get("action_id") != pending_action_id:
                continue
            if ev.get("event_type") != "ACTION_RESULT":
                continue
            st = ev.get("status")
            if st in {"EXECUTED", "FAILED", "DENIED", "EXPIRED"}:
                terminal_status = st
                terminal_message = ev.get("message")
                break
        if terminal_status:
            break

if not terminal_status:
    print("[phase5][FAIL] did not observe a terminal ACTION_RESULT after approval")
    sys.exit(1)

print(f"[phase5] terminal_status={terminal_status} message={terminal_message}")

if terminal_status != "EXECUTED":
    print("[phase5][FAIL] expected EXECUTED")
    sys.exit(1)

print("[phase5][PASS] approval-gated action was executed")
PY
