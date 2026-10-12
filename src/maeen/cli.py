from __future__ import annotations
import argparse, sys
from .orchestrator import Orchestrator
from .report import explain
from .runtime import build_runtime


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="maeen", description="MAeen FarmOS slice-1 demo (SIMULATION)")
    ap.add_argument("--scenario", choices=["40", "142"], default="40")
    ap.add_argument("--mode", choices=["simulation", "advisory", "controlled"], default="simulation")
    ap.add_argument("--well-flow", type=float, default=45.0, help="simulated well output m3/h")
    ap.add_argument("--workflow", choices=["water", "anomaly"], default="water")
    ap.add_argument("--fault", choices=["none", "stale_well", "meter_mismatch", "valve_stuck", "pump_fault",
                                        "leak", "leak_severe", "leak_downstream", "flow_sensor_bias",
                                        "pump_degraded", "filter_clogged", "severe_leak_valve_stuck_open"],
                    default="none")
    ap.add_argument("--confirm", choices=["leak_confirmed", "sensor_fault_confirmed",
                                          "equipment_fault_confirmed", "false_alarm"],
                    help="anomaly workflow: human confirms the outcome (as --approver)")
    ap.add_argument("--approver", default="owner1", help="user id approving (try op1 to see a denial)")
    ap.add_argument("--lang", choices=["en", "ar"], default="en")
    ap.add_argument("--no-approve", action="store_true")
    a = ap.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    rt = build_runtime(a.scenario, a.mode, well_flow=a.well_flow)
    if a.fault == "stale_well": rt.sim.inject("stale_sensor", "well_flow_m3h")
    if a.fault == "meter_mismatch": rt.sim.inject("meter_mismatch", "0.5")
    if a.fault == "valve_stuck": rt.sim.inject("valve_stuck_closed", "V-B2")
    if a.fault == "pump_fault": rt.sim.inject("pump_fault")
    orch = Orchestrator(rt)
    if a.workflow == "anomaly":
        return _anomaly_demo(a, rt, orch)
    print(f"== MAeen FarmOS | scenario={rt.cfg.scenario} | mode={a.mode} | ALL DATA SIMULATED ==")
    wf = orch.start_water_shortage("evt-demo-001")
    print(f"[1] workflow {wf['id']} -> {wf['state']}")
    for w in wf.get("plan", {}).get("warnings", []): print("    warning:", w)
    if wf["state"] == "AWAITING_APPROVAL" and not a.no_approve:
        print(f"[2] approval attempt by '{a.approver}'")
        wf2 = orch.approve(wf["id"], a.approver)
        print("    ->", wf2.get("error") or wf2["state"])
        wf = wf2 if "error" not in wf2 else wf
        if wf.get("results"):
            for r in wf["results"]: print(f"    {r['device']}: {r['status']} {r['detail']}")
    print("\n" + explain(orch.load(wf["id"]), a.lang))
    ok, bad = rt.audit.verify()
    print(f"\nAudit events: {len(rt.audit.trail())} | hash chain intact: {ok}")
    for n in rt.db.execute("SELECT severity,text_en,text_ar FROM notification"):
        print(f"  [{n['severity']}] {n['text_en'] if a.lang=='en' else n['text_ar']}")
    return 0


def _anomaly_demo(a, rt, orch) -> int:
    from .report import explain_anomaly
    from .evaluation import evaluate_anomaly_cases
    valve, expected = "V-B1", min(rt.cfg.block("B1").rated_flow_m3h, a.well_flow)
    rt.sim.open_valve = valve                         # simulate an irrigation run in progress
    f = a.fault
    if f == "leak": rt.sim.inject("leak", "15")
    if f in ("leak_severe", "severe_leak_valve_stuck_open"): rt.sim.inject("leak", "25")
    if f == "severe_leak_valve_stuck_open": rt.sim.inject("valve_stuck_open", valve)
    if f == "leak_downstream": rt.sim.inject("leak_downstream", "30")
    if f == "flow_sensor_bias": rt.sim.inject("sensor_bias", (f"flow:{valve}", "0.6"))
    if f == "pump_degraded": rt.sim.inject("pump_degraded", "0.6")
    if f == "filter_clogged": rt.sim.inject("filter_clogged", "0.6")
    if f == "stale_well": rt.sim.inject("stale_sensor", "well_flow_m3h")
    if f == "meter_mismatch": rt.sim.inject("meter_mismatch", "0.5")
    wf = orch.start_irrigation_anomaly("evt-anomaly-001", valve, expected)
    print(f"[1] workflow {wf['id']} -> {wf['state']}")
    if wf.get("safe_state"): print("    safe-state:", wf["safe_state"]["status"], wf["safe_state"]["detail"])
    if a.confirm and wf["state"] == "AWAITING_INSPECTION":
        r = orch.resolve_anomaly(wf["id"], a.approver, a.confirm, "demo")
        print(f"[2] human confirmation by '{a.approver}' ->", r.get("error") or r["state"])
    print("\n" + explain_anomaly(orch.load(wf["id"]), a.lang))
    print("\nEvaluation (human-confirmed only):", evaluate_anomaly_cases(rt.db))
    ok, _ = rt.audit.verify()
    print(f"Audit events: {len(rt.audit.trail())} | hash chain intact: {ok}")
    for n in rt.db.execute("SELECT severity,text_en,text_ar FROM notification"):
        print(f"  [{n['severity']}] {n['text_en'] if a.lang=='en' else n['text_ar']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
