# AIOps Platform — Infrastructure Stack

Complete local observability pipeline for AIOps demonstration using Kafka, Vector, Loki, and Grafana.

## 🏗️ Architecture

```
Kafka Topics → Vector (VRL normalize) → Loki → Grafana Dashboards
```

**Components:**
- **Kafka**: Event bus for logs, incidents, metrics, ML outputs, and actions
- **Vector**: Consumes Kafka JSON messages, normalizes fields, ships to Loki
- **Loki**: Log aggregation system with label-based indexing
- **Grafana**: Visualization dashboards and panels

## 📁 Repository Structure

```
aiops-platform/
└── infra/
    ├── docker-compose.yml
    ├── runbook.sh                    # Automated startup & verification
    ├── loki/
    │   ├── loki-config.yaml
    │   └── data/                     # Persisted Loki data (bind mount)
    ├── vector/
    │   └── vector.toml               # Kafka sources + VRL transforms + Loki sink
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   └── datasources.yaml  # Loki datasource config
        │   └── dashboards/
        │       └── dashboards.yaml   # Dashboard provider config
        └── dashboards/               # Exported dashboard JSON files
```


### Prerequisites

- Docker Desktop (running)
- `docker compose` CLI available
- Optional: `jq` for JSON formatting

### Start the Stack

```bash
cd aiops-platform/infra
docker compose up -d
```

### Verify Running Containers

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | egrep "zookeeper|kafka|loki|vector|grafana"
```

### Access Services

- **Grafana**: http://localhost:3000 (default: admin/admin)
- **Loki API**: http://localhost:3100

## Smoke Test (End-to-End Verification)

### 1. Produce Test Message to Kafka

```bash
printf '%s\n' '{"message":"demo test","service":"payments","severity":"error"}' \
  | docker exec -i kafka kafka-console-producer \
      --bootstrap-server kafka:9092 \
      --topic logs_parsed
```

### 2. Verify Loki Labels

```bash
curl -s http://localhost:3100/loki/api/v1/labels | jq
curl -sG http://localhost:3100/loki/api/v1/label/source/values | jq
```

Expected labels:
- `source` (should include "aiops")
- `service`
- `severity`
- `topic`

### 3. Query Recent Logs

```bash
start="$(($(date +%s)-300))000000000"
end="$(date +%s)000000000"

curl -sG "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={source="aiops"}' \
  --data-urlencode "start=${start}" \
  --data-urlencode "end=${end}" \
  --data-urlencode "limit=20" | jq
