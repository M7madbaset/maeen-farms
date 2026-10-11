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
    ap.add_argument("--fault", choices=["none", "stale_well", "meter_mismatch", "valve_stuck", "pump_fault"],
                    default="none")
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


if __name__ == "__main__":
    raise SystemExit(main())
