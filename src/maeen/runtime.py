from __future__ import annotations
from dataclasses import dataclass
from . import config as C
from .agents.registry import SPECS
from .audit import AuditLog
from .db import connect
from .gateway import ToolGateway
from .policy import Policy
from .simulator import FarmSimulator, SimClock


@dataclass
class Runtime:
    cfg: object
    db: object
    clock: object
    sim: object
    audit: object
    policy: object
    gateway: object
    mode: str


def build_runtime(scenario="40", mode=C.SIMULATION, db_path=":memory:",
                  well_flow=45.0, eto=7.5, users=None) -> Runtime:
    if mode not in C.MODES:
        raise ValueError(f"mode must be one of {C.MODES}")
    cfg = C.build_config(scenario)
    clock = SimClock()
    db = connect(db_path)
    rt = Runtime(cfg, db, clock, FarmSimulator(cfg, clock, well_flow, eto),
                 AuditLog(db, clock), None, None, mode)
    rt.policy = Policy(SPECS, users or C.USERS, clock)
    rt.gateway = ToolGateway(rt)
    rt.audit.append("system", "runtime_started", None,
                    {"mode": mode, "scenario": cfg.scenario, "simulated_data": True})
    return rt
