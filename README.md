# MAeen FarmOS — vertical slices 1-4 (SIMULATION ONLY)

End-to-end **water-shortage workflow**: validate sensor data → deterministic water budget →
independent review → human approval → policy + safety gateway → simulated valves →
telemetry verification → bilingual alerts → tamper-evident audit trail.

**Not production-ready. All data is simulated. No LLM is used yet. See `docs/ARCHITECTURE.md` §6 for exact status.**

## Prerequisites
Python ≥ 3.10 (stdlib only at runtime), `pytest` for tests.

## Setup & run
```bash
cd maeen
python3 -m venv .venv && source .venv/bin/activate
pip install pytest
export PYTHONPATH=src            # Windows PowerShell: $env:PYTHONPATH="src"

python -m maeen                                   # happy path (40 feddan)
python -m maeen --scenario 142 --well-flow 120    # 142-feddan scenario
python -m maeen --lang ar                         # Arabic output
python -m maeen --approver op1                    # operator denied
python -m maeen --fault valve_stuck               # verification fails -> safe fallback
python -m maeen --fault stale_well                # blocked, no action
python -m maeen --fault meter_mismatch            # contradictory data, blocked
python -m maeen --fault pump_fault                # no well output, blocked
python -m maeen --mode advisory                   # escalates: simulated evidence not allowed outside simulation
```

### Slice 2: irrigation anomaly workflow
```bash
python -m maeen --workflow anomaly --fault leak_severe                    # autonomous verified safe-close
python -m maeen --workflow anomaly --fault severe_leak_valve_stuck_open   # close fails -> critical escalation
python -m maeen --workflow anomaly --fault leak --confirm leak_confirmed  # human confirms (as owner1)
python -m maeen --workflow anomaly --fault flow_sensor_bias --lang ar     # sensor fault, Arabic
python -m maeen --workflow anomaly --fault pump_degraded                  # equipment
python -m maeen --workflow anomaly --fault stale_well                     # blocked, no guess
```

### Slice 3: daily executive report
```bash
python -m maeen --workflow report --out-dir docs/sample_reports --lang ar
```
Builds a simulated day (completed plan, pending plan, severe leak, blocked check) and writes
`daily_report_{en,ar}.{html,txt}` and `daily_report.json`. Sections without a data source
(crop health, stock, spending, workforce, maintenance, forecast) are listed as unavailable, never estimated.

### Slice 4: PostgreSQL/PostGIS data model
```bash
docker compose up -d db                                 # UNTESTED compose file (no Docker in my build sandbox)
pip install "psycopg[binary]" pglast
export MAEEN_DATABASE_URL="host=localhost port=5432 user=maeen password=YOUR_PASSWORD dbname=maeen"
python -m maeen.pgmigrate --seed                        # applies db/migrations/*.sql, then DEV-ONLY seed
```
Run the live database tests (creates and drops a throw-away database):
```bash
MAEEN_TEST_PG_ADMIN_DSN="host=localhost port=5432 user=maeen password=YOUR_PASSWORD dbname=postgres" \
  PYTHONPATH=src python -m pytest -q                    # 121 tests
```
Migrations are forward-only and checksummed; editing an applied migration is rejected. Requires PostgreSQL >= 15 + PostGIS.
CI template with a PostGIS service container: `docs/ci/tests.yml` (copy to `.github/workflows/`).

## Tests
```bash
PYTHONPATH=src python -m pytest -q      # 98 pass, 23 skip unless a PostgreSQL test DSN is set
```

## Troubleshooting
* `ModuleNotFoundError: maeen` → set `PYTHONPATH=src`.
* Arabic looks garbled → use a UTF-8 terminal.
* Demo users (`owner1`, `agro1`, `op1`, `aud1`, `fin1`) are seed data in `config.py`; there is no real login yet.
