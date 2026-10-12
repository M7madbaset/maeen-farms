"""Tool authorization gateway + deterministic safety layer. The ONLY path to a device."""
from __future__ import annotations
import json
from datetime import timedelta
from .models import CommandResult, DeviceCommand
from .policy import fingerprint
from .validation import validate_reading


class ToolGateway:
    def __init__(self, rt):
        self.rt = rt

    def read_sensor(self, agent_id: str, sensor_id: str):
        d = self.rt.policy.authorize(agent_id, "read_telemetry")
        if not d.allowed:
            self.rt.audit.append(agent_id, "tool_denied", None, {"tool": "read_telemetry", "code": d.code})
            raise PermissionError(d.reason)
        r = self.rt.sim.read(sensor_id)
        self.rt.audit.append(agent_id, "telemetry_read", None,
                             {"sensor": sensor_id, "value": None if r is None else r.value,
                              "simulated": True})
        return r

    # ------------------------------------------------------------------
    def _deny(self, cmd, agent_id, code, reason, simulated=True):
        self.rt.audit.append(agent_id, "command_denied", cmd.workflow_id,
                             {"device": cmd.device_id, "code": code, "reason": reason})
        return CommandResult("denied", False, simulated, {"code": code, "reason": reason})

    def _store(self, cmd, res: CommandResult):
        self.rt.db.execute(
            "INSERT OR REPLACE INTO device_command VALUES (?,?,?,?,?,?,?)",
            (cmd.idempotency_key, cmd.command_id, cmd.workflow_id, cmd.device_id, res.status,
             json.dumps({"verified": res.verified, "simulated": res.simulated, "detail": res.detail}),
             self.rt.clock.now().isoformat()))
        self.rt.db.commit()

    def _safety_check(self, cmd: DeviceCommand):
        """Deterministic limits, evaluated independently of the agent that proposed the command."""
        cfg, rt = self.rt.cfg, self.rt
        if cmd.device_id not in cfg.valve_ids():
            return "unknown_device", "device is not a configured valve"
        if cmd.action != "run_valve_timed":
            return "unsupported_action", cmd.action
        if not (0 < cmd.minutes <= cfg.max_valve_minutes):
            return "limit_minutes", f"{cmd.minutes} min outside (0, {cfg.max_valve_minutes}]"
        well = rt.sim.read("well_flow_m3h")
        st, why = validate_reading(well, rt.clock.now(), cfg.max_data_age_s, cfg.range_for("well_flow_m3h"))
        if st != "ok":
            return "well_data_untrusted", f"well flow {st}: {why}"
        if well.value < cfg.min_well_flow_m3h:
            return "dry_run_risk", "well output ~0; refusing to run valve"
        for v in cfg.valve_ids():
            r = rt.sim.read(f"flow:{v}")
            st, why = validate_reading(r, rt.clock.now(), cfg.max_data_age_s, cfg.range_for(f"flow:{v}"))
            if st != "ok":
                return "valve_state_unknown", f"{v}: {st}"
            if r.value > 0.05:
                return "another_valve_open", f"{v} is already flowing"
        return None

    def execute_command(self, cmd: DeviceCommand, agent_id: str, approval, dispatch_time=None):
        rt, cfg = self.rt, self.rt.cfg
        fp = fingerprint(cmd.workflow_id, cmd.device_id, cmd.action, cmd.minutes, cmd.expected_flow_m3h)
        d = rt.policy.authorize(agent_id, "request_valve_command", approval, fp, dispatch_time)
        if not d.allowed:
            return self._deny(cmd, agent_id, d.code, d.reason)
        prior = rt.db.execute("SELECT * FROM device_command WHERE idempotency_key=?",
                              (cmd.idempotency_key,)).fetchone()
        if prior:
            rt.audit.append("gateway", "duplicate_command_ignored", cmd.workflow_id,
                            {"key": cmd.idempotency_key})
            info = json.loads(prior["result"])
            return CommandResult(prior["status"], info["verified"], info["simulated"],
                                 {**info["detail"], "duplicate": True})
        if cmd.expires_at < rt.clock.now():
            return self._deny(cmd, agent_id, "command_expired", "command expired before dispatch")
        bad = self._safety_check(cmd)
        if bad:
            res = self._deny(cmd, agent_id, "safety_" + bad[0], bad[1])
            self._store(cmd, res)
            return res

        mode = rt.mode
        if mode == "advisory":
            res = CommandResult("advisory_recorded", False, True,
                                {"note": "advisory mode: no device command issued; human must act "
                                         "and record the actual outcome"})
            rt.audit.append(agent_id, "advisory_recorded", cmd.workflow_id, {"device": cmd.device_id})
            self._store(cmd, res)
            return res
        if mode == "controlled" and (rt.sim.is_simulated or cmd.device_id not in cfg.commissioned_valves):
            return self._deny(cmd, agent_id, "not_commissioned",
                              "controlled mode requires a commissioned real-hardware adapter")

        before = rt.sim.read(f"volume_m3:{cmd.device_id}").value
        hw = rt.sim.command(cmd.device_id, "run_valve_timed", cmd.minutes)
        rt.audit.append("safety_layer", "device_command_sent", cmd.workflow_id,
                        {"device": cmd.device_id, "minutes": cmd.minutes, "hw": hw, "simulated": True})
        if not hw["ok"]:
            res = CommandResult("rejected_by_hardware", False, True, hw)
            self._store(cmd, res)
            return res
        flow = rt.sim.read(f"flow:{cmd.device_id}")
        tol = cfg.verify_tolerance
        if flow is None or abs(flow.value - cmd.expected_flow_m3h) > tol * cmd.expected_flow_m3h:
            rt.sim.command(cmd.device_id, "close_valve")           # safe fallback
            rt.audit.append("safety_layer", "safe_state_close_valve", cmd.workflow_id,
                            {"device": cmd.device_id, "reason": "flow verification failed"})
            res = CommandResult("failed_verification", False, True,
                                {"expected_m3h": cmd.expected_flow_m3h,
                                 "measured_m3h": None if flow is None else flow.value,
                                 "fallback": "valve closed"})
            self._store(cmd, res)
            return res
        rt.sim.elapse(cmd.minutes)
        delivered = rt.sim.read(f"volume_m3:{cmd.device_id}").value - before
        ok = abs(delivered - cmd.planned_volume_m3) <= tol * cmd.planned_volume_m3
        res = CommandResult("verified" if ok else "failed_verification", ok, True,
                            {"planned_m3": cmd.planned_volume_m3, "delivered_m3": round(delivered, 3),
                             "measured_flow_m3h": flow.value})
        rt.audit.append("safety_layer", "result_verified" if ok else "result_unverified",
                        cmd.workflow_id, res.detail)
        self._store(cmd, res)
        return res

    # ------------------------------------------------------------------
    def safe_close(self, agent_id: str, valve_id: str, workflow_id: str, reason: str) -> CommandResult:
        """Move a valve to the safe (closed) state. Autonomous by policy, but still gated by
        tool grant, mode and commissioning, and always VERIFIED against telemetry."""
        rt, cfg = self.rt, self.rt.cfg
        d = rt.policy.authorize(agent_id, "close_valve_safe_state")
        if not d.allowed:
            rt.audit.append(agent_id, "safe_close_denied", workflow_id, {"code": d.code})
            return CommandResult("denied", False, True, {"code": d.code, "reason": d.reason})
        if valve_id not in cfg.valve_ids():
            return CommandResult("denied", False, True, {"code": "unknown_device"})
        if rt.mode == "advisory":
            rt.audit.append(agent_id, "advisory_recorded", workflow_id,
                            {"action": "close_valve", "device": valve_id, "reason": reason})
            return CommandResult("advisory_recorded", False, True,
                                 {"note": "advisory mode: operator must close the valve manually"})
        if rt.mode == "controlled" and (rt.sim.is_simulated or valve_id not in cfg.commissioned_valves):
            return CommandResult("denied", False, True, {"code": "not_commissioned"})
        hw = rt.sim.command(valve_id, "close_valve")
        rt.audit.append("safety_layer", "device_command_sent", workflow_id,
                        {"device": valve_id, "action": "close_valve", "reason": reason,
                         "hw": hw, "simulated": True})
        flow = rt.sim.read(f"flow:{valve_id}")
        closed = bool(hw.get("ok")) and flow is not None and flow.value <= 0.05
        res = CommandResult("verified" if closed else "failed_verification", closed, True,
                            {"hw": hw, "flow_after_m3h": None if flow is None else flow.value})
        rt.audit.append("safety_layer", "safe_close_verified" if closed else "safe_close_FAILED",
                        workflow_id, res.detail)
        return res
