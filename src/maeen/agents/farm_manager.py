from __future__ import annotations
from .registry import SPECS
from .irrigation import _out

EPS = 1e-6


class FarmManagerAgent:
    """Independent reviewer: checks plan invariants without re-using the planner's code path."""
    spec = SPECS["farm_manager"]

    def review(self, rt, cid: str, plan: dict) -> dict:
        d, cfg, findings = plan["data"], rt.cfg, []
        alloc, demand, floors = d["allocation_m3"], d["demand_m3"], d["floors_m3"]
        if sum(alloc.values()) > d["available_m3"] + EPS:
            findings.append("total allocation exceeds available water")
        for b, v in alloc.items():
            if v > demand[b] + EPS: findings.append(f"{b}: allocation exceeds demand")
            if v < -EPS: findings.append(f"{b}: negative allocation")
        for b in sorted(cfg.blocks, key=lambda x: x.priority):   # priority invariant
            if alloc[b.block_id] > floors[b.block_id] + EPS:
                for hb in cfg.blocks:
                    if hb.priority < b.priority and alloc[hb.block_id] < demand[hb.block_id] - EPS:
                        findings.append(f"{b.block_id} above floor while higher-priority {hb.block_id} unmet")
        for a in plan["proposed_actions"]:
            if a["minutes"] > cfg.max_valve_minutes: findings.append(f"{a['device_id']}: exceeds safety minutes")
        if rt.mode != "simulation" and any(e.get("simulated") for e in plan["evidence"]):
            findings.append("simulated evidence used outside simulation mode")
        verdict = "reject" if findings else "approve"
        return _out(self.spec, cid, "ok", f"Independent review verdict: {verdict}.",
                    data={"verdict": verdict, "findings": findings}, confidence=0.95,
                    evidence=plan["evidence"][:3], assumptions=[], warnings=findings)
