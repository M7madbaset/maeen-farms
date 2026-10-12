import pytest
from maeen.runtime import build_runtime
from maeen.orchestrator import Orchestrator
from maeen.evaluation import evaluate_anomaly_cases
from maeen.agents.registry import validate_output
from maeen.report import explain_anomaly

V = "V-B1"


def setup(fault=None, mode="simulation", **kw):
    rt = build_runtime("40", mode=mode, **kw)
    rt.sim.open_valve = V                      # irrigation run in progress (simulated)
    if fault:
        for f, t in fault: rt.sim.inject(f, t)
    return rt, Orchestrator(rt)


def go(rt, o, eid="e1"):
    return o.start_irrigation_anomaly(eid, V, 45.0)


def test_normal_operation_no_action():
    rt, o = setup(); wf = go(rt, o)
    assert wf["state"] == "COMPLETED_NO_ACTION" and wf["plan"]["data"]["classification"] == "normal"
    assert rt.sim.open_valve == V                 # nothing touched
    assert not rt.db.execute("SELECT 1 FROM notification").fetchone()


def test_severe_leak_triggers_verified_autonomous_safe_close():
    rt, o = setup([("leak", "25")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "leak_upstream_of_valve"
    assert wf["plan"]["data"]["severity"] == "severe"
    assert wf["state"] == "AWAITING_INSPECTION" and wf["safe_state"]["verified"]
    assert rt.sim.open_valve is None
    ev = {e["event_type"] for e in rt.audit.trail()}
    assert {"device_command_sent", "safe_close_verified"} <= ev
    assert rt.db.execute("SELECT 1 FROM notification WHERE severity='critical'").fetchone()


def test_valve_stuck_open_escalates_critical_never_reports_success():
    rt, o = setup([("leak", "25"), ("valve_stuck_open", V)]); wf = go(rt, o)
    assert wf["state"] == "ESCALATED_CRITICAL" and wf["safe_state"]["status"] == "failed_verification"
    assert not wf["safe_state"]["verified"]
    n = rt.db.execute("SELECT text_en,text_ar FROM notification WHERE text_en LIKE '%did NOT close%'").fetchone()
    assert n and "المحبس" in n["text_ar"]
    assert any(e["event_type"] == "safe_close_FAILED" for e in rt.audit.trail())


def test_moderate_leak_alerts_but_does_not_act_autonomously():
    rt, o = setup([("leak", "15")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "leak_upstream_of_valve"
    assert wf["plan"]["data"]["severity"] == "high" and wf["plan"]["proposed_actions"] == []
    assert wf["state"] == "AWAITING_INSPECTION" and "safe_state" not in wf and rt.sim.open_valve == V


def test_minor_leak_is_reported_as_ambiguous_not_overclaimed():
    rt, o = setup([("leak", "8")]); wf = go(rt, o)
    d = wf["plan"]["data"]
    assert d["classification"] == "sensor_fault_or_minor_leak" and "minor_leak" in d["analysis"]["alternatives"]
    assert wf["plan"]["confidence"] <= 0.5


def test_flow_sensor_bias_is_sensor_fault_without_valve_action():
    rt, o = setup([("sensor_bias", (f"flow:{V}", "0.6"))]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "sensor_fault_or_minor_leak"
    assert rt.sim.open_valve == V and wf["state"] == "AWAITING_INSPECTION"


def test_meter_fault_identified():
    rt, o = setup([("meter_mismatch", "0.5")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "sensor_fault_main_meter" and rt.sim.open_valve == V


def test_well_sensor_fault_identified():
    rt, o = setup()
    orig = rt.sim.read
    from maeen.models import Reading
    def biased(sid):
        r = orig(sid)
        return Reading(r.sensor_id, r.value * 0.5, r.unit, r.ts, r.quality, r.source, r.simulated) if sid == "well_flow_m3h" else r
    rt.sim.read = biased
    wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "sensor_fault_well_flow"


def test_pump_degradation_and_filter_clog_are_equipment_with_maintenance_note():
    rt, o = setup([("pump_degraded", "0.6")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "equipment_pump_or_supply" and rt.sim.open_valve == V
    rt, o = setup([("filter_clogged", "0.6")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "equipment_filter_or_blockage"
    assert "maintenance" in rt.db.execute("SELECT text_en FROM notification").fetchone()["text_en"].lower()


def test_downstream_break_detected_no_autonomous_close():
    rt, o = setup([("leak_downstream", "30")]); wf = go(rt, o)
    assert wf["plan"]["data"]["classification"] == "leak_downstream" and "safe_state" not in wf


@pytest.mark.parametrize("fault", [("stale_sensor", "pressure_bar"), ("offline_sensor", f"flow:{V}"),
                                   ("stale_sensor", "main_meter_m3h")])
def test_missing_or_stale_telemetry_blocks_classification(fault):
    rt, o = setup([fault]); wf = go(rt, o)
    assert wf["state"] == "BLOCKED" and wf["plan"]["status"] == "blocked_insufficient_data"
    assert wf["plan"]["data"] == {} and rt.sim.open_valve == V


def test_simulated_evidence_rejected_outside_simulation_mode():
    rt, o = setup([("leak", "25")], mode="advisory"); wf = go(rt, o)
    assert wf["state"] == "BLOCKED" and wf["plan"]["status"] == "blocked_simulated_evidence"
    assert rt.sim.open_valve == V


def test_gateway_safe_close_modes():
    rt, _ = setup(mode="advisory")
    r = rt.gateway.safe_close("irrigation_anomaly", V, "w", "t")
    assert r.status == "advisory_recorded" and rt.sim.open_valve == V
    rt, _ = setup(mode="controlled")
    assert rt.gateway.safe_close("irrigation_anomaly", V, "w", "t").detail["code"] == "not_commissioned"


def test_least_privilege_for_anomaly_agent_and_others():
    rt, _ = setup()
    assert rt.policy.authorize("irrigation_anomaly", "request_valve_command").code == "tool_not_granted"
    assert rt.policy.authorize("irrigation_anomaly", "apply_fertilizer").code == "tool_not_granted"
    assert rt.gateway.safe_close("palm_health", V, "w", "t").detail["code"] == "tool_not_granted"
    assert rt.sim.open_valve == V
    assert rt.policy.authorize("irrigation_anomaly", "modify_safety_limit").code == "prohibited"


def test_duplicate_anomaly_event_is_idempotent():
    rt, o = setup([("leak", "25")]); wf = go(rt, o)
    again = go(rt, o)
    assert again["duplicate"] and rt.db.execute("SELECT COUNT(*) c FROM workflow").fetchone()["c"] == 1
    assert sum(1 for e in rt.audit.trail() if e["event_type"] == "safe_close_verified") == 1


def test_human_confirmation_roles_and_evaluation():
    rt, o = setup([("leak", "15")]); wf = go(rt, o)
    assert "denied" in o.resolve_anomaly(wf["id"], "aud1", "leak_confirmed")["error"]
    assert "denied" in o.resolve_anomaly(wf["id"], "irrigation_anomaly", "leak_confirmed")["error"]
    assert "outcome must be" in o.resolve_anomaly(wf["id"], "op1", "whatever")["error"]
    done = o.resolve_anomaly(wf["id"], "op1", "leak_confirmed", "found cracked fitting")
    assert done["state"] == "RESOLVED"
    ev = evaluate_anomaly_cases(rt.db)
    assert ev["confirmed_cases"] == 1 and ev["accuracy"] == 1.0 and ev["false_alarm_rate"] == 0.0
    assert "error" in o.resolve_anomaly(wf["id"], "op1", "leak_confirmed")     # not resolvable twice


def test_false_alarm_counts_against_quality():
    rt, o = setup([("leak", "15")]); a = go(rt, o, "a")
    o.resolve_anomaly(a["id"], "agro1", "false_alarm")
    ev = evaluate_anomaly_cases(rt.db)
    assert ev["false_alarm_rate"] == 1.0 and ev["accuracy"] == 0.0


def test_output_schema_and_bilingual_explanation():
    rt, o = setup([("leak", "25")]); wf = go(rt, o)
    validate_output(wf["plan"])
    en, ar = explain_anomaly(o.load(wf["id"]), "en"), explain_anomaly(o.load(wf["id"]), "ar")
    assert all(f"{i})" in en and f"{i})" in ar for i in range(1, 8)) and "المحبس" in ar
    assert rt.audit.verify() == (True, None)
