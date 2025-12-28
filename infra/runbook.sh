#!/usr/bin/env bash
set -euo pipefail

# Master Runbook (Phases 1–5) for AIOps Platform
#
# What it validates:
#   Phase 1: Stack up + Kafka/Loki/Grafana reachable + Loki has logs (vector -> loki)
#   Phase 2: Metrics anomaly -> ml_output is emitted (inject deterministic metrics_enriched)
#   Phase 3: Error burst -> incidents emitted (inject deterministic logs_parsed burst)
#   Phase 4: Auto-remediator policy checks:
#            - alias payment-service -> payments produces ACTION_INTENT
#            - unknown service produces NO ACTION_INTENT (denylist)
#   Phase 5: Human-in-the-loop approval:
#            - REQUIRE_APPROVAL=true => PENDING_APPROVAL then EXECUTED after approval
#
# Usage:
#   cd infra
#   ./runbook.sh                # runs all phases
#   ./runbook.sh all            # runs all phases
#   ./runbook.sh phase1         # run only phase 1
#   ./runbook.sh phase4         # run only phase 4
#   REQUIRE_APPROVAL=true ./runbook.sh phase5
#
# Notes:
# - This runbook uses docker compose and executes Python snippets inside action-executor container.

cd "$(dirname "$0")"

