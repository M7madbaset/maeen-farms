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
| Time series | **Native PostgreSQL range partitioning** (implemented, slice 4); TimescaleDB deferred | TimescaleDB, InfluxDB | Same engine, no extra extension to operate. TimescaleDB could not be validated in my environment, so it is not claimed; revisit for compression/continuous aggregates if sensor volume grows. |
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
| 2 | Irrigation & Water | **Partial** — validation, demand, allocation, plan; **anomaly classification (slice 2)** as least-privilege identity `irrigation_anomaly` |
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

## 5b. Irrigation-anomaly workflow (C) — implemented in slice 2

Uses three redundant flow estimates (well W, main meter M, valve flow F), pressure P and expected flow E.

| Pattern | Classification | Action |
|---|---|---|
| W≈M≈F≈E, P normal | normal | none |
| W≈M, F low, loss ≥ 10 %, P low | leak upstream of valve | alert; **loss ≥ 40 % → autonomous safe-close, verified by telemetry** |
| W≈M, F low, P normal | sensor fault *or* minor leak (ambiguity stated) | alert + inspect |
| M deviates, W≈F | main-meter fault | alert + calibrate |
| W deviates, M≈F | well-sensor fault | alert + calibrate |
| all agree, high flow, P low | downstream break | alert (no autonomous action) |
| all agree, low flow, P low / high | pump-supply / filter-blockage | alert + maintenance recommendation |
| missing/stale/invalid input | BLOCKED | alert; **no classification** |

Safe-close failing verification (valve stuck open) → `ESCALATED_CRITICAL` with a hardware shut-off instruction.
Classifications are hypotheses; a human with role operator/agronomist/owner confirms the outcome, and
`evaluation.py` computes accuracy and false-alarm rate from **confirmed outcomes only**.

## 5c. Data model (slice 4) — `db/migrations/001..007`

PostgreSQL 16 + PostGIS 3.4, 62 application tables, forward-only checksummed migrations (`python -m maeen.pgmigrate`).
Verified by 23 live-database tests and 11 static SQL tests.

* **Provenance is enforced by the schema:** domains `data_origin_t` (raw / derived / model prediction / human-confirmed /
  AI recommendation / configuration) and `quality_status_t`; every observation-like table has `is_simulated NOT NULL`.
  `recommendation.origin` can only be `ai_recommendation`; `harvest_forecast` only `model_prediction`.
* **Integrity rules in the database (defence in depth, not just app code):** missing readings must be recorded as
  `missing` (no invented values); verified results need measurements; fertigation/irrigation plans cannot be
  approved/executing without an approval row; non-safe-state device commands need an approval; real (non-simulated) commands are
  rejected for equipment that is not `commissioned`; simulated data cannot sit in a controlled-mode plan; purchase
  approver ≠ requester and committer ≠ approver; policy activator ≠ author; SOP approver ≠ author; confirmed pest
  diagnoses and anomaly outcomes must name a human; posted costs and confirmed orders must name a human.
* **Audit:** `audit_event` is hash-chained **inside PostgreSQL** (trigger, advisory-lock-serialised, timezone-independent
  hash), blocks UPDATE/DELETE/TRUNCATE, and `verify_audit_chain()` returns the first tampered row. Tested with 4 concurrent
  writers and with a privileged attacker disabling the trigger. External anchoring of the chain head is still not implemented.
* **Geo:** farm boundary, block polygons, palm points, equipment/sensor/zone geometry, all GiST-indexed. One row per palm.
* **Time series:** `sensor_reading` partitioned by month (`ensure_sensor_reading_partition`) with a default partition.
* The running application (slices 1-3) **still uses SQLite**; no repository layer maps the workflows to PostgreSQL yet.

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
| PostgreSQL/PostGIS schema for the full entity list + migrations + live tests | **Implemented (slice 4)**; not yet wired to the application (slices 1-3 use SQLite) |
| Vector store (pgvector) / RAG tables, model registry, retention & backup policy | **Not implemented** |
| REST API, MQTT, event bus, edge gateway, offline sync | **Not implemented** |
| Dashboards, mobile UI | **Not implemented** |
| Workflow C (irrigation anomaly): classification, verified autonomous safe-close, human-confirmed outcomes, evaluation | **Implemented (slice 2), simulated; thresholds unvalidated** |
| Workflow K (daily executive report): JSON + EN/AR text + RTL-safe, HTML-escaped HTML; sample in `docs/sample_reports/` | **Implemented (slice 3)**, covers only data that exists; 6 sections declared 'no data source' |
| Workflows A, D, E, F, G, H, I, J | **Not implemented** |
| Real hardware, controlled mode | **Not possible**: denied unless a commissioned real adapter exists (none) |
| Docker, CI, lint | **Not implemented** |

## 7. Prioritised backlog

1. **Persistence/API (Phase 1):** ~~Postgres/PostGIS schema + migrations~~ — **done in slice 4**. Remaining: repository layer moving workflows from SQLite to PostgreSQL, FastAPI, real authn (OIDC), secrets handling. *Effort: needs sizing by the team.*
2. ~~Workflow C (irrigation anomaly)~~ — **done in slice 2** (rules only; backtesting on real data still required).
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
