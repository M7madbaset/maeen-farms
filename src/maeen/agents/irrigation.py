from __future__ import annotations
from ..calc.water import block_demand, allocate
from ..validation import validate_reading, contradictory
from .registry import SPECS


def _out(spec, cid, status, summary, **kw):
    d = dict(agent_id=spec.agent_id, version=spec.version, status=status, summary=summary,
             data={}, confidence=0.0, evidence=[], proposed_actions=[],
             risk_category="autonomous", requires_approval=False, correlation_id=cid,
             assumptions=[], warnings=[])
    d.update(kw)
    return d


class IrrigationWaterAgent:
    spec = SPECS["irrigation_water"]

    def run(self, rt, cid: str, params: dict | None = None) -> dict:
        cfg, spec, now = rt.cfg, self.spec, rt.clock.now()
        read = lambda sid: rt.gateway.read_sensor(spec.agent_id, sid)
        readings = {s: read(s) for s in ("well_flow_m3h", "main_meter_m3h", "eto_mm_day")}
        evidence = [r.to_evidence() for r in readings.values() if r]
        issues = []
        for sid, r in readings.items():
            st, why = validate_reading(r, now, cfg.max_data_age_s, cfg.range_for(sid))
            if st != "ok":
                issues.append(f"{sid}: {st} ({why})")
        if issues:
            return _out(spec, cid, "blocked_insufficient_data",
                        "Cannot plan: sensor data missing/stale/invalid. No action proposed.",
                        evidence=evidence, warnings=issues, assumptions=list(cfg.assumptions))
        well, meter = readings["well_flow_m3h"].value, readings["main_meter_m3h"].value
        if contradictory(well, meter, cfg.flow_tolerance):
            return _out(spec, cid, "blocked_contradictory_data",
                        "Well flow and main meter disagree beyond tolerance. Inspect sensors/leak.",
                        evidence=evidence, warnings=[f"well={well} m3/h vs meter={meter} m3/h"],
                        assumptions=list(cfg.assumptions))
        if well < cfg.min_well_flow_m3h:
            return _out(spec, cid, "blocked_no_well_output",
                        "No well output: possible pump/well failure. Route to emergency workflow J.",
                        evidence=evidence, warnings=["well output ~0"], assumptions=list(cfg.assumptions))

        warnings = []
        available = well * cfg.pump_hours_available
        if cfg.allocation_m3_day is not None:
            if cfg.allocation_verified:
                available = min(available, cfg.allocation_m3_day)
            else:
                warnings.append(f"Allocation {cfg.allocation_m3_day} m3/day is UNVERIFIED and not "
                                "used as an availability cap; measured well output is used.")
        eto = readings["eto_mm_day"].value
        demand = {b.block_id: block_demand(b, eto, cfg).gross_m3 for b in cfg.blocks}
        alloc, rem = allocate(demand, cfg.blocks, available)
        total_d, total_a = sum(demand.values()), sum(alloc.values())
        shortage = max(0.0, total_d - available)

        actions = []
        for b in sorted(cfg.blocks, key=lambda x: x.priority):
            vol = alloc[b.block_id]
            if vol < 0.01:
                continue
            flow = min(b.rated_flow_m3h, well)
            minutes = vol / flow * 60.0
            if minutes > cfg.max_valve_minutes:
                minutes = float(cfg.max_valve_minutes)
                vol = flow * minutes / 60.0
                warnings.append(f"{b.block_id}: run time capped by safety limit")
            actions.append({"action": "request_valve_command", "device_id": b.valve_id,
                            "block_id": b.block_id, "minutes": round(minutes, 2),
                            "expected_flow_m3h": round(flow, 3),
                            "planned_volume_m3": round(vol, 3)})
        conf = 0.9 if cfg.parameters_validated else 0.6
        if not cfg.parameters_validated:
            warnings.append("Agronomic parameters are unvalidated placeholders.")
        needs = bool(actions) and shortage > 0
        return _out(
            spec, cid, "ok",
            (f"Shortage of {shortage:.0f} m3: demand {total_d:.0f} m3 vs available {available:.0f} m3."
             if shortage > 0 else "No shortage: available water covers estimated demand."),
            data={"eto_mm_day": eto, "available_m3": available, "demand_m3": demand,
                  "allocation_m3": alloc, "total_demand_m3": total_d, "total_allocated_m3": total_a,
                  "shortage_m3": shortage, "floors_m3": {b.block_id: b.min_fraction * demand[b.block_id]
                                                          for b in cfg.blocks},
                  "scenario": cfg.scenario},
            confidence=conf, evidence=evidence, proposed_actions=actions if shortage > 0 else [],
            risk_category="approval_required" if needs else "autonomous",
            requires_approval=needs, assumptions=list(cfg.assumptions), warnings=warnings)
