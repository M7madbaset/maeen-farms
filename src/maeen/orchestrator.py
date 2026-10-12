"""Orchestrator: durable workflow state machine for the water-shortage workflow (B)."""
from __future__ import annotations
import hashlib, json, uuid
from datetime import timedelta
from .agents.farm_manager import FarmManagerAgent
from .agents.irrigation import IrrigationWaterAgent
from .agents.anomaly import IrrigationAnomalyAgent
from .agents.registry import validate_output, SchemaError
from .models import Approval, DeviceCommand
from .policy import fingerprint

SIM = "[SIMULATED] "
SIM_AR = "[محاكاة] "


class Orchestrator:
    def __init__(self, rt):
        self.rt = rt

    # -- persistence -----------------------------------------------------
    def load(self, wid: str) -> dict:
        row = self.rt.db.execute("SELECT * FROM workflow WHERE id=?", (wid,)).fetchone()
        return None if row is None else {"id": row["id"], "state": row["state"], **json.loads(row["data"])}

    def _save(self, wid, state, data, new=False, event_id=None, wtype="water_shortage"):
        now = self.rt.clock.now().isoformat()
        d = {k: v for k, v in data.items() if k not in ("id", "state")}
        d.setdefault("history", []).append([state, now])
        if new:
            self.rt.db.execute("INSERT INTO workflow VALUES (?,?,?,?,?,?,?)",
                               (wid, wtype, event_id, state, json.dumps(d, default=str), now, now))
        else:
            self.rt.db.execute("UPDATE workflow SET state=?, data=?, updated_at=? WHERE id=?",
                               (state, json.dumps(d, default=str), now, wid))
        self.rt.db.commit()
        self.rt.audit.append("orchestrator", "workflow_state", wid, {"state": state})
        return {"id": wid, "state": state, **d}

    def _notify(self, wid, audience, severity, en, ar):
        self.rt.db.execute(
            "INSERT INTO notification(ts,workflow_id,audience,severity,text_en,text_ar,simulated) VALUES (?,?,?,?,?,?,1)",
            (self.rt.clock.now().isoformat(), wid, audience, severity, SIM + en, SIM_AR + ar))
        self.rt.db.commit()

    def _run_agent(self, wid, call, spec):
        last = None
        for attempt in range(spec.max_retries + 1):
            try:
                out = validate_output(call())
                self.rt.db.execute("INSERT OR REPLACE INTO agent_execution VALUES (?,?,?,?,?,?,?)",
                                   (f"{wid}:{spec.agent_id}:{attempt}", spec.agent_id, spec.version, wid,
                                    out["status"], json.dumps(out, default=str, ensure_ascii=False),
                                    self.rt.clock.now().isoformat()))
                self.rt.db.commit()
                self.rt.audit.append(spec.agent_id, "agent_output", wid,
                                     {"status": out["status"], "summary": out["summary"],
                                      "confidence": out["confidence"]})
                return out
            except (SchemaError, PermissionError, ValueError, KeyError) as e:
                last = e
                self.rt.audit.append(spec.agent_id, "agent_error", wid, {"attempt": attempt, "error": str(e)})
        raise RuntimeError(f"agent {spec.agent_id} failed: {last}")

    # -- workflow ----------------------------------------------------------
    def start_water_shortage(self, event_id: str, requested_by: str = "system") -> dict:
        rt = self.rt
        row = rt.db.execute("SELECT id FROM workflow WHERE event_id=?", (event_id,)).fetchone()
        if row:
            rt.audit.append("orchestrator", "duplicate_event_ignored", row["id"], {"event_id": event_id})
            return {**self.load(row["id"]), "duplicate": True}
        wid = "wf-" + hashlib.sha256(event_id.encode()).hexdigest()[:10]
        wf = self._save(wid, "DATA_VALIDATION", {"mode": rt.mode, "requested_by": requested_by,
                                                  "simulated": True}, new=True, event_id=event_id)
        planner, reviewer = IrrigationWaterAgent(), FarmManagerAgent()
        try:
            plan = self._run_agent(wid, lambda: planner.run(rt, wid), planner.spec)
        except RuntimeError as e:
            self._notify(wid, "owner", "high", f"Planning failed: {e}", f"فشل التخطيط: {e}")
            return self._save(wid, "FAILED", {**wf, "error": str(e)})
        wf["plan"] = plan
        if plan["status"] != "ok":
            self._notify(wid, "owner,agronomist", "high",
                         f"Plan blocked: {plan['status']}. {'; '.join(plan['warnings'])}. Field inspection needed.",
                         f"تعذر إنشاء الخطة: {plan['status']}. يلزم فحص ميداني للحساسات والمصدر.")
            return self._save(wid, "BLOCKED", wf)
        if not plan["proposed_actions"]:
            return self._save(wid, "COMPLETED_NO_ACTION", wf)
        wf = self._save(wid, "UNDER_REVIEW", wf)
        try:
            review = self._run_agent(wid, lambda: reviewer.review(rt, wid, plan), reviewer.spec)
        except RuntimeError as e:
            return self._save(wid, "FAILED", {**wf, "error": str(e)})
        wf["review"] = review
        if review["data"]["verdict"] != "approve":
            self._notify(wid, "owner,agronomist", "high", "Independent review REJECTED the plan; escalated.",
                         "رفضت المراجعة المستقلة الخطة وتم التصعيد.")
            return self._save(wid, "ESCALATED", wf)
        wf["plan_hash"] = hashlib.sha256(json.dumps(plan["proposed_actions"], sort_keys=True).encode()).hexdigest()
        d = plan["data"]
        self._notify(wid, "owner,agronomist", "high",
                     f"Water shortage: available {d['available_m3']:.0f} m3 vs demand {d['total_demand_m3']:.0f} m3. "
                     f"Plan awaiting approval (owner or agronomist).",
                     f"نقص في المياه: المتاح {d['available_m3']:.0f} م³ مقابل احتياج {d['total_demand_m3']:.0f} م³. "
                     f"الخطة بانتظار موافقة المالك أو المهندس الزراعي.")
        return self._save(wid, "AWAITING_APPROVAL", wf)

    def approve(self, wid: str, user_id: str) -> dict:
        rt = self.rt
        wf = self.load(wid)
        if wf is None or wf["state"] != "AWAITING_APPROVAL":
            return {**(wf or {}), "error": "workflow not awaiting approval"}
        dec = rt.policy.validate_approver(user_id, "request_valve_command")
        if not dec.allowed:
            rt.audit.append(user_id, "approval_denied", wid, {"code": dec.code, "reason": dec.reason})
            return {**wf, "error": f"approval denied: {dec.code}"}
        now = rt.clock.now()
        cmds = []
        for a in wf["plan"]["proposed_actions"]:
            cmds.append((a, fingerprint(wid, a["device_id"], "run_valve_timed", a["minutes"], a["expected_flow_m3h"])))
        approval = Approval("ap-" + uuid.uuid4().hex[:8], wid, user_id, now,
                            now + timedelta(seconds=rt.cfg.approval_ttl_s), tuple(f for _, f in cmds))
        rt.db.execute("INSERT INTO approval VALUES (?,?,?,?,?,?)",
                      (approval.approval_id, wid, user_id, json.dumps(approval.fingerprints),
                       now.isoformat(), approval.expires_at.isoformat()))
        rt.db.commit()
        rt.audit.append(user_id, "plan_approved", wid, {"approval_id": approval.approval_id,
                                                       "plan_hash": wf["plan_hash"]})
        wf["approved_by"] = user_id
        wf = self._save(wid, "EXECUTING", wf)
        dispatch, results, failed = now, [], None
        for a, fp in cmds:
            cmd = DeviceCommand("cmd-" + uuid.uuid4().hex[:8], f"{wid}:{a['device_id']}:{fp[:12]}",
                                a["device_id"], "run_valve_timed", a["minutes"], a["expected_flow_m3h"],
                                a["planned_volume_m3"], rt.clock.now() + timedelta(seconds=rt.cfg.command_ttl_s),
                                wid, "irrigation_water")
            res = rt.gateway.execute_command(cmd, "irrigation_water", approval, dispatch)
            results.append({"device": a["device_id"], "block": a["block_id"], "status": res.status,
                            "verified": res.verified, "simulated": res.simulated, "detail": res.detail})
            if res.status not in ("verified", "advisory_recorded"):
                failed = (a, res)
                break
        wf["results"] = results
        if failed:
            a, res = failed
            self._notify(wid, "owner,agronomist,irrigation_operator", "critical",
                         f"Execution failed on {a['device_id']} ({res.status}); valve closed, remaining plan stopped.",
                         f"فشل التنفيذ/التحقق على المحبس {a['device_id']}؛ تم إغلاقه وإيقاف بقية الخطة.")
            return self._save(wid, "FAILED_VERIFICATION", wf)
        if rt.mode == "advisory":
            return self._save(wid, "COMPLETED_ADVISORY", wf)   # outcome pending human confirmation
        self._notify(wid, "owner", "info", "Plan executed and verified against telemetry.",
                     "تم تنفيذ الخطة والتحقق منها بالقياسات.")
        return self._save(wid, "COMPLETED", wf)

    def reject(self, wid: str, user_id: str, reason: str = "") -> dict:
        wf = self.load(wid)
        dec = self.rt.policy.validate_approver(user_id, "request_valve_command")
        if wf is None or wf["state"] != "AWAITING_APPROVAL" or not dec.allowed:
            return {**(wf or {}), "error": "cannot reject"}
        self.rt.audit.append(user_id, "plan_rejected", wid, {"reason": reason})
        return self._save(wid, "REJECTED", wf)


