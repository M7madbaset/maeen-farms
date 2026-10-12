"""Irrigation anomaly classifier. Deterministic rules over redundant flow sensors
(well W, main meter M, valve flow F) plus pressure P, against the expected flow E.
Ambiguity is reported, never hidden."""
from __future__ import annotations
from ..validation import validate_reading, contradictory
from .registry import SPECS
from .irrigation import _out

LOW, HIGH = "low", "high"


class IrrigationAnomalyAgent:
    spec = SPECS["irrigation_anomaly"]

    def run(self, rt, cid: str, params: dict) -> dict:
        cfg, spec, now = rt.cfg, self.spec, rt.clock.now()
        valve, E = params["valve_id"], float(params["expected_flow_m3h"])
        ids = ["well_flow_m3h", "main_meter_m3h", f"flow:{valve}", "pressure_bar"]
        rd = {i: rt.gateway.read_sensor(spec.agent_id, i) for i in ids}
        ev = [r.to_evidence() for r in rd.values() if r]
        issues = []
        for i, r in rd.items():
            st, why = validate_reading(r, now, cfg.max_data_age_s, cfg.range_for(i))
            if st != "ok":
                issues.append(f"{i}: {st} ({why})")
        if issues:
            return _out(spec, cid, "blocked_insufficient_data",
                        "Cannot classify: telemetry missing/stale/invalid. No action taken.",
                        evidence=ev, warnings=issues)
        if rt.mode != "simulation" and any(e["simulated"] for e in ev):
            return _out(spec, cid, "blocked_simulated_evidence",
                        "Simulated evidence is not accepted outside simulation mode.", evidence=ev)
        W, M, F, P = (rd[i].value for i in ids)
        tol, (plo, phi) = cfg.anomaly_flow_tol, cfg.pressure_band_bar
        loss = (M - F) / M if M > 0 else 0.0
        f_dev = abs(F - E) / E if E > 0 else 0.0
        ptag = LOW if P < plo else HIGH if P > phi else "normal"
        a_wm, a_wf, a_mf = (not contradictory(W, M, tol), not contradictory(W, F, tol),
                            not contradictory(M, F, tol))
        analysis = {"W": W, "M": M, "F": F, "P": P, "E": E, "loss_fraction": round(loss, 3),
                    "deviation_from_expected": round(f_dev, 3), "pressure": ptag,
                    "agree": {"W~M": a_wm, "W~F": a_wf, "M~F": a_mf}}
        cls, conf, sev, alts, target = "normal", 0.8, "none", [], None

        if a_wm and a_wf and a_mf:                                  # all three estimates agree
            if f_dev <= tol and ptag == "normal":
                pass
            elif F > E * (1 + tol) and ptag == LOW:
                cls, conf, sev = "leak_downstream", 0.65, "high"
                alts = ["flow_setpoint_error"]
            elif F < E * (1 - tol) and ptag == LOW:
                cls, conf, sev = "equipment_pump_or_supply", 0.6, "medium"
                alts = ["well_drawdown", "partially_closed_valve"]
            elif F < E * (1 - tol) and ptag == HIGH:
                cls, conf, sev = "equipment_filter_or_blockage", 0.6, "medium"
                alts = ["partially_closed_valve"]
            elif ptag != "normal":
                cls, conf, sev = "inconclusive", 0.3, "medium"
            else:
                cls, conf, sev = "unexplained_flow_deviation", 0.4, "medium"
                alts = ["setpoint_error", "sensor_common_cause"]
        elif a_wm and not a_wf:                                     # F deviates from W and M
            if loss >= cfg.leak_loss_warn and ptag == LOW:
                cls, conf = "leak_upstream_of_valve", 0.7
                sev = "severe" if loss >= cfg.leak_loss_severe else "high"
                alts = ["valve_flow_sensor_fault"]
                target = valve
            else:
                cls, conf, sev = "sensor_fault_or_minor_leak", 0.5, "medium"
                alts = ["valve_flow_sensor_fault", "minor_leak"]
        elif a_wf and not a_wm:
            cls, conf, sev, alts = "sensor_fault_main_meter", 0.6, "low", ["meter_calibration_drift"]
        elif a_mf and not a_wm:
            cls, conf, sev, alts = "sensor_fault_well_flow", 0.6, "low", ["well_meter_calibration_drift"]
        else:
            cls, conf, sev = "inconclusive", 0.3, "medium"

        analysis["alternatives"] = alts
        actions = []
        if target and sev == "severe":
            actions.append({"action": "close_valve_safe_state", "device_id": target,
                            "reason": f"severe leak: {loss:.0%} of flow unaccounted"})
        summary = ("Irrigation telemetry is consistent with expectations." if cls == "normal" else
                   f"Anomaly classified as '{cls}' (severity {sev}, confidence {conf:.0%}); "
                   "this is a hypothesis to be confirmed by field inspection.")
        return _out(spec, cid, "ok", summary, data={"classification": cls, "severity": sev,
                                                      "analysis": analysis, "valve_id": valve},
                    confidence=conf, evidence=ev, proposed_actions=actions,
                    risk_category="autonomous", requires_approval=False,
                    warnings=([] if cls == "normal" else ["Image/sensor-based diagnosis is not definitive; inspect."]),
                    assumptions=["Single valve open; no other consumers on the main line.",
                                 "Pressure band and tolerances are unvalidated placeholders."])
