#!/usr/bin/env bash
set -euo pipefail

RESET_AUTO_REMEDIATOR="${RESET_AUTO_REMEDIATOR:-true}"
BROKER="${BROKER:-kafka:9092}"
INC_TOPIC="${INC_TOPIC:-incidents}"
ACT_TOPIC="${ACT_TOPIC:-actions}"

need_running() {
  local svc="$1"
  if ! docker compose ps --status running --services | grep -qx "$svc"; then
    echo "ERROR: $svc is not running"
    docker compose ps
    exit 1
  fi
}

consume_actions_latest_into_file() {
  local out_file="$1"
  local group="rb4-$(date +%s)-$RANDOM"
  # stderr suppressed because Kafka prints TimeoutException on timeout (expected)
  docker compose exec -T kafka bash -lc \
    "kafka-console-consumer \
      --bootstrap-server $BROKER \
      --topic $ACT_TOPIC \
      --consumer-property group.id=$group \
      --consumer-property auto.offset.reset=latest \
      --timeout-ms 30000" >"$out_file" 2>/dev/null || true
}

send_incident() {
  local service="$1"
  local incident_type="$2"
  local test_id="$3"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  docker compose exec -T kafka bash -lc "
    printf '{\"timestamp\":\"%s\",\"service\":\"%s\",\"severity\":\"error\",\"incident_type\":\"%s\",\"source\":\"manual_test\",\"details\":{\"test_id\":\"%s\"}}\\n' \
      \"$ts\" \"$service\" \"$incident_type\" \"$test_id\" \
    | kafka-console-producer --bootstrap-server $BROKER --topic $INC_TOPIC
  "
}

echo "[0/3] Sanity: containers running?"
need_running kafka
need_running auto-remediator
need_running action-executor
docker compose ps

if [[ "$RESET_AUTO_REMEDIATOR" == "true" ]]; then
  echo
  echo "Restarting auto-remediator to clear in-memory cooldown/dedup..."
  docker compose restart auto-remediator >/dev/null
  sleep 2
  need_running auto-remediator
fi

echo
echo "[1/3] Alias incident: payment-service -> payments (should produce ACTION_INTENT)"
ALIAS_ID="alias_payment_service_$(date +%s)"
tmp1="$(mktemp)"
# Start consumer first (from latest), then send
( consume_actions_latest_into_file "$tmp1" ) &
c_pid=$!
sleep 1
send_incident "payment-service" "LATENCY_SPIKE" "$ALIAS_ID"
wait "$c_pid" || true

if grep -q "$ALIAS_ID" "$tmp1"; then
  echo "PASS: alias incident produced an action event"
else
  echo "FAIL: did not find alias test_id in actions"
  echo "Tip: check auto-remediator logs:"
  echo "  docker compose logs --since 2m auto-remediator | egrep 'Service alias normalized|Emitted action intent|Policy rejected'"
  exit 1
fi

echo
echo "[2/3] Deny unknown service: unknown should NOT produce actions"
DENY_ID="deny_unknown_$(date +%s)"
tmp2="$(mktemp)"
( consume_actions_latest_into_file "$tmp2" ) &
c2_pid=$!
sleep 1
send_incident "unknown" "LATENCY_SPIKE" "$DENY_ID"
wait "$c2_pid" || true

if grep -q "$DENY_ID" "$tmp2"; then
  echo "FAIL: deny_unknown produced an action (unexpected)"
  echo "Recent auto-remediator policy logs:"
  docker compose logs --since 2m auto-remediator | egrep "Policy rejected candidate actions|Emitted action intent" || true
  exit 1
else
  echo "PASS: deny_unknown produced no actions"
fi

echo
echo "[3/3] Optional: confirm policy rejection was logged (deny_unknown)"
docker compose logs --since 2m auto-remediator | grep "Policy rejected candidate actions" >/dev/null \
  && echo "OK: policy rejection log present" \
  || echo "NOTE: no policy rejection log found in last 2m (may be log window/timing)."

echo
echo "Phase 4 runbook PASSED."
