# MAeen FarmOS — Architecture, Decisions, Backlog (v0.1)

> Status: **slice 1 only**. Everything marked "target" is design, not code. All data in the
> running slice is **simulated**. Nothing here is production-ready.

## 1. Layered architecture (target)

```
 UI (web + mobile PWA, AR/EN, approval inbox)
   │ REST/WebSocket
 Orchestrator + durable workflow engine ── Agent runtime (16 agents, JSON-schema contracts)
   │                                          │ every tool call
 Governance: Policy engine · RBAC · Tool Gateway · Safety layer · Approvals · Audit chain
   │ commands only via gateway (default-deny)           ▲ telemetry/events
 Integration: REST · MQTT · event bus · adapters (equipment, accounting, SMS/WhatsApp, weather)
   │
 Farm-edge gateway (offline-first; store-and-forward; local rules; local audit buffer)
   │  ── network-segmented from AI services ──
 Physical: wells/meters/pressure/soil/weather · pump controllers · valves · HARDWARE interlocks
```
Principles: LLMs propose and explain; **deterministic code calculates, validates and enforces**;
hardware protects itself (dry-run, pressure, e-stop, fail-closed valves) with no AI in the loop.

## 2. Technology selection (target) and trade-offs

| Concern | Choice | Alternatives considered | Why / cost of ownership |
|---|---|---|---|
| Language/API | Python 3.12 + FastAPI | Node/NestJS, Go | Best agronomy/ML/LLM ecosystem; one team skill set. Slower raw throughput — irrelevant at farm scale. |
| Operational DB | PostgreSQL 16 + PostGIS | MySQL, MongoDB | Relational integrity for approvals/finance + geospatial blocks/palms. Needs backups/DBA habits. |
| Time series | TimescaleDB (extension on same PG) | InfluxDB, plain PG | One engine to operate/back up; continuous aggregates. Revisit if >10⁴ sensors. |
| Workflows | Temporal (durable execution) | Celery, LangGraph-only, Airflow | Retries, timers, human-approval signals, replay. Heavier to run; worth it for physical ops. LangGraph may live *inside* agent steps. |
| Messaging | MQTT (Mosquitto/EMQX) at edge + NATS or Postgres-outbox centrally | Kafka, RabbitMQ | Low-bandwidth, intermittent links. Kafka is overkill here. |
| Objects | MinIO (S3 API) | Cloud S3 only | Works on the farm LAN; sync to cloud. |
| RAG | pgvector in the same PG | Qdrant, Chroma | Fewer moving parts; approved-document corpus is small. |
| Policy | In-process deterministic engine now; OPA/Cedar later | LLM-judged policy (rejected) | Auditable, testable, versioned. |
| UI | React + i18n, RTL-first, PWA | Flutter, server-rendered | Offline-capable field UI; one codebase. |
| Deploy | Docker Compose on farm edge server; managed cloud for central | k8s everywhere | k8s is too much ops for one farm. |
| LLM | Provider-agnostic interface, structured output, versioned prompts | Single-vendor lock-in | Swap models; cost budgets/rate limits per agent. |

## 3. Agent catalog (16 + orchestrator)

| # | Agent | Slice-1 status |
|---|---|---|
| 0 | Farm Orchestrator | **Partial** — water-shortage state machine only; no generic planner |
| 1 | Farm Manager / Decision Intelligence | **Partial** — independent plan reviewer only |
| 2 | Irrigation & Water | **Partial** — validation, demand, allocation, plan (deterministic); no leak/anomaly detection |
| 3 | Soil & Fertigation · 4 Palm Health · 5 Agronomy · 6 Yield · 7 Workforce · 8 Equipment · 9 Procurement · 10 Harvest/Traceability · 11 Finance · 12 Sales · 13 Compliance · 14 Risk · 15 Knowledge/Reporting | **Not implemented** (contract stub for Palm Health used in negative tests) |

Common contract (implemented): agent_id, version, objective, tools, data scopes, timeout/retry,
output JSON schema (confidence, evidence, proposed actions, risk category, approval flag,
correlation ID, assumptions, warnings). Agents cannot grant themselves tools; grants live in the registry.

