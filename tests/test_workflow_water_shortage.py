import pytest
from maeen.runtime import build_runtime
from maeen.orchestrator import Orchestrator
from maeen.agents.registry import validate_output, SchemaError
from maeen.agents.irrigation import IrrigationWaterAgent
from maeen.report import explain


def run(rt):
    o = Orchestrator(rt); return o, o.start_water_shortage("evt-1")


def test_happy_path_end_to_end(rt):
    o, wf = run(rt)
    assert wf["state"] == "AWAITING_APPROVAL" and rt.sim.open_valve is None
    assert sum(rt.sim.delivered.values()) == 0          # nothing moved before approval
    done = o.approve(wf["id"], "owner1")
    assert done["state"] == "COMPLETED"
    assert all(r["verified"] and r["simulated"] for r in done["results"])
    planned = sum(a["planned_volume_m3"] for a in wf["plan"]["proposed_actions"])
    assert sum(rt.sim.delivered.values()) == pytest.approx(planned, rel=0.01)
    assert rt.audit.verify() == (True, None)
    trail = {e["event_type"] for e in rt.audit.trail(wf["id"])}
    assert {"workflow_state", "agent_output", "plan_approved", "device_command_sent", "result_verified"} <= trail


def test_agronomist_can_approve_operator_cannot(rt):
    o, wf = run(rt)
    denied = o.approve(wf["id"], "op1")
    assert "approval denied" in denied["error"] and o.load(wf["id"])["state"] == "AWAITING_APPROVAL"
    assert rt.sim.open_valve is None and sum(rt.sim.delivered.values()) == 0
    assert o.approve(wf["id"], "agro1")["state"] == "COMPLETED"


def test_duplicate_event_does_not_create_second_workflow(rt):
    o, wf = run(rt)
    again = o.start_water_shortage("evt-1")
    assert again["duplicate"] and again["id"] == wf["id"]
    assert rt.db.execute("SELECT COUNT(*) c FROM workflow").fetchone()["c"] == 1


def test_double_approval_does_not_double_irrigate(rt):
    o, wf = run(rt); o.approve(wf["id"], "owner1"); v = dict(rt.sim.delivered)
    assert "error" in o.approve(wf["id"], "owner1") and rt.sim.delivered == v


def test_rejection_path(rt):
    o, wf = run(rt)
    assert o.reject(wf["id"], "owner1", "wait for lab")["state"] == "REJECTED"
    assert sum(rt.sim.delivered.values()) == 0


@pytest.mark.parametrize("setup,status", [
    (lambda s: s.inject("stale_sensor", "well_flow_m3h"), "blocked_insufficient_data"),
    (lambda s: s.inject("offline_sensor", "eto_mm_day"), "blocked_insufficient_data"),
    (lambda s: s.inject("meter_mismatch", "0.5"), "blocked_contradictory_data"),
    (lambda s: s.inject("pump_fault"), "blocked_no_well_output"),
])
def test_bad_data_blocks_planning_and_never_fabricates(setup, status):
    rt = build_runtime("40"); setup(rt.sim)
    o, wf = run(rt)
    assert wf["state"] == "BLOCKED" and wf["plan"]["status"] == status
    assert wf["plan"]["proposed_actions"] == []
    assert rt.sim.open_valve is None and sum(rt.sim.delivered.values()) == 0
    n = rt.db.execute("SELECT * FROM notification").fetchone()
    assert n["severity"] == "high" and n["text_ar"].startswith("[محاكاة]")


def test_no_shortage_means_no_action():
    rt = build_runtime("40", well_flow=150.0)
    o, wf = run(rt)
    assert wf["state"] == "COMPLETED_NO_ACTION"


