#!/usr/bin/env bash
#
# AIOps Infrastructure Runbook
# Automated startup and verification script (Kafka -> Vector -> Loki -> Grafana)
#
# Usage:
#   ./runbook.sh           # start + verify
#   ./runbook.sh --reset           # destructive reset (removes volumes) + start + verify
#   ./runbook.sh --phase4          # start + verify + Phase 4 remediation smoke test
#   ./runbook.sh --reset --phase4  # reset + start + verify + Phase 4 smoke test
#
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# -----------------------------
# Colors
# -----------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}==>${NC} $*"; }
log_success() { echo -e "${GREEN}✓${NC} $*"; }
log_warn()    { echo -e "${YELLOW}!${NC} $*"; }
log_error()   { echo -e "${RED}✗${NC} $*"; }

# -----------------------------
# Args
# -----------------------------
RESET=false
PHASE4=false

for arg in "$@"; do
  case "$arg" in
    --reset) RESET=true ;;
    --phase4) PHASE4=true ;;
  esac
done

# -----------------------------
# Helpers
# -----------------------------
require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "Missing required command: $cmd"
    exit 1
  fi
}

is_container_running() {
  local name="$1"
  docker ps --format "{{.Names}}" | grep -qx "$name"
}

wait_http_200() {
  local url="$1"
  local name="$2"
  local tries="${3:-45}"

  log_info "Waiting for ${name} (${url}) ..."
  for i in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log_success "${name} is ready"
      return 0
    fi
    printf "."
    sleep 1
  done
  echo ""
  log_error "${name} did not become ready in ${tries} seconds"
  return 1
}

wait_kafka_ready() {
  local tries="${1:-60}"

  log_info "Waiting for Kafka to become ready..."
  for i in $(seq 1 "$tries"); do
    if docker exec kafka kafka-broker-api-versions --bootstrap-server kafka:9092 >/dev/null 2>&1; then
      log_success "Kafka is ready"
      return 0
    fi
    printf "."
    sleep 1
  done
  echo ""
  log_error "Kafka did not become ready in ${tries} seconds"
  log_warn "Kafka logs (last 200 lines):"
  docker logs kafka --tail 200 || true
  return 1
}

loki_has_streams_for_aiops() {
  # returns 0 if non-empty, 1 if empty
  local start end resp
  start="$(($(date +%s)-300))000000000"
  end="$(date +%s)000000000"

  resp="$(curl -sG "http://localhost:3100/loki/api/v1/query_range" \
    --data-urlencode 'query={source="aiops"}' \
    --data-urlencode "start=${start}" \
    --data-urlencode "end=${end}" \
    --data-urlencode "limit=5")"

  # If jq exists, do a real JSON check.
  if command -v jq >/dev/null 2>&1; then
    local n
    n="$(echo "$resp" | jq -r '.data.result | length' 2>/dev/null || echo "0")"
    [[ "$n" -gt 0 ]]
    return
  fi

  # Fallback: simple check for empty result array
  if echo "$resp" | grep -q '"result":\[\]'; then
    return 1
  fi
  return 0
}

print_container_table() {
  echo ""
  docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" \
    | egrep "NAME|zookeeper|kafka|loki|vector|grafana" || true
  echo ""
}

# -----------------------------
# Main
# -----------------------------
require_cmd docker
require_cmd curl

cd "$(dirname "$0")"

if [[ "$RESET" == "true" ]]; then
  log_warn "RESET mode enabled: this will remove containers + volumes (Grafana password/dashboards in volume will be deleted)."
  log_info "Stopping and removing stack + volumes..."
  docker compose down -v || true
fi

log_info "Starting AIOps infrastructure stack..."
docker compose up -d

print_container_table

# Verify core containers are up (fast feedback)
for svc in zookeeper kafka loki grafana; do
  if is_container_running "$svc"; then
    log_success "Container running: $svc"
  else
    log_error "Container not running: $svc"
    log_warn "Logs for $svc (last 200 lines):"
    docker logs "$svc" --tail 200 || true
    exit 1
  fi
done

# Kafka readiness
wait_kafka_ready 60

# Loki readiness
if ! wait_http_200 "http://localhost:3100/ready" "Loki" 45; then
  log_warn "Loki logs (last 200 lines):"
  docker logs loki --tail 200 || true
  exit 1
fi

# Vector should be running; if not, try to start it
log_info "Verifying Vector container status..."
if ! is_container_running "vector"; then
  log_warn "Vector is not running. Attempting to start Vector..."
  docker compose up -d vector
  sleep 3
