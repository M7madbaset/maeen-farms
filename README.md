# MAeen FarmOS — vertical slice 1 (SIMULATION ONLY)

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

## Tests
```bash
PYTHONPATH=src python -m pytest -q      # 59 tests
```

## Troubleshooting
* `ModuleNotFoundError: maeen` → set `PYTHONPATH=src`.
* Arabic looks garbled → use a UTF-8 terminal.
* Demo users (`owner1`, `agro1`, `op1`, `aud1`, `fin1`) are seed data in `config.py`; there is no real login yet.