# ---------------------------------------------------------------------------
# Workflow C: irrigation anomaly
# ---------------------------------------------------------------------------
ANOMALY_TEXT = {   # class -> (EN, AR)
    "leak_upstream_of_valve": ("Probable LEAK upstream of {v}: {loss:.0%} of flow unaccounted, pressure low. Inspect the line.",
                               "تسرب محتمل قبل المحبس {v}: {loss:.0%} من التدفق غير محسوب والضغط منخفض. يلزم فحص الخط."),
    "leak_downstream": ("Probable break DOWNSTREAM of {v}: flow above expected with low pressure. Inspect drip lines.",
                        "كسر محتمل بعد المحبس {v}: التدفق أعلى من المتوقع مع ضغط منخفض. افحص خطوط التنقيط."),
    "sensor_fault_or_minor_leak": ("Valve {v} flow reading disagrees with well/meter but pressure is normal: sensor fault or minor leak. Inspect and calibrate.",
                                   "قراءة تدفق المحبس {v} تخالف البئر/العداد مع ضغط طبيعي: عطل حساس أو تسرب بسيط. افحص وعاير."),
    "sensor_fault_main_meter": ("Main meter disagrees with well flow and valve flow: probable meter fault. Calibrate.",
                                "العداد الرئيسي يخالف تدفق البئر والمحبس: عطل محتمل في العداد. يلزم المعايرة."),
    "sensor_fault_well_flow": ("Well flow sensor disagrees with meter and valve flow: probable sensor fault. Calibrate.",
                               "حساس تدفق البئر يخالف العداد والمحبس: عطل محتمل. يلزم المعايرة."),
    "equipment_pump_or_supply": ("Low flow with low pressure: pump degradation or supply shortfall. Maintenance work order recommended.",
                                 "تدفق وضغط منخفضان: تدهور المضخة أو نقص الإمداد. يوصى بأمر صيانة."),
    "equipment_filter_or_blockage": ("Low flow with HIGH pressure: filter clogging or blockage. Maintenance work order recommended.",
                                     "تدفق منخفض مع ضغط مرتفع: انسداد فلتر أو خط. يوصى بأمر صيانة."),
}
DEFAULT_ANOMALY_TEXT = ("Irrigation anomaly '{cls}' on {v}: inconclusive, inspect.",
                        "شذوذ في الري '{cls}' على {v}: غير حاسم، يلزم الفحص.")