fi

if is_container_running "vector"; then
  log_success "Vector is running"
else
  log_error "Vector failed to start (container is not running)."
  log_warn "Vector logs (last 200 lines):"
  docker logs vector --tail 200 || true
  exit 1
fi

# Optional: show recent Vector errors (helpful when things look empty)
log_info "Checking Vector logs for recent errors/warnings (last 10 minutes)..."
docker logs vector --since 10m | egrep -i "error|warn|kafka|rdkafka|broker|allbrokersdown|unknown|parse|syntax" || true
echo ""

# Produce test log into Kafka
log_info "Producing test log to Kafka (topic: logs_parsed)..."
test_payload='{"message":"runbook smoke test","service":"payments","severity":"error","timestamp":"'"$(date -u +"%Y-%m-%dT%H:%M:%SZ")"'"}'

# Note: kafka-console-producer returns 0 even if leader is still stabilizing sometimes,
# so we will verify via Loki query after.
printf '%s\n' "$test_payload" \
  | docker exec -i kafka kafka-console-producer \
      --bootstrap-server kafka:9092 \
      --topic logs_parsed >/dev/null 2>&1 || {
        log_error "Failed to send test message to Kafka"
        log_warn "Kafka logs (last 200 lines):"
        docker logs kafka --tail 200 || true
        exit 1
      }

log_success "Test message sent to Kafka"
sleep 3

# Query Loki for recent streams
log_info "Querying Loki for recent logs (last 5 minutes) with {source=\"aiops\"}..."
if ! loki_has_streams_for_aiops; then
  log_error "Loki returned no streams for {source=\"aiops\"} in the last 5 minutes."
  log_warn "Common causes:"
  echo "  • Vector is down/crashing (Exited 78) due to config/VRL errors"
  echo "  • Vector cannot reach Kafka or Loki"
  echo "  • Wrong labels/query or logs not produced"
  echo ""
  log_warn "Vector logs (last 200 lines):"
  docker logs vector --tail 200 || true
  echo ""
  log_info "Loki labels available:"
  curl -s "http://localhost:3100/loki/api/v1/labels" || true
  echo ""
  log_info "Loki label values:"
  echo "  source:   $(curl -sG "http://localhost:3100/loki/api/v1/label/source/values" || true)"
  echo "  service:  $(curl -sG "http://localhost:3100/loki/api/v1/label/service/values" || true)"
  echo "  severity: $(curl -sG "http://localhost:3100/loki/api/v1/label/severity/values" || true)"
  echo "  topic:    $(curl -sG "http://localhost:3100/loki/api/v1/label/topic/values" || true)"
  echo ""
  exit 2
fi

log_success "Loki has data for {source=\"aiops\"}"

if $PHASE4; then
  log_info "Running Phase 4 remediation smoke test (alias mapping + reject/audit events)..."
  "${SCRIPT_DIR}/phase4_smoke.sh"
  echo ""
  log_success "Phase 4 smoke test passed"
fi

# Show a few entries if jq exists (best effort)
if command -v jq >/dev/null 2>&1; then
  log_info "Sample log entries (best-effort):"
  start="$(($(date +%s)-300))000000000"
  end="$(date +%s)000000000"
  curl -sG "http://localhost:3100/loki/api/v1/query_range" \
    --data-urlencode 'query={source="aiops"}' \
    --data-urlencode "start=${start}" \
    --data-urlencode "end=${end}" \
    --data-urlencode "limit=3" \
    | jq -r '.data.result[0].values[]? | "\(.[0]): \(.[1])"' || true
  echo ""
fi

# Final report
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_success "Infrastructure stack is operational"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Service URLs:"
echo "  • Grafana:  http://localhost:3000"
echo "  • Loki API: http://localhost:3100"
echo "  • Kafka:    localhost:9092"
echo ""
echo "Notes:"
echo "  • Grafana password persists because /var/lib/grafana is stored in the docker volume."
echo "  • If you run './runbook.sh --reset', Grafana volume is deleted and you'll reset credentials/dashboards."
echo ""
echo "Quick Commands:"
echo "  • View logs:          docker logs <container>"
echo "  • Stop stack:         docker compose stop"
echo "  • Restart stack:      docker compose restart"
echo "  • Full reset:         ./runbook.sh --reset"
echo ""
log_info "Next steps:"
echo "  1. Open Grafana: http://localhost:3000"
echo "  2. Confirm Loki datasource + dashboards load"
echo "  3. Generate more Kafka events and watch panels update"
echo ""