```

## Grafana Dashboards

### Included Dashboards

1. **Errors by Service** - Service-level error breakdown
2. **Incidents Topic Only** - Incident stream monitoring
3. **Live Error Logs** - Real-time error log viewer
4. **Log Severity Distribution** - Severity level analytics
5. **Log Volume (per minute)** - Traffic patterns

### Dashboard Persistence

**Grafana Password:**
- Stored in Docker volume `grafana-storage:/var/lib/grafana`
- Persists across restarts (unless volume is deleted with `-v` flag)

**Dashboard Storage:**

| Type | Location | Persistence |
|------|----------|-------------|
| UI-created | `grafana-storage` volume | Survives restarts & `docker compose down` |
| Git-provisioned | `infra/grafana/dashboards/*.json` | Version-controlled, auto-loaded on startup |

**Recommendation:** Use Git provisioning for repeatable demos across machines.

### Exporting Dashboards to Git

**Option A: JSON Model (Recommended)**

1. Open dashboard in Grafana
2. Click gear icon (⚙️ Dashboard settings)
3. Select **JSON Model** tab
4. Click **Save JSON to file**
5. Save to `infra/grafana/dashboards/<dashboard-name>.json`

**Option B: Share → Export**

1. Open dashboard
2. Click **Share** button
3. Go to **Export** tab
4. **Save to file**
5. Place in `infra/grafana/dashboards/`



## 🔄 Bring-It-Back Runbook

Use this after laptop restarts, Docker downtime, or 4-5 days of inactivity.

### Automated Method (Recommended)

```bash
cd aiops-platform/infra
chmod +x runbook.sh
./runbook.sh
```

The script will:
- Start all containers
- Wait for Loki readiness
- Verify Vector is running
- Push test message to Kafka
- Query Loki to confirm data flow
- Display service URLs

### Manual Method

```bash
cd aiops-platform/infra
docker compose up -d
docker ps --format "table {{.Names}}\t{{.Status}}" | egrep "zookeeper|kafka|loki|vector|grafana"

# Then run smoke test (see above)
```

## Stopping Services

### Pause (Keeps State)

```bash
docker compose stop
```

Resume with: `docker compose start` or `./runbook.sh`

### Remove Containers (Keeps Volumes)

```bash
docker compose down
```

### Complete Wipe (Including Data)

 **Warning:** Deletes Grafana password and all stored dashboards

```bash
docker compose down -v
```

## Troubleshooting

### Grafana Shows "No Data"

**Checklist:**

1. **Verify Vector is running:**
   ```bash
   docker ps | grep vector
   ```

2. **Check Vector logs if exited:**
   ```bash
   docker logs vector --tail 200
   ```

3. **Test Loki directly:**
   - Set Grafana time range to "Last 1 hour"
   - Run manual Loki query (see Smoke Test #3)

### Loki Has Labels But No Streams

**Cause:** Loki is healthy but hasn't received recent data.

**Fix:**
1. Produce new test message to Kafka (see Smoke Test #1)
2. Verify Vector is running and configured correctly
3. Check Vector logs for connection issues

### Vector Exits with Code 78

**Cause:** Configuration syntax error (usually VRL)

**Fix:**
```bash
# Check logs
docker logs vector --tail 200

# Fix infra/vector/vector.toml
# Then restart
docker compose up -d vector
```

### Kafka JSON Decode Errors

**Cause:** Vector expects valid JSON (configured with `decoding.codec = "json"`)

**Fix:** Ensure all messages are valid JSON:
```bash
# Correct format
printf '%s\n' '{"message":"test","service":"api","severity":"info"}' \
  | docker exec -i kafka kafka-console-producer \
      --bootstrap-server kafka:9092 \
      --topic logs_parsed
```

### Vector Can't Reach Loki

**Symptoms:**
- Vector logs show connection errors
- Loki queries return empty results
- Vector container restarts frequently

**Fix:**
```bash
# Check Loki is ready
curl http://localhost:3100/ready

# Verify network connectivity
docker network inspect infra_default

# Restart Vector
docker compose restart vector
```

## 📋 Loki Label Schema

Vector attaches these labels (configured in `vector.toml`):

- `source="aiops"` - Constant label for demo
- `service` - Service name from message
- `severity` - Log severity level
- `topic` - Source Kafka topic

**Query Pattern:**
```
{source="aiops"}
{source="aiops", service="payments"}
{source="aiops", severity="error"}
```

## 🐳 Docker Compose Services

```yaml
services:
  zookeeper       # Kafka coordination
  kafka           # Message broker (port 9092)
  loki            # Log aggregation (port 3100)
  vector          # Log pipeline processor
  grafana         # Visualization (port 3000)

volumes:
  grafana-storage # Persists Grafana DB, dashboards, settings
```

## Configuration Files

### Loki Config
- **Location:** `infra/loki/loki-config.yaml`
- **Data:** `infra/loki/data/` (bind mount)

### Vector Config
- **Location:** `infra/vector/vector.toml`
- **Sources:** Kafka topics
- **Transforms:** VRL normalization
- **Sink:** Loki HTTP endpoint

### Grafana Provisioning
- **Datasource:** `infra/grafana/provisioning/datasources/datasources.yaml`
- **Dashboards:** `infra/grafana/provisioning/dashboards/dashboards.yaml`
- **Dashboard Files:** `infra/grafana/dashboards/*.json`

## Tips

- **Kafka Topics:** Auto-created on first use (logs_parsed, incidents, metrics_enriched, ml_output, actions)
- **Time Zones:** Use Grafana's time range picker; Loki stores nanosecond timestamps
- **Performance:** For high volume, adjust Loki retention settings in `loki-config.yaml`
- **Debugging:** Use `docker logs <container>` for all troubleshooting
- **Fresh Start:** Run `./runbook.sh` after any extended downtime

## 🔗 Useful Links

- [Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Vector Documentation](https://vector.dev/docs/)
- [Kafka Documentation](https://kafka.apache.org/documentation/)
- [Grafana Provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)

##  Support

If issues persist after troubleshooting:
1. Collect logs: `docker logs <service> > <service>.log`
2. Check container status: `docker ps -a`
3. Verify disk space: `docker system df`
4. Review recent changes to config files