log() { printf "%s\n" "$*"; }
die() { printf "%s\n" "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need_cmd docker
need_cmd curl

COMPOSE="docker compose"

phase_header() {
  log ""
  log "============================================================"
  log "$1"
  log "============================================================"
}

wait_for_kafka() {
  log "[wait] Kafka..."
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if $COMPOSE exec -T kafka bash -lc "kafka-topics --bootstrap-server kafka:9092 --list >/dev/null 2>&1"; then
      log "[wait] Kafka is ready"
      return 0
    fi
    sleep 1
  done
  die "[FAIL] Kafka did not become ready in time"
}

wait_for_loki() {
  log "[wait] Loki..."
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if curl -fsS "http://localhost:3100/ready" >/dev/null 2>&1; then
      log "[wait] Loki is ready"
      return 0
    fi
    sleep 1
  done
  die "[FAIL] Loki did not become ready in time"
}

wait_for_grafana() {
  log "[wait] Grafana..."
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if curl -fsS "http://localhost:3000/api/health" >/dev/null 2>&1; then
      log "[wait] Grafana is ready"
      return 0
    fi
    sleep 1
  done
  die "[FAIL] Grafana did not become ready in time"
}

phase0_up() {
  phase_header "Phase 0: Bring stack up"

  # Quick Docker engine sanity check
  docker info >/dev/null 2>&1 || die "[FAIL] Docker engine not responding. Start Docker Desktop and retry."

  # Bring everything up (remove-orphans avoids warnings like kafka-init)
  $COMPOSE up -d --remove-orphans

  # Print a compact status view
  $COMPOSE ps

  wait_for_kafka
  wait_for_loki
  wait_for_grafana

  log "[PASS] Stack is up and core endpoints respond"
}

phase1_smoke() {
  phase_header "Phase 1: Observability smoke test (Vector -> Loki)"

  # Ensure Loki contains at least one log stream from our pipeline in the last ~10 minutes.
  # IMPORTANT: This project labels streams with source/topic/service/severity (not job=vector).
  # Run inside action-executor container (no host python dependency).
  $COMPOSE exec -T action-executor python - <<'PY'
import json, time, urllib.parse, urllib.request

BASE="http://loki:3100"

# Stable selector for this project.
# If you want a stricter check, use: '{source="aiops", topic="logs_parsed"}'
query = '{source="aiops"}'

end_ns = int(time.time() * 1e9)
start_ns = end_ns - int(600 * 1e9)  # last 10 minutes

params = {
  "query": query,
  "start": str(start_ns),
  "end": str(end_ns),
  "limit": "1",
  "direction": "BACKWARD",
}

url = BASE + "/loki/api/v1/query_range?" + urllib.parse.urlencode(params)
with urllib.request.urlopen(url, timeout=10) as r:
  data = json.loads(r.read().decode("utf-8"))

result = (((data or {}).get("data") or {}).get("result") or [])
if not result:
  raise SystemExit(f"[FAIL] Loki returned no log streams for {query} (expected at least 1)")

stream = result[0].get("stream", {})
print("[PASS] Loki has logs from Vector pipeline", stream)
PY

  # Ensure core Kafka topics exist.
  log "[phase1] Kafka topics check..."
  topics="$($COMPOSE exec -T kafka bash -lc "kafka-topics --bootstrap-server kafka:9092 --list" | tr -d '\r')"

  required_topics=(
    "logs_raw"
    "logs_parsed"
    "metrics_raw"
    "metrics_enriched"
    "ml_output"
    "incidents"
    "actions"
  )

  for t in "${required_topics[@]}"; do
    echo "$topics" | grep -qx "$t" || die "[FAIL] Missing Kafka topic: $t"
  done

  log "[PASS] Phase 1 complete"
}

phase2_metrics_anomaly() {
  phase_header "Phase 2: Deterministic metrics anomaly -> ml_output"

  $COMPOSE exec -T action-executor python - <<'PY'
import json, os, time, uuid, sys
from kafka import KafkaProducer, KafkaConsumer, TopicPartition

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
METRICS_ENRICHED = os.getenv("METRICS_ENRICHED_TOPIC", "metrics_enriched")
ML_OUT = os.getenv("ML_OUTPUT_TOPIC", "ml_output")

run_id = f"phase2-{uuid.uuid4().hex[:8]}"
service = f"payments-{run_id}"
now_ms = int(time.time() * 1000)

producer = KafkaProducer(
  bootstrap_servers=[BROKER],
  value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

# Consumer with manual assignment so we can safely snapshot end offsets
consumer = KafkaConsumer(
  bootstrap_servers=[BROKER],
  enable_auto_commit=False,
  auto_offset_reset="latest",
  value_deserializer=lambda m: json.loads(m.decode("utf-8")),
)

# Discover partitions for ml_output and assign them
parts = consumer.partitions_for_topic(ML_OUT)
if not parts:
  print("[FAIL] ml_output topic has no partitions (topic missing or broker issue)")
  sys.exit(1)

tps = [TopicPartition(ML_OUT, p) for p in sorted(parts)]
consumer.assign(tps)

# Snapshot current end offsets BEFORE we produce our test message
end_offsets = consumer.end_offsets(tps)
print("[phase2] end_offsets(before) =", {f"{tp.partition}": end_offsets[tp] for tp in tps})

payload = {
  "service": service,
  "timestamp": now_ms,
  "cpu_avg_5": 20.0,
  "memory_avg_5": 30.0,
  "latency_avg_5": 800.0,
  "cpu_percent": 20.0,
  "memory_percent": 30.0,
  "severity": "warning",
  "test_id": run_id,   # not expected to return in ml_output; just for tracing
}

producer.send(METRICS_ENRICHED, payload)
producer.flush()
print(f"[phase2] sent metrics_enriched service={service} ts={now_ms}")

# Start consuming from the previous end offsets
for tp in tps:
  consumer.seek(tp, end_offsets[tp])

# Wait up to 45s for an ml_output record with our unique service
deadline = time.time() + 45
found = None

while time.time() < deadline and found is None:
  records = consumer.poll(timeout_ms=1000)
  for tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("service") != service:
        continue
      if int(ev.get("timestamp") or 0) < now_ms:
        continue
      found = ev
      break
    if found:
      break

if not found:
  print("[FAIL] did not observe ml_output for injected metrics within timeout")
  print("  Debug tips:")
  print("   - confirm ml-anomaly-detector logs show the same service name")
  print("   - check kafka topic wiring: metrics_enriched -> ml-anomaly-detector -> ml_output")
  sys.exit(1)

print("[PASS] ml_output observed for injected metrics:", {
  "service": found.get("service"),
  "is_anomaly": found.get("is_anomaly"),
  "anomaly_type": found.get("anomaly_type"),
  "anomaly_score": found.get("anomaly_score"),
})
PY

  log "[PASS] Phase 2 complete"
}

phase3_incident_from_logs() {
  phase_header "Phase 3: Deterministic log error burst -> incidents"

  # Inject 9 error logs to logs_parsed for payments within the burst window.
  # incident-classifier should emit ERROR_RATE_SPIKE incident (critical).
  $COMPOSE exec -T action-executor python - <<'PY'
import json, os, time, uuid, sys
from kafka import KafkaProducer, KafkaConsumer, TopicPartition

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
LOGS_PARSED = os.getenv("LOGS_PARSED_TOPIC", "logs_parsed")
INCIDENTS = os.getenv("INCIDENTS_TOPIC", "incidents")

test_id = f"phase3-{uuid.uuid4().hex[:8]}"
start_ms = int(time.time() * 1000)

producer = KafkaProducer(
  bootstrap_servers=[BROKER],
  value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

# Use explicit partition assignment + end-offset snapshot for reliable "new messages only"
consumer = KafkaConsumer(
  bootstrap_servers=[BROKER],
  enable_auto_commit=False,
  auto_offset_reset="latest",
  value_deserializer=lambda m: json.loads(m.decode("utf-8")),
)

parts = consumer.partitions_for_topic(INCIDENTS)
if not parts:
  print("[FAIL] incidents topic has no partitions (topic missing or broker issue)")
  sys.exit(1)

tps = [TopicPartition(INCIDENTS, p) for p in sorted(parts)]
consumer.assign(tps)

end_offsets = consumer.end_offsets(tps)
for tp in tps:
  consumer.seek(tp, end_offsets[tp])

# Send 9 errors quickly (threshold in service is 8 within 30s by default)
for i in range(9):
  ev = {
    "timestamp": int(time.time() * 1000),  # classifier accepts this OR will fill ISO time
    "service": "payments",
    "severity": "error",
    "message": f"{test_id} timeout connecting to db (burst)",
  }
  producer.send(LOGS_PARSED, ev)

producer.flush()
print(f"[phase3] sent 9 error logs to logs_parsed test_id={test_id}")

# Wait for ERROR_RATE_SPIKE incident for payments that references our test_id in sample_log.message
deadline = time.time() + 45
found = None

while time.time() < deadline and found is None:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      inc = msg.value
      if inc.get("service") != "payments":
        continue
      if inc.get("incident_type") != "ERROR_RATE_SPIKE":
        continue

      # Correlate via sample_log.message which contains our test_id string
      details = inc.get("details") or {}
      sample_log = details.get("sample_log") or {}
      sample_msg = sample_log.get("message") or ""
      if test_id not in sample_msg:
        continue

      found = inc
      break
    if found:
      break

if not found:
  print("[FAIL] did not observe ERROR_RATE_SPIKE incident from burst logs")
  print("  Debug tips:")
  print("   - check incident-classifier logs for 'Published incident: type=ERROR_RATE_SPIKE'")
  print("   - verify ERROR_BURST_THRESHOLD/ERROR_BURST_WINDOW_SEC env values")
  sys.exit(1)

print("[PASS] incident observed:", {
  "incident_type": found.get("incident_type"),
  "severity": found.get("severity"),
  "service": found.get("service"),
  "count": (found.get("details") or {}).get("count"),
  "threshold": (found.get("details") or {}).get("threshold"),
})
PY

  log "[PASS] Phase 3 complete"
}

phase4_policy_alias_deny() {
  phase_header "Phase 4: Auto-remediator policy (alias + denylist)"

  # Ensure approval is NOT required for phase4 (we want APPROVED intents).
  export REQUIRE_APPROVAL=false

  # Recreate services so env is applied (docker compose reads env from current shell).
  $COMPOSE up -d --force-recreate auto-remediator action-executor

  $COMPOSE exec -T action-executor python - <<'PY'
import json, os, time, uuid, sys
from kafka import KafkaProducer, KafkaConsumer, TopicPartition

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INCIDENTS = os.getenv("INCIDENTS_TOPIC", "incidents")
ACTIONS = os.getenv("ACTIONS_TOPIC", "actions")

producer = KafkaProducer(
  bootstrap_servers=[BROKER],
  value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

consumer = KafkaConsumer(
  bootstrap_servers=[BROKER],
  enable_auto_commit=False,
  auto_offset_reset="latest",
  value_deserializer=lambda m: json.loads(m.decode("utf-8")),
)

# Assign partitions for actions topic and snapshot end offsets (read only new events)
parts = consumer.partitions_for_topic(ACTIONS)
if not parts:
  print("[FAIL] actions topic has no partitions")
  sys.exit(1)

tps = [TopicPartition(ACTIONS, p) for p in sorted(parts)]
consumer.assign(tps)
end_offsets = consumer.end_offsets(tps)
for tp in tps:
  consumer.seek(tp, end_offsets[tp])

# (1) Alias incident: payment-service -> payments should produce ACTION_INTENT
incident_id_1 = uuid.uuid4().hex
now_ms = int(time.time() * 1000)

incident1 = {
  "event_type": "INCIDENT",
  "incident_id": incident_id_1,
  "service": "payment-service",
  "incident_type": "LATENCY_SPIKE",
  "severity": "error",
  "ts": now_ms,
  "test_id": f"phase4-{uuid.uuid4().hex[:8]}",  # may not be forwarded; do not rely on it
}
producer.send(INCIDENTS, incident1)
producer.flush()
print(f"[phase4] sent alias incident incident_id={incident_id_1}")

intent = None
deadline = time.time() + 30
while time.time() < deadline and intent is None:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("event_type") != "ACTION_INTENT":
        continue

      # Correlate on incident_id (most reliable)
      if ev.get("incident_id") != incident_id_1:
        continue

      # Validate alias resolution: payment-service -> payments
      target = ev.get("target_service") or ev.get("service")
      if target != "payments":
        print("[FAIL] ACTION_INTENT observed but target_service is not 'payments':", ev)
        sys.exit(1)

      # Accept APPROVED or PROPOSED; PROPOSED indicates REQUIRE_APPROVAL may still be true.
      if ev.get("status") not in {"APPROVED", "PROPOSED"}:
        print("[FAIL] ACTION_INTENT status unexpected:", ev.get("status"), ev)
        sys.exit(1)

      intent = ev
      break
    if intent:
      break

if not intent:
  print("[FAIL] did not observe ACTION_INTENT for alias incident_id")
  print("  Debug tips:")
  print("   - check auto-remediator logs")
  print("   - verify auto-remediator is consuming 'incidents' and producing to 'actions'")
  sys.exit(1)

print("[PASS] alias produced ACTION_INTENT", {
  "action_id": intent.get("action_id"),
  "status": intent.get("status"),
  "target_service": intent.get("target_service"),
  "incident_id": intent.get("incident_id"),
})

# (2) Deny unknown service: should NOT produce ACTION_INTENT
incident_id_2 = uuid.uuid4().hex
incident2 = {
  "event_type": "INCIDENT",
  "incident_id": incident_id_2,
  "service": "unknown",
  "incident_type": "LATENCY_SPIKE",
  "severity": "error",
  "ts": int(time.time() * 1000),
}
producer.send(INCIDENTS, incident2)
producer.flush()
print(f"[phase4] sent unknown incident incident_id={incident_id_2}")

# Wait briefly; any ACTION_INTENT correlated to incident_id_2 is a failure.
deadline = time.time() + 10
while time.time() < deadline:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("event_type") != "ACTION_INTENT":
        continue
      if ev.get("incident_id") == incident_id_2:
        print("[FAIL] observed ACTION_INTENT for denylisted service 'unknown':", ev)
        sys.exit(1)

print("[PASS] deny_unknown produced no ACTION_INTENT")
PY

  log "[PASS] Phase 4 complete"
}

phase5_approval_flow() {
  phase_header "Phase 5: Human-in-the-loop approval (REQUIRE_APPROVAL=true)"

  export REQUIRE_APPROVAL=true

  # Recreate services so REQUIRE_APPROVAL takes effect.
  $COMPOSE up -d --force-recreate auto-remediator action-executor

  $COMPOSE exec -T action-executor python - <<'PY'
import json, os, sys, time, uuid
from kafka import KafkaConsumer, KafkaProducer, TopicPartition

BROKER = os.getenv("KAFKA_BROKER", "kafka:9092")
INCIDENTS_TOPIC = os.getenv("INCIDENTS_TOPIC", "incidents")
ACTIONS_TOPIC = os.getenv("ACTIONS_TOPIC", "actions")

INCIDENT_ID = uuid.uuid4().hex
NOW_MS = int(time.time() * 1000)

producer = KafkaProducer(
  bootstrap_servers=[BROKER],
  value_serializer=lambda v: json.dumps(v).encode("utf-8"),
)

consumer = KafkaConsumer(
  bootstrap_servers=[BROKER],
  enable_auto_commit=False,
  auto_offset_reset="latest",
  value_deserializer=lambda m: json.loads(m.decode("utf-8")),
)

# Assign partitions explicitly and snapshot end offsets to read only new events
parts = consumer.partitions_for_topic(ACTIONS_TOPIC)
if not parts:
  print("[phase5][FAIL] actions topic has no partitions")
  sys.exit(1)

tps = [TopicPartition(ACTIONS_TOPIC, p) for p in sorted(parts)]
consumer.assign(tps)

end_offsets = consumer.end_offsets(tps)
for tp in tps:
  consumer.seek(tp, end_offsets[tp])

print(f"[phase5] assigned={tps} end_offsets(before)={{p.partition: end_offsets[p] for p in tps}}")

# Trigger incident -> should produce ACTION_INTENT (likely PROPOSED when REQUIRE_APPROVAL=true)
incident = {
  "event_type": "INCIDENT",
  "incident_id": INCIDENT_ID,
  "service": "payment-service",  # alias -> payments
  "incident_type": "LATENCY_SPIKE",
  "severity": "error",
  "ts": NOW_MS,
}
producer.send(INCIDENTS_TOPIC, incident)
producer.flush()
print(f"[phase5] sent incident incident_id={INCIDENT_ID}")

# Wait for ACTION_INTENT correlated by incident_id
intent = None
deadline = time.time() + 40
while time.time() < deadline and intent is None:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("event_type") != "ACTION_INTENT":
        continue
      if ev.get("incident_id") != INCIDENT_ID:
        continue
      intent = ev
      break
    if intent:
      break

if not intent:
  print("[phase5][FAIL] did not observe ACTION_INTENT for incident_id")
  print("  Next debug commands:")
  print("    docker logs auto-remediator --tail 200")
  print("    docker compose exec -T auto-remediator sh -lc 'echo REQUIRE_APPROVAL=$REQUIRE_APPROVAL'")
  sys.exit(1)

action_id = intent.get("action_id")
status = intent.get("status")
target = intent.get("target_service")
print(f"[phase5] observed ACTION_INTENT action_id={action_id} status={status} target={target}")

if not action_id:
  print("[phase5][FAIL] ACTION_INTENT missing action_id:", intent)
  sys.exit(1)

# Wait for PENDING_APPROVAL ACTION_RESULT (from action-executor)
pending = None
deadline = time.time() + 40
while time.time() < deadline and pending is None:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("event_type") != "ACTION_RESULT":
        continue
      if ev.get("action_id") != action_id:
        continue
      if ev.get("status") == "PENDING_APPROVAL":
        pending = ev
        break
    if pending:
      break

if not pending:
  print("[phase5][FAIL] did not observe ACTION_RESULT=PENDING_APPROVAL for action_id")
  print("  If REQUIRE_APPROVAL=true but you never see PENDING_APPROVAL, action-executor may not be enforcing approvals.")
  sys.exit(1)

print(f"[phase5] observed ACTION_RESULT=PENDING_APPROVAL action_id={action_id}")

# Approve (publish ACTION_APPROVAL to actions topic)
approval = {
  "event_type": "ACTION_APPROVAL",
  "action_id": action_id,
  "decision": "APPROVE",
  "approver": "runbookphase5",
  "ts": int(time.time() * 1000),
}
producer.send(ACTIONS_TOPIC, approval)
producer.flush()
print("[phase5] sent approval")

# Wait for terminal result
terminal = None
deadline = time.time() + 50
while time.time() < deadline and terminal is None:
  records = consumer.poll(timeout_ms=1000)
  for _tp, msgs in records.items():
    for msg in msgs:
      ev = msg.value
      if ev.get("event_type") != "ACTION_RESULT":
        continue
      if ev.get("action_id") != action_id:
        continue
      if ev.get("status") in {"EXECUTED", "FAILED", "DENIED", "EXPIRED"}:
        terminal = ev
        break
    if terminal:
      break

if not terminal:
  print("[phase5][FAIL] did not observe terminal ACTION_RESULT after approval")
  sys.exit(1)

print(f"[phase5] terminal_status={terminal.get('status')} message={terminal.get('message')}")

if terminal.get("status") != "EXECUTED":
  print("[phase5][FAIL] expected EXECUTED")
  sys.exit(1)

print("[phase5][PASS] approval-gated action was executed")
PY

  log "[PASS] Phase 5 complete"
}


run_all() {
  phase0_up
  phase1_smoke
  phase2_metrics_anomaly
  phase3_incident_from_logs
  phase4_policy_alias_deny
  phase5_approval_flow
  phase_header "ALL PHASES PASSED"
}

cmd="${1:-all}"
case "$cmd" in
  all)      run_all ;;
  phase0)   phase0_up ;;
  phase1)   phase0_up; phase1_smoke ;;
  phase2)   phase0_up; phase2_metrics_anomaly ;;
  phase3)   phase0_up; phase3_incident_from_logs ;;
  phase4)   phase0_up; phase4_policy_alias_deny ;;
  phase5)   phase0_up; phase5_approval_flow ;;
  *)
    die "Unknown command: $cmd
Usage: ./runbook.sh [all|phase0|phase1|phase2|phase3|phase4|phase5]"
    ;;
esac