def test_stuck_valve_triggers_safe_fallback_and_stops_plan():
    rt = build_runtime("40"); rt.sim.inject("valve_stuck_closed", "V-B2")
    o, wf = run(rt); done = o.approve(wf["id"], "owner1")
    assert done["state"] == "FAILED_VERIFICATION"
    assert [r["status"] for r in done["results"]] == ["verified", "failed_verification"]
    assert rt.sim.open_valve is None                       # safe state
    assert rt.sim.delivered["V-B3"] == 0 and rt.sim.delivered["V-B4"] == 0   # plan halted
    assert any(e["event_type"] == "safe_state_close_valve" for e in rt.audit.trail())
    assert rt.db.execute("SELECT severity FROM notification WHERE severity='critical'").fetchone()


def test_non_simulation_modes_escalate_when_evidence_is_simulated():
    """Reviewer invariant: simulated evidence may not drive advisory/controlled workflows."""
    for mode in ("advisory", "controlled"):
        rt = build_runtime("40", mode=mode)
        o, wf = run(rt)
        assert wf["state"] == "ESCALATED"
        assert "simulated evidence" in wf["review"]["data"]["findings"][0]
        assert "error" in o.approve(wf["id"], "owner1")
        assert sum(rt.sim.delivered.values()) == 0 and rt.sim.open_valve is None


def test_gateway_advisory_mode_never_touches_devices():
    from test_security_negative import mkcmd, mkapproval
    rt = build_runtime("40", mode="advisory")
    cmd = mkcmd(rt); r = rt.gateway.execute_command(cmd, "irrigation_water", mkapproval(rt, cmd))
    assert r.status == "advisory_recorded" and not r.verified
    assert not any(e["event_type"] == "device_command_sent" for e in rt.audit.trail())
    assert sum(rt.sim.delivered.values()) == 0


def test_gateway_controlled_mode_denied_without_commissioned_real_hardware():
    from test_security_negative import mkcmd, mkapproval
    rt = build_runtime("40", mode="controlled")
    cmd = mkcmd(rt); r = rt.gateway.execute_command(cmd, "irrigation_water", mkapproval(rt, cmd))
    assert r.status == "denied" and r.detail["code"] == "not_commissioned"
    assert sum(rt.sim.delivered.values()) == 0


def test_142_scenario_unverified_allocation_not_used_as_cap_but_warned():
    rt = build_runtime("142", well_flow=120.0)
    o, wf = run(rt)
    assert wf["state"] == "AWAITING_APPROVAL"
    assert wf["plan"]["data"]["available_m3"] == pytest.approx(120 * 20)   # measured, not 1800
    assert any("UNVERIFIED" in w for w in wf["plan"]["warnings"])


def test_142_verified_allocation_caps_availability():
    rt = build_runtime("142", well_flow=120.0); rt.cfg.allocation_verified = True
    o, wf = run(rt)
    assert wf["plan"]["data"]["available_m3"] == 1800


def test_agent_output_schema_validation_and_contract(rt):
    out = IrrigationWaterAgent().run(rt, "cid")
    validate_output(out)
    bad = dict(out); bad.pop("evidence")
    with pytest.raises(SchemaError): validate_output(bad)
    bad = dict(out); bad["confidence"] = 7.0
    with pytest.raises(SchemaError): validate_output(bad)
    assert out["agent_id"] == "irrigation_water" and out["correlation_id"] == "cid"


def test_reviewer_rejects_inconsistent_plan(rt):
    from maeen.agents.farm_manager import FarmManagerAgent
    plan = IrrigationWaterAgent().run(rt, "c")
    plan["data"]["allocation_m3"]["B1"] += 5000          # corrupt plan
    out = FarmManagerAgent().review(rt, "c", plan)
    assert out["data"]["verdict"] == "reject" and out["data"]["findings"]


def test_arabic_and_english_explanations_contain_all_seven_parts(rt):
    o, wf = run(rt); done = o.approve(wf["id"], "owner1")
    en, ar = explain(o.load(wf["id"]), "en"), explain(o.load(wf["id"]), "ar")
    for i in range(1, 8):
        assert f"{i})" in en and f"{i})" in ar
    assert "المتاح" in ar and "900" in ar            # Arabic text with intact Western digits