## 4. Action risk matrix (implemented in `policy.py`)

| Class | Actions | Rule |
|---|---|---|
| Autonomous | read telemetry, create recommendation, review plan, notify, **close valve to safe state** | allowed if tool granted; audited |
| Approval required | timed valve run, fertilizer application, purchase *request* | named human role (owner/agronomist; finance for purchases); approval bound to exact command fingerprint; expires; agents cannot approve |
| Prohibited for agents | commit purchase, approve payment, modify policy, modify safety limits, expand permissions | always denied, audited |
| Default | any unknown action | denied |

## 5. Water-shortage workflow (B) — implemented

```
DATA_VALIDATION ─► BLOCKED (missing/stale/contradictory/no-output → alert, NO action)
      │ ok
      ├─► COMPLETED_NO_ACTION (no shortage)
      ▼
UNDER_REVIEW ─► ESCALATED (independent reviewer rejects)
      ▼
AWAITING_APPROVAL ─► REJECTED
      │ approve (owner|agronomist)
      ▼
EXECUTING ─► FAILED_VERIFICATION (flow/volume mismatch → close valve, stop plan, critical alert)
      ▼
COMPLETED  (verified vs telemetry)   |  COMPLETED_ADVISORY (human acts; outcome pending)
```
Sensor rules: stale (>`max_data_age_s`), missing, out-of-range, and well-vs-meter contradiction
all block planning; values are never substituted.

## 6. Implementation status

| Capability | Status |
|---|---|
| Orchestrator, durable workflow rows (SQLite), duplicate-event protection | Implemented (water shortage only) |
| Policy engine, RBAC, tool gateway, approvals bound to commands, idempotency | Implemented |
| Safety layer (software limits) + simulated hardware interlocks | Implemented (simulated) |
| Hash-chained append-only audit + DB triggers | Implemented (chain head not externally anchored) |
| Sensor/actuator simulator with fault injection | Implemented (simulated) |
| Water demand/allocation maths; 40 & 142 feddan scenarios | Implemented, **parameters unvalidated** |
| AR/EN notifications and 7-part explanation | Implemented (templates) |
| Agent timeouts | Declared, **not enforced** |
| LLM integration / RAG / prompts | **Not implemented** (slice is fully deterministic) |
| Authentication (real), secrets, TLS | **Not implemented** (CLI trusts user id) |
| PostgreSQL/PostGIS/Timescale schema (full model §6), migrations | **Not implemented** (minimal SQLite tables only) |
| REST API, MQTT, event bus, edge gateway, offline sync | **Not implemented** |
| Dashboards, mobile UI | **Not implemented** |
| Workflows A, C–K | **Not implemented** |
| Real hardware, controlled mode | **Not possible**: denied unless a commissioned real adapter exists (none) |
| Docker, CI, lint | **Not implemented** |

## 7. Prioritised backlog

1. **Persistence/API (Phase 1):** Postgres+PostGIS+Timescale schema & Alembic migrations (full entity list), FastAPI, real authn (OIDC), secrets handling. *Effort: needs sizing by the team.*
2. **Workflow C (irrigation anomaly)** — reuses validation + simulator; add leak vs sensor-fault classifier with backtests.
3. **Workflow A (daily plan)** + read-only dashboard (AR/EN, RTL).
4. **Temporal** migration of the orchestrator; enforce agent timeouts; LLM client interface with schema-constrained output and prompt-injection tests on untrusted inputs.
5. Palm Health agent + case tracking (D), Knowledge/RAG with approved-SOP gating.
6. Field workflows (work orders, maintenance, inventory) and approval inbox.
7. Edge gateway (MQTT, store-and-forward), then Phase 5 commissioning of **one** zone.

## 8. Required before any real use (not done)

* Agronomist validation of Kc, leaching, efficiency, priorities, floors; measured well output,
  water analysis and meter calibration; confirmation of the 1,800 m³/day allocation.
* Hardware: independent dry-run/pressure/e-stop interlocks commissioned and tested without software.
* Security review, penetration test, backup/restore drill, hardware-in-the-loop tests, field trial in advisory mode first.