CONFIRMABLE = ("leak_confirmed", "sensor_fault_confirmed", "equipment_fault_confirmed", "false_alarm")


def _start_anomaly(self, event_id: str, valve_id: str, expected_flow_m3h: float) -> dict:
    rt = self.rt
    row = rt.db.execute("SELECT id FROM workflow WHERE event_id=?", (event_id,)).fetchone()
    if row:
        rt.audit.append("orchestrator", "duplicate_event_ignored", row["id"], {"event_id": event_id})
        return {**self.load(row["id"]), "duplicate": True}
    wid = "an-" + hashlib.sha256(event_id.encode()).hexdigest()[:10]
    wf = self._save(wid, "DETECTING", {"mode": rt.mode, "simulated": True, "valve_id": valve_id,
                                        "expected_flow_m3h": expected_flow_m3h},
                    new=True, event_id=event_id, wtype="irrigation_anomaly")
    agent = IrrigationAnomalyAgent()
    try:
        out = self._run_agent(wid, lambda: agent.run(rt, wid, {"valve_id": valve_id,
                                                               "expected_flow_m3h": expected_flow_m3h}), agent.spec)
    except RuntimeError as e:
        self._notify(wid, "irrigation_operator", "high", f"Anomaly detection failed: {e}", f"فشل كشف الشذوذ: {e}")
        return self._save(wid, "FAILED", {**wf, "error": str(e)})
    wf["plan"] = out
    if out["status"] != "ok":
        self._notify(wid, "irrigation_operator,agronomist", "high",
                     f"Anomaly check blocked: {out['status']}. {'; '.join(out['warnings'])}",
                     f"تعذر فحص الشذوذ: {out['status']}. بيانات القياس غير صالحة؛ يلزم فحص ميداني.")
        return self._save(wid, "BLOCKED", wf)
    d = out["data"]; cls = d["classification"]
    rt.db.execute("INSERT INTO anomaly_case(workflow_id,predicted,confidence,ts) VALUES (?,?,?,?)",
                  (wid, cls, out["confidence"], rt.clock.now().isoformat()))
    rt.db.commit()
    if cls == "normal":
        return self._save(wid, "COMPLETED_NO_ACTION", wf)
    en, ar = ANOMALY_TEXT.get(cls, DEFAULT_ANOMALY_TEXT)
    fmt = {"v": valve_id, "loss": d["analysis"]["loss_fraction"], "cls": cls}
    sev = "critical" if d["severity"] == "severe" else "high"
    self._notify(wid, "irrigation_operator,agronomist", sev, en.format(**fmt), ar.format(**fmt))
    safe = [a for a in out["proposed_actions"] if a["action"] == "close_valve_safe_state"]
    if safe:
        res = rt.gateway.safe_close(agent.spec.agent_id, safe[0]["device_id"], wid, safe[0]["reason"])
        wf["safe_state"] = {"status": res.status, "verified": res.verified, "detail": res.detail}
        if res.status == "failed_verification":
            self._notify(wid, "owner,irrigation_operator", "critical",
                         f"Valve {valve_id} did NOT close. Stop the pump / use manual or hardware shut-off NOW.",
                         f"المحبس {valve_id} لم يُغلق. أوقف المضخة أو استخدم الإغلاق اليدوي/الهيدروليكي فورًا.")
            return self._save(wid, "ESCALATED_CRITICAL", wf)
        if res.verified:
            self._notify(wid, "owner,irrigation_operator", "critical",
                         f"Safe state: valve {valve_id} closed and verified.", f"الوضع الآمن: تم إغلاق المحبس {valve_id} والتحقق منه.")
    return self._save(wid, "AWAITING_INSPECTION", wf)


