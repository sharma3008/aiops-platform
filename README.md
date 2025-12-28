# AIOps Platform — Infrastructure & Observability Stack

A **production-style local infrastructure stack** for demonstrating an end-to-end **AIOps platform**, including observability, ML-driven anomaly detection, incident classification, and automated remediation workflows.

This repository focuses on the **infra + observability layer**, while also running containerized AIOps services on top of Kafka.

---

## ✨ What This Repo Provides

- Full local **observability pipeline** using Kafka, Vector, Loki, and Grafana
- Event-driven backbone for logs, metrics, incidents, ML outputs, and actions
- Containerized Python services for the complete AIOps lifecycle
- Deterministic **runbook** to validate the entire system end-to-end
- Grafana dashboards (UI + Git-provisioned) for demos and interviews

This setup mirrors how **real AIOps platforms** are built and tested.

---

## AIOps Capabilities Included

Containerized Python services implement:

- Log generation → parsing
- Metrics generation → enrichment
- ML anomaly detection (Isolation Forest)
- Incident classification (error bursts, anomaly signals)
- Policy-based auto-remediation (intent emission + approvals)

Kafka topics act as **contracts** between all services.

---

## High-Level Architecture

```
Kafka Topics → Vector (VRL normalization) → Loki → Grafana Dashboards
```

### Core Components

- **Kafka** – Event bus for logs, metrics, incidents, ML outputs, and actions  
- **Vector** – Consumes Kafka JSON events, normalizes fields, ships to Loki  
- **Loki** – Label-indexed log storage  
- **Grafana** – Dashboards for observability and AIOps decision visibility  

---

## Repository Structure

```
aiops-platform/
└── infra/
    ├── docker-compose.yml
    ├── runbook.sh                    # Automated startup & verification
    ├── loki/
    │   ├── loki-config.yaml
    │   └── data/                     # Persisted Loki data
    ├── vector/
    │   └── vector.toml               # Kafka → VRL → Loki pipeline
    └── grafana/
        ├── provisioning/
        │   ├── datasources/
        │   │   └── datasources.yaml  # Loki datasource
        │   └── dashboards/
        │       └── dashboards.yaml   # Dashboard provider
        └── dashboards/               # Versioned dashboard JSON files
```

---

##  Prerequisites

- Docker Desktop (running)
- `docker compose` CLI
- Optional: `jq` for JSON inspection

---

##  Start the Full Stack

```bash
cd aiops-platform/infra
docker compose up -d
```

### Run services outside Docker (optional)

```bash
export KAFKA_BROKER=localhost:29092
```

---

##  Verify Containers

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" \
  | egrep "zookeeper|kafka|loki|vector|grafana"
```

---

##  Access Services

- **Grafana**: http://localhost:3000  
  - Default login: `admin / admin`
- **Loki API**: http://localhost:3100

---

## Smoke Test (End-to-End)

### Produce a Test Log

```bash
printf '%s\n' '{"message":"demo test","service":"payments","severity":"error"}' \
  | docker exec -i kafka kafka-console-producer \
      --bootstrap-server kafka:9092 \
      --topic logs_parsed
```

### Verify Loki Labels

```bash
curl -s http://localhost:3100/loki/api/v1/labels | jq
curl -s http://localhost:3100/loki/api/v1/label/source/values | jq
```

Expected labels:
- `source=aiops`
- `service`
- `severity`
- `topic`

###  Query Recent Logs

```bash
start="$(($(date +%s)-300))000000000"
end="$(date +%s)000000000"

curl -sG http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={source="aiops"}' \
  --data-urlencode "start=${start}" \
  --data-urlencode "end=${end}" \
  --data-urlencode "limit=20" | jq
```

---

##  Grafana Dashboards

### Included Panels

- Live Error Logs
- Errors by Service
- Incidents Feed (Kafka incidents topic)
- Log Severity Distribution
- Log Volume (per minute)

These form the **Operations Overview**.

Additional dashboards can visualize:
- ML anomalies
- Action intents (APPROVED / PROPOSED / DENIED)
- Pending approvals
- Action execution results

---

## Dashboard Persistence

| Dashboard Type | Storage | Persistence |
|---|---|---|
| UI-created | `grafana-storage` volume | Survives restarts |
| Git-provisioned | `infra/grafana/dashboards/*.json` | Version controlled |

### Export Dashboards to Git

**Recommended method**
1. Dashboard → ⚙️ Settings → **JSON Model**
2. Save to `infra/grafana/dashboards/`
3. Restart Grafana

---

## Runbook (Recommended)

Use this after restarts or extended downtime.

```bash
cd aiops-platform/infra
chmod +x runbook.sh
./runbook.sh
```

The runbook:
- Starts all services
- Waits for Loki readiness
- Verifies Vector ingestion
- Pushes test events
- Confirms Loki queries succeed
- Validates the AIOps pipeline

---

##  Stopping Services

### Pause (keep state)

```bash
docker compose stop
```

### Remove containers (keep data)

```bash
docker compose down
```

### Full reset (⚠ deletes dashboards & Grafana DB)

```bash
docker compose down -v
```

---

##  Loki Label Schema

Vector attaches:

- `source="aiops"`
- `service`
- `severity`
- `topic`

**Example queries**
```logql
{source="aiops"}
{source="aiops", service="payments"}
{source="aiops", severity="error"}
```

---

##  Docker Services

```yaml
zookeeper   # Kafka coordination
kafka       # Event bus
vector      # Log processing pipeline
loki        # Log aggregation
grafana     # Visualization
```

---

##  Troubleshooting (Quick)

- **Grafana shows no data** → Check time range + Vector logs  
- **Vector exits (code 78)** → VRL syntax error  
- **Loki labels but no streams** → Produce new test log  
- **Kafka decode errors** → Ensure valid JSON  

```bash
docker logs vector --tail 200
docker logs loki --tail 200
```

---

## Why This Matters

This repo demonstrates:
- Event-driven infrastructure design
- Observability pipelines used in production
- AIOps-ready data contracts
- Safe, repeatable demo environments

It is designed for **learning, demos, interviews, and further production hardening**.

---

## Tip for Reviewers
Run `./runbook.sh`, open Grafana, and watch the entire AIOps lifecycle in real time.
