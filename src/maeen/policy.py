"""Independent, deterministic policy engine (no LLM). Default-deny."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    risk: str                       # autonomous | approval_required | prohibited
    approver_roles: frozenset = frozenset()


ACTIONS = {
    "read_telemetry": Rule("autonomous"),
    "create_recommendation": Rule("autonomous"),
    "review_plan": Rule("autonomous"),
    "send_notification": Rule("autonomous"),
    "close_valve_safe_state": Rule("autonomous"),   # only moves toward safe state
    "request_valve_command": Rule("approval_required", frozenset({"owner", "agronomist"})),
    "apply_fertilizer": Rule("approval_required", frozenset({"agronomist", "owner"})),
    "create_purchase_request": Rule("approval_required", frozenset({"owner", "finance_officer"})),
    # never available to agents, regardless of approval:
    "commit_purchase": Rule("prohibited"),
    "approve_payment": Rule("prohibited"),
    "modify_policy": Rule("prohibited"),
    "modify_safety_limit": Rule("prohibited"),
    "expand_agent_permissions": Rule("prohibited"),
}


@dataclass
class Decision:
    allowed: bool
    code: str
    reason: str = ""


def fingerprint(workflow_id: str, device_id: str, action: str, minutes: float, flow: float) -> str:
    raw = json.dumps([workflow_id, device_id, action, round(minutes, 3), round(flow, 3)])
    return hashlib.sha256(raw.encode()).hexdigest()


class Policy:
    def __init__(self, registry: dict, users: dict, clock):
        self.registry, self.users, self.clock = registry, users, clock

    def validate_approver(self, user_id: str, action: str) -> Decision:
        rule = ACTIONS.get(action)
        if rule is None or rule.risk != "approval_required":
            return Decision(False, "not_approvable", f"'{action}' cannot be approved")
        if user_id in self.registry or user_id.startswith("agent:"):
            return Decision(False, "agents_cannot_approve", "agents may not approve actions")
        roles = self.users.get(user_id)
        if roles is None:
            return Decision(False, "unknown_user", f"unknown user '{user_id}'")
        if not (set(roles) & rule.approver_roles):
            return Decision(False, "insufficient_role",
                            f"roles {sorted(roles)} cannot approve '{action}'")
        return Decision(True, "ok")

    def authorize(self, agent_id: str, action: str, approval=None, fp: str | None = None,
                  at=None) -> Decision:
        at = at or self.clock.now()
        rule = ACTIONS.get(action)
        if rule is None:
            return Decision(False, "unknown_action", f"'{action}' is not a defined action")
        if rule.risk == "prohibited":
            return Decision(False, "prohibited", f"'{action}' is prohibited for agents")
        spec = self.registry.get(agent_id)
        if spec is None:
            return Decision(False, "unknown_agent", f"unknown agent '{agent_id}'")
        if action not in spec.tools:
            return Decision(False, "tool_not_granted", f"{agent_id} has no grant for '{action}'")
        if rule.risk == "autonomous":
            return Decision(True, "ok")
        if approval is None:
            return Decision(False, "approval_required", f"'{action}' needs human approval")
        v = self.validate_approver(approval.approver_user_id, action)
        if not v.allowed:
            return v
        if approval.expires_at < at:
            return Decision(False, "approval_expired", "approval has expired")
        if fp is None or fp not in approval.fingerprints:
            return Decision(False, "command_not_approved", "command differs from what was approved")
        return Decision(True, "ok")