def _resolve_anomaly(self, wid: str, user_id: str, outcome: str, note: str = "") -> dict:
    rt = self.rt
    wf = self.load(wid)
    if wf is None or wf["state"] != "AWAITING_INSPECTION":
        return {**(wf or {}), "error": "workflow not awaiting inspection"}
    if outcome not in CONFIRMABLE:
        return {**wf, "error": f"outcome must be one of {CONFIRMABLE}"}
    dec = rt.policy.validate_approver(user_id, "confirm_anomaly_outcome")
    if not dec.allowed:
        rt.audit.append(user_id, "outcome_denied", wid, {"code": dec.code})
        return {**wf, "error": f"denied: {dec.code}"}
    rt.db.execute("UPDATE anomaly_case SET confirmed=?, confirmed_by=?, note=?, confirmed_ts=? WHERE workflow_id=?",
                  (outcome, user_id, note, rt.clock.now().isoformat(), wid))
    rt.db.commit()
    rt.audit.append(user_id, "human_confirmed_outcome", wid, {"outcome": outcome, "note": note})
    wf["confirmed_outcome"] = outcome
    return self._save(wid, "RESOLVED", wf)


Orchestrator.start_irrigation_anomaly = _start_anomaly
Orchestrator.resolve_anomaly = _resolve_anomaly
