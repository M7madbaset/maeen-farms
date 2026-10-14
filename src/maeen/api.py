"""REST API (FastAPI). Security model:
 * every route except /health and /auth/login requires a valid bearer token;
 * roles are looked up live from the database, never taken from the token;
 * the acting user for approvals/confirmations is the authenticated user - never a request field;
 * the policy engine, not the API, decides whether an approval is valid.
All data served in slices 1-5 is SIMULATED."""
from __future__ import annotations
import math, re, threading
from typing import Literal, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from .daily_report import build_daily_report, render_html, render_text
from .evaluation import evaluate_anomaly_cases
from .orchestrator import Orchestrator, CONFIRMABLE
from .report import explain, explain_anomaly

WID = re.compile(r"^(wf|an)-[0-9a-f]{10}$")
EVENT_ID = r"^[A-Za-z0-9._:-]{1,100}$"
PUBLIC_PATHS = {"/health", "/auth/login"}
WORKFLOW_STARTERS = ("owner", "agronomist", "irrigation_operator", "admin")
AUDIT_READERS = ("auditor", "owner", "admin")
SENSORS = {"well_flow_m3h", "main_meter_m3h", "eto_mm_day", "pressure_bar"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class StartWater(Strict):
    event_id: str = Field(pattern=EVENT_ID)


class StartAnomaly(StartWater):
    valve_id: str = Field(min_length=1, max_length=32)
    expected_flow_m3h: float = Field(gt=0, le=500, allow_inf_nan=False)


class Reject(Strict):
    reason: str = Field(default="", max_length=300)


class Outcome(Strict):
    outcome: Literal["leak_confirmed", "sensor_fault_confirmed", "equipment_fault_confirmed", "false_alarm"]
    note: str = Field(default="", max_length=500)


class Fault(Strict):
    fault: Literal["leak", "leak_downstream", "pump_degraded", "filter_clogged", "meter_mismatch",
                   "valve_stuck_closed", "valve_stuck_open", "stale_sensor", "offline_sensor",
                   "pump_fault", "open_valve", "sensor_bias"]
    target: Optional[str] = Field(default=None, max_length=64)
    factor: Optional[float] = Field(default=None, allow_inf_nan=False)


class User:
    def __init__(self, username: str, roles: set):
        self.username, self.roles = username, roles


def create_app(rt, jwt_secret: str, token_ttl: int = 900, enable_docs: bool = False,
               limiter: auth.LoginLimiter | None = None) -> FastAPI:
    tokens = auth.TokenService(jwt_secret, token_ttl)
    limiter = limiter or auth.LoginLimiter()
    directory = auth.UserDirectory(rt.db)
    rt.policy.users = directory                      # policy and API share one live source of roles
    orch, lock = Orchestrator(rt), threading.RLock()
    app = FastAPI(title="MAeen FarmOS API", version="0.5.0",
                  description="SIMULATION-ONLY API for MAeen FarmOS slices 1-5.",
                  docs_url="/docs" if enable_docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if enable_docs else None)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
        if creds is None:
            raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
        try:
            username = tokens.verify(creds.credentials)
        except auth.AuthError:
            raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        roles = directory.get(username)
        if roles is None:
            raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        return User(username, roles)

    def need(*allowed: str):
        def dep(request: Request, user: User = Depends(current_user)) -> User:
            if not (user.roles & set(allowed)):
                with lock:
                    rt.audit.append(user.username, "api_forbidden", None,
                                    {"path": request.url.path, "method": request.method, "roles": sorted(user.roles)})
                raise HTTPException(403, "insufficient role")
            return user
        return dep

    def wf_or_404(wid: str) -> dict:
        if not WID.match(wid):
            raise HTTPException(404, "workflow not found")
        wf = orch.load(wid)
        if wf is None:
            raise HTTPException(404, "workflow not found")
        return wf

    def result(wf: dict, ok_codes=("workflow not awaiting",)) -> dict:
        err = wf.get("error")
        if err:
            if err.startswith(("approval denied", "denied")):
                raise HTTPException(403, err)
            raise HTTPException(409, err)
        return wf

    # ---- open endpoints ---------------------------------------------------------------------------------
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/auth/login")
    def login(body: Login):
        with lock:
            try:
                ok = auth.authenticate(rt.db, limiter, body.username, body.password)
            except auth.AuthError:
                rt.audit.append("anonymous", "login_locked", None, {"username": body.username})
                raise HTTPException(429, "too many failed attempts; try again later")
            rt.audit.append(body.username if ok else "anonymous", "login_ok" if ok else "login_failed", None,
                            {"username": body.username})
        if not ok:
            raise HTTPException(401, "invalid credentials")      # same message for unknown user / bad password
        return {"access_token": tokens.issue(body.username), "token_type": "bearer", "expires_in": tokens.ttl}

    # ---- authenticated read ------------------------------------------------------------------------------
    @app.get("/me")
    def me(user: User = Depends(current_user)):
        return {"username": user.username, "roles": sorted(user.roles)}

    @app.get("/status")
    def status(user: User = Depends(current_user)):
        return {"mode": rt.mode, "scenario": rt.cfg.scenario, "simulated_data": True,
                "parameters_validated": rt.cfg.parameters_validated, "assumptions": list(rt.cfg.assumptions),
                "allocation_m3_day": rt.cfg.allocation_m3_day, "allocation_verified": rt.cfg.allocation_verified,
                "system_time": rt.clock.now().isoformat()}

    @app.get("/workflows")
    def list_workflows(user: User = Depends(current_user)):
        with lock:
            rows = rt.db.execute("SELECT id,type,state,created_at,updated_at FROM workflow ORDER BY created_at, id").fetchall()
        return [dict(r) for r in rows]

    @app.get("/workflows/{wid}")
    def get_workflow(wid: str, lang: Literal["en", "ar"] = "en", user: User = Depends(current_user)):
        with lock:
            wf = wf_or_404(wid)
            text = explain(wf, lang) if wf["id"].startswith("wf-") else explain_anomaly(wf, lang)
        return {"workflow": wf, "explanation": text}

    @app.get("/reports/daily")
    def daily(lang: Literal["en", "ar"] = "en", format: Literal["json", "html", "text"] = "json",
              user: User = Depends(current_user)):
        with lock:
            rep = build_daily_report(rt)
        if format == "json":
            return rep
        if format == "html":
            return HTMLResponse(render_html(rep, lang), headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"})
        return Response(render_text(rep, lang), media_type="text/plain; charset=utf-8")

    @app.get("/anomaly-quality")
    def anomaly_quality(user: User = Depends(current_user)):
        with lock:
            return evaluate_anomaly_cases(rt.db)

    # ---- workflows (write) -------------------------------------------------------------------------------
    @app.post("/workflows/water-shortage", status_code=201)
    def start_water(body: StartWater, response: Response, user: User = Depends(need(*WORKFLOW_STARTERS))):
        with lock:
            wf = orch.start_water_shortage(body.event_id, requested_by=user.username)
        if wf.get("duplicate"):
            response.status_code = 200
        return wf

    @app.post("/workflows/anomaly", status_code=201)
    def start_anomaly(body: StartAnomaly, response: Response, user: User = Depends(need(*WORKFLOW_STARTERS))):
        if body.valve_id not in rt.cfg.valve_ids():
            raise HTTPException(422, f"unknown valve_id; configured: {rt.cfg.valve_ids()}")
        with lock:
            wf = orch.start_irrigation_anomaly(body.event_id, body.valve_id, body.expected_flow_m3h)
        if wf.get("duplicate"):
            response.status_code = 200
        return wf

    @app.post("/workflows/{wid}/approve")
    def approve(wid: str, user: User = Depends(current_user)):      # who approves = who is logged in; no body
        with lock:
            wf_or_404(wid)
            return result(orch.approve(wid, user.username))

    @app.post("/workflows/{wid}/reject")
    def reject(wid: str, body: Reject = Reject(), user: User = Depends(current_user)):
        with lock:
            wf_or_404(wid)
            return result(orch.reject(wid, user.username, body.reason))

    @app.post("/workflows/{wid}/confirm-outcome")
    def confirm(wid: str, body: Outcome, user: User = Depends(current_user)):
        with lock:
            wf_or_404(wid)
            return result(orch.resolve_anomaly(wid, user.username, body.outcome, body.note))

    # ---- audit -------------------------------------------------------------------------------------------
    @app.get("/audit")
    def audit_trail(correlation_id: Optional[str] = Query(default=None, max_length=64),
                    limit: int = Query(default=100, ge=1, le=500), user: User = Depends(need(*AUDIT_READERS))):
        with lock:
            return rt.audit.trail(correlation_id)[-limit:]

    @app.get("/audit/verify")
    def audit_verify(user: User = Depends(need(*AUDIT_READERS))):
        with lock:
            ok, bad = rt.audit.verify()
            return {"intact": ok, "first_bad_event": bad, "events": len(rt.audit.trail())}

    # ---- simulator control (admin, simulation mode only) ---------------------------------------------------
    @app.post("/admin/simulator/faults")
    def inject_fault(body: Fault, user: User = Depends(need("admin"))):
        if rt.mode != "simulation":
            raise HTTPException(409, "fault injection is only available in simulation mode")
        valves = set(rt.cfg.valve_ids())
        valid_sensor = lambda s: s in SENSORS or (s or "").startswith("flow:") and s[5:] in valves

        def num(lo, hi):
            if body.factor is None or not (lo <= body.factor <= hi) or math.isnan(body.factor):
                raise HTTPException(422, f"factor required in [{lo}, {hi}]")
            return str(body.factor)

        f = body.fault
        with lock:
            if f in ("leak", "leak_downstream"): rt.sim.inject(f, num(0, 500))
            elif f in ("pump_degraded", "filter_clogged"): rt.sim.inject(f, num(0.05, 1.0))
            elif f == "meter_mismatch": rt.sim.inject(f, num(0.05, 3.0))
            elif f == "pump_fault": rt.sim.inject(f)
            elif f in ("valve_stuck_closed", "valve_stuck_open"):
                if body.target not in valves: raise HTTPException(422, "target must be a configured valve")
                rt.sim.inject(f, body.target)
            elif f == "open_valve":
                if body.target not in valves: raise HTTPException(422, "target must be a configured valve")
                rt.sim.open_valve = body.target
            elif f in ("stale_sensor", "offline_sensor"):
                if not valid_sensor(body.target): raise HTTPException(422, "unknown sensor")
                rt.sim.inject(f, body.target)
            elif f == "sensor_bias":
                if not valid_sensor(body.target): raise HTTPException(422, "unknown sensor")
                rt.sim.inject(f, (body.target, num(0.05, 3.0)))
            rt.audit.append(user.username, "simulator_fault_injected", None,
                            {"fault": f, "target": body.target, "factor": body.factor, "simulated": True})
        return {"ok": True, "fault": f, "simulated": True}

    return app
