"""Sensor/actuator simulator. EVERYTHING it returns is flagged simulated=True.
It also models hardware interlocks that are independent of any software above it."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .models import Reading


class SimClock:
    def __init__(self, start: datetime | None = None):
        self._t = start or datetime(2026, 10, 11, 5, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._t

    def advance(self, minutes: float):
        self._t += timedelta(minutes=minutes)


class FarmSimulator:
    is_simulated = True
    HW_MAX_MINUTES = 960          # hardware timer ceiling (independent of software limit)

    def __init__(self, cfg, clock: SimClock, well_flow_m3h: float = 45.0, eto_mm_day: float = 7.5):
        self.cfg, self.clock = cfg, clock
        self.well_flow, self.eto = well_flow_m3h, eto_mm_day
        self.open_valve: str | None = None
        self.delivered = {v: 0.0 for v in cfg.valve_ids()}
        self.stale: set = set()
        self.offline: set = set()
        self.stuck_closed: set = set()
        self.meter_factor = 1.0
        self.pump_fault = False

    # --- fault injection -------------------------------------------------
    def inject(self, fault: str, target: str | None = None):
        if fault == "stale_sensor": self.stale.add(target)
        elif fault == "offline_sensor": self.offline.add(target)
        elif fault == "valve_stuck_closed": self.stuck_closed.add(target)
        elif fault == "meter_mismatch": self.meter_factor = float(target)
        elif fault == "pump_fault": self.pump_fault = True
        else: raise ValueError(f"unknown fault {fault}")

    # --- telemetry -------------------------------------------------------
    def _well(self) -> float:
        return 0.0 if self.pump_fault else self.well_flow

    def _valve_flow(self, valve: str) -> float:
        if self.open_valve != valve or valve in self.stuck_closed:
            return 0.0
        rated = next(b.rated_flow_m3h for b in self.cfg.blocks if b.valve_id == valve)
        return min(rated, self._well())

    def read(self, sensor_id: str) -> Reading | None:
        if sensor_id in self.offline:
            return None
        if sensor_id == "well_flow_m3h": v, u = self._well(), "m3/h"
        elif sensor_id == "main_meter_m3h": v, u = self._well() * self.meter_factor, "m3/h"
        elif sensor_id == "eto_mm_day": v, u = self.eto, "mm/day"
        elif sensor_id.startswith("flow:"): v, u = self._valve_flow(sensor_id[5:]), "m3/h"
        elif sensor_id.startswith("volume_m3:"): v, u = self.delivered[sensor_id[10:]], "m3"
        else: raise KeyError(sensor_id)
        ts = self.clock.now() - (timedelta(hours=1) if sensor_id in self.stale else timedelta(0))
        return Reading(sensor_id, v, u, ts, "good", "simulator", True)

    # --- actuation (with hardware interlocks) ------------------------------
    def command(self, device_id: str, action: str, minutes: float = 0.0) -> dict:
        if device_id not in self.delivered:
            return {"ok": False, "reason": "unknown_device"}
        if action == "close_valve":
            self.open_valve = None
            return {"ok": True, "state": "closed"}
        if action != "run_valve_timed":
            return {"ok": False, "reason": "unsupported_action"}
        if self.open_valve is not None:
            return {"ok": False, "reason": "hw_interlock_another_valve_open"}
        if self.pump_fault or self._well() <= 0:
            return {"ok": False, "reason": "hw_interlock_dry_run_protection"}
        if not (0 < minutes <= self.HW_MAX_MINUTES):
            return {"ok": False, "reason": "hw_interlock_timer_limit"}
        self.open_valve = device_id
        return {"ok": True, "state": "open"}

    def elapse(self, minutes: float):
        """Simulation-only: fast-forward a timed run, accumulate totaliser, close valve."""
        if self.open_valve:
            self.delivered[self.open_valve] += self._valve_flow(self.open_valve) * minutes / 60.0
        self.clock.advance(minutes)
        self.open_valve = None
