"""Negative tests: unauthorized actors/actions must be denied AND audited."""
import pytest
from datetime import timedelta
from maeen.models import DeviceCommand, Approval
from maeen.policy import fingerprint
from maeen.agents.registry import SPECS


def mkcmd(rt, minutes=60.0, wid="wf-t"):
    return DeviceCommand("c1", f"{wid}:V-B1:{minutes}", "V-B1", "run_valve_timed", minutes, 45.0,
                         45.0 * minutes / 60, rt.clock.now() + timedelta(minutes=15), wid, "irrigation_water")


def mkapproval(rt, cmd, user="owner1", ttl=3600):
    fp = fingerprint(cmd.workflow_id, cmd.device_id, cmd.action, cmd.minutes, cmd.expected_flow_m3h)
    return Approval("ap", cmd.workflow_id, user, rt.clock.now(), rt.clock.now() + timedelta(seconds=ttl), (fp,))


def test_agent_cannot_open_valve_without_approval(rt):
    r = rt.gateway.execute_command(mkcmd(rt), "irrigation_water", None)
    assert r.status == "denied" and r.detail["code"] == "approval_required"
    assert rt.sim.open_valve is None


def test_agent_without_tool_grant_cannot_open_valve_even_with_approval(rt):
    cmd = mkcmd(rt)
    r = rt.gateway.execute_command(cmd, "palm_health", mkapproval(rt, cmd))
    assert r.detail["code"] == "tool_not_granted" and rt.sim.open_valve is None


@pytest.mark.parametrize("action", ["modify_safety_limit", "modify_policy", "commit_purchase",
                                    "approve_payment", "expand_agent_permissions"])
def test_prohibited_actions_denied_for_every_agent(rt, action):
    for agent in SPECS:
        assert rt.policy.authorize(agent, action).code == "prohibited"


def test_fertilizer_and_purchase_request_need_grants_and_approval(rt):
    assert rt.policy.authorize("irrigation_water", "apply_fertilizer").code == "tool_not_granted"
    assert rt.policy.authorize("irrigation_water", "create_purchase_request").code == "tool_not_granted"


def test_unknown_action_is_default_deny(rt):
    assert rt.policy.authorize("irrigation_water", "launch_drone").code == "unknown_action"


@pytest.mark.parametrize("user,code", [("op1", "insufficient_role"), ("aud1", "insufficient_role"),
                                       ("ghost", "unknown_user"), ("irrigation_water", "agents_cannot_approve")])
def test_wrong_approvers_rejected(rt, user, code):
    cmd = mkcmd(rt)
    r = rt.gateway.execute_command(cmd, "irrigation_water", mkapproval(rt, cmd, user))
    assert r.status == "denied" and r.detail["code"] == code and rt.sim.open_valve is None


def test_approval_bound_to_exact_command(rt):
    cmd = mkcmd(rt, 60.0)
    ap = mkapproval(rt, cmd)
    tampered = mkcmd(rt, 600.0); tampered.workflow_id = cmd.workflow_id
    r = rt.gateway.execute_command(tampered, "irrigation_water", ap)
    assert r.detail["code"] == "command_not_approved"


def test_expired_approval_and_expired_command(rt):
    cmd = mkcmd(rt); ap = mkapproval(rt, cmd, ttl=1)
    rt.clock.advance(5)
    assert rt.gateway.execute_command(cmd, "irrigation_water", ap).detail["code"] == "approval_expired"
    cmd2 = mkcmd(rt, 30.0); cmd2.expires_at = rt.clock.now() - timedelta(seconds=1)
    assert rt.gateway.execute_command(cmd2, "irrigation_water", mkapproval(rt, cmd2)).detail["code"] == "command_expired"


def test_software_safety_limit_blocks_overlong_run_even_when_approved(rt):
    cmd = mkcmd(rt, 800.0)
    r = rt.gateway.execute_command(cmd, "irrigation_water", mkapproval(rt, cmd))
    assert r.detail["code"] == "safety_limit_minutes" and rt.sim.open_valve is None


def test_denials_are_audited(rt):
    rt.gateway.execute_command(mkcmd(rt), "irrigation_water", None)
    assert any(e["event_type"] == "command_denied" for e in rt.audit.trail())


def test_agent_without_telemetry_grant_cannot_read(rt):
    with pytest.raises(PermissionError):
        rt.gateway.read_sensor("not_an_agent", "well_flow_m3h")


def test_idempotent_command_not_executed_twice(rt):
    cmd = mkcmd(rt, 60.0); ap = mkapproval(rt, cmd)
    r1 = rt.gateway.execute_command(cmd, "irrigation_water", ap)
    v = rt.sim.delivered["V-B1"]
    r2 = rt.gateway.execute_command(cmd, "irrigation_water", ap)
    assert r1.status == "verified" and r2.detail.get("duplicate") and rt.sim.delivered["V-B1"] == v
