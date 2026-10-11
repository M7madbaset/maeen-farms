"""Agent contracts. Slice 1 implements 2 of 16 agents; the rest are specified in docs only."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    version: str
    objective: str
    tools: frozenset
    data_scopes: frozenset
    timeout_s: int = 30          # declared in contract; NOT enforced in slice 1
    max_retries: int = 1


SPECS = {
    "irrigation_water": AgentSpec(
        "irrigation_water", "0.1.0",
        "Validate water data, compute demand/allocation, propose an irrigation plan.",
        frozenset({"read_telemetry", "create_recommendation", "request_valve_command"}),
        frozenset({"telemetry", "farm_config"})),
    "farm_manager": AgentSpec(
        "farm_manager", "0.1.0",
        "Independently review high-impact plans against invariants.",
        frozenset({"read_telemetry", "create_recommendation", "review_plan"}),
        frozenset({"telemetry", "farm_config"})),
    "palm_health": AgentSpec(      # contract only: used by negative tests
        "palm_health", "0.0.1", "Not implemented in slice 1.",
        frozenset({"read_telemetry", "create_recommendation"}), frozenset({"observations"})),
}

OUTPUT_SCHEMA = {
    "agent_id": str, "version": str, "status": str, "summary": str, "data": dict,
    "confidence": float, "evidence": list, "proposed_actions": list,
    "risk_category": str, "requires_approval": bool, "correlation_id": str,
    "assumptions": list, "warnings": list,
}


class SchemaError(ValueError):
    pass


def validate_output(d: dict) -> dict:
    for k, t in OUTPUT_SCHEMA.items():
        if k not in d:
            raise SchemaError(f"missing field '{k}'")
        if not isinstance(d[k], t):
            raise SchemaError(f"field '{k}' must be {t.__name__}")
    if not 0.0 <= d["confidence"] <= 1.0:
        raise SchemaError("confidence out of range")
    if d["risk_category"] not in ("autonomous", "approval_required", "prohibited"):
        raise SchemaError("invalid risk_category")
    return d
