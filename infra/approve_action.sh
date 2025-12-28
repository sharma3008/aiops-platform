#!/usr/bin/env bash
set -euo pipefail

# Simple helper to approve/deny an action via Kafka.
#
# Usage:
#   cd infra
#   ./approve_action.sh <action_id> [APPROVE|DENY] [approver_name]

cd "$(dirname "$0")"

ACTION_ID="${1:-}"
DECISION="${2:-APPROVE}"
APPROVER="${3:-manual}"

if [[ -z "$ACTION_ID" ]]; then
  echo "Usage: $0 <action_id> [APPROVE|DENY] [approver_name]" >&2
  exit 2
fi

payload=$(python3 - <<PY
import json, time
print(json.dumps({
  "event_type": "ACTION_APPROVAL",
  "action_id": "$ACTION_ID",
  "decision": "$DECISION",
  "approver": "$APPROVER",
  "ts": int(time.time())
}))
PY
)

echo "$payload" | docker compose exec -T kafka bash -lc \
  'kafka-console-producer --bootstrap-server kafka:9092 --topic actions > /dev/null'

echo "Sent ACTION_APPROVAL: action_id=$ACTION_ID decision=$DECISION approver=$APPROVER"
