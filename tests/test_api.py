import pytest
pytest.importorskip("jwt")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient
from maeen import auth
from maeen.api import create_app, PUBLIC_PATHS
from maeen.runtime import build_runtime

SECRET, PW = "k" * 40, "correct-horse-battery"
USERS = {"owner1": ["owner"], "agro1": ["agronomist"], "op1": ["irrigation_operator"],
         "aud1": ["auditor"], "fin1": ["finance_officer"], "admin1": ["admin"]}


@pytest.fixture
def env():
    rt = build_runtime("40")
    for u, r in USERS.items(): auth.create_user(rt.db, u, PW, r)
    app = create_app(rt, SECRET)
    return rt, app, TestClient(app)


def login(c, user, pw=PW):
    r = c.post("/auth/login", json={"username": user, "password": pw})
    return {"Authorization": "Bearer " + r.json()["access_token"]} if r.status_code == 200 else r


@pytest.fixture
def H(env):
    _, _, c = env
    return {u: login(c, u) for u in USERS}


# ---- authentication -------------------------------------------------------------------------------------
def test_every_route_except_health_and_login_requires_authentication(env):
    rt, app, c = env
    seen = 0
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or path in PUBLIC_PATHS or path.startswith(("/docs", "/openapi", "/redoc")): continue
        for m in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            url = path.replace("{wid}", "wf-0123456789")
            r = c.request(m, url, json={} if m == "POST" else None)
            assert r.status_code == 401, f"{m} {path} -> {r.status_code}"
            assert r.headers["www-authenticate"] == "Bearer"; seen += 1
    assert seen >= 14
    assert c.get("/health").status_code == 200


def test_login_failures_are_indistinguishable_and_lock_out(env):
    rt, app, c = env
    a = c.post("/auth/login", json={"username": "owner1", "password": "wrong-password-123"})
    b = c.post("/auth/login", json={"username": "nobody", "password": "wrong-password-123"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    for _ in range(5): c.post("/auth/login", json={"username": "owner1", "password": "bad-bad-bad-bad"})
    assert c.post("/auth/login", json={"username": "owner1", "password": PW}).status_code == 429


def test_bad_tokens_rejected(env, H):
    rt, app, c = env
    good = H["owner1"]["Authorization"]
    assert c.get("/me", headers={"Authorization": good}).status_code == 200
    for bad in [good[:-4] + "xxxx", "Bearer garbage", "Basic abc", "Bearer "]:
        assert c.get("/me", headers={"Authorization": bad}).status_code == 401


def test_expired_token_rejected():
    rt = build_runtime("40"); auth.create_user(rt.db, "owner1", PW, ["owner"])
    c = TestClient(create_app(rt, SECRET, token_ttl=-5))
    h = login(c, "owner1")
    assert c.get("/me", headers=h).status_code == 401


def test_roles_are_live_not_from_token(env, H):
    rt, app, c = env
    assert c.post("/workflows/water-shortage", json={"event_id": "x1"}, headers=H["op1"]).status_code == 201
    auth.set_roles(rt.db, "op1", ["auditor"])                        # revoke role; same token
    assert c.post("/workflows/water-shortage", json={"event_id": "x2"}, headers=H["op1"]).status_code == 403
    auth.set_active(rt.db, "op1", False)
    assert c.get("/me", headers=H["op1"]).status_code == 401         # deactivated => token dead


def test_security_headers_and_no_docs_by_default(env, H):
    rt, app, c = env
    r = c.get("/me", headers=H["owner1"])
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_weak_jwt_secret_refuses_to_start():
    with pytest.raises(ValueError): create_app(build_runtime("40"), "short")


# ---- end-to-end workflow over HTTP --------------------------------------------------------------------------
def test_definition_of_done_flow_over_http(env, H):
    rt, app, c = env
    r = c.post("/workflows/water-shortage", json={"event_id": "evt-1"}, headers=H["owner1"])
    assert r.status_code == 201 and r.json()["state"] == "AWAITING_APPROVAL"
    wid = r.json()["id"]
    assert sum(rt.sim.delivered.values()) == 0                                     # nothing happens before approval
    # unauthorised approver is refused and nothing moves
    assert c.post(f"/workflows/{wid}/approve", headers=H["op1"]).status_code == 403
    assert c.post(f"/workflows/{wid}/approve", headers=H["aud1"]).status_code == 403
    assert sum(rt.sim.delivered.values()) == 0
    # the approver cannot be spoofed through the body
    assert c.post(f"/workflows/{wid}/approve", json={"user_id": "owner1"}, headers=H["op1"]).status_code == 403
    done = c.post(f"/workflows/{wid}/approve", headers=H["agro1"])
    assert done.status_code == 200 and done.json()["state"] == "COMPLETED" and done.json()["approved_by"] == "agro1"
    assert c.post(f"/workflows/{wid}/approve", headers=H["owner1"]).status_code == 409      # no double execution
    det = c.get(f"/workflows/{wid}?lang=ar", headers=H["aud1"]).json()
    assert "المتاح" in det["explanation"] and det["workflow"]["results"][0]["simulated"] is True
    v = c.get("/audit/verify", headers=H["aud1"]).json()
    assert v["intact"] is True and v["events"] > 10
    trail = c.get(f"/audit?correlation_id={wid}", headers=H["aud1"]).json()
    types = {e["event_type"] for e in trail}
    assert {"plan_approved", "device_command_sent", "result_verified"} <= types
    denied = [e for e in c.get("/audit?limit=500", headers=H["owner1"]).json() if e["event_type"] == "approval_denied"]
    assert {e["actor"] for e in denied} == {"op1", "aud1"}                          # refusals are attributed and kept
    c.post("/workflows/water-shortage", json={"event_id": "forbidden-probe"}, headers=H["fin1"])
    assert any(e["event_type"] == "api_forbidden" and e["actor"] == "fin1"
               for e in c.get("/audit?limit=500", headers=H["owner1"]).json())


def test_role_matrix_for_writes_and_audit(env, H):
    rt, app, c = env
    for u in ("aud1", "fin1"):
        assert c.post("/workflows/water-shortage", json={"event_id": "z"}, headers=H[u]).status_code == 403
    for u in ("op1", "agro1", "fin1"):
        assert c.get("/audit", headers=H[u]).status_code == 403
        assert c.get("/audit/verify", headers=H[u]).status_code == 403
    for u in ("owner1", "aud1", "admin1"):
        assert c.get("/audit/verify", headers=H[u]).status_code == 200
    assert c.get("/workflows", headers=H["fin1"]).status_code == 200


def test_duplicate_event_is_idempotent_over_http(env, H):
    rt, app, c = env
    a = c.post("/workflows/water-shortage", json={"event_id": "dup"}, headers=H["owner1"])
    b = c.post("/workflows/water-shortage", json={"event_id": "dup"}, headers=H["op1"])
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(c.get("/workflows", headers=H["owner1"]).json()) == 1


def test_reject_and_confirm_paths(env, H):
    rt, app, c = env
    wid = c.post("/workflows/water-shortage", json={"event_id": "r1"}, headers=H["owner1"]).json()["id"]
    assert c.post(f"/workflows/{wid}/reject", json={"reason": "wait"}, headers=H["op1"]).status_code in (403, 409)
    assert c.post(f"/workflows/{wid}/reject", json={"reason": "wait"}, headers=H["owner1"]).json()["state"] == "REJECTED"
    # anomaly + human confirmation
    c.post("/admin/simulator/faults", json={"fault": "open_valve", "target": "V-B1"}, headers=H["admin1"])
    c.post("/admin/simulator/faults", json={"fault": "leak", "factor": 15}, headers=H["admin1"])
    an = c.post("/workflows/anomaly", json={"event_id": "a1", "valve_id": "V-B1", "expected_flow_m3h": 45}, headers=H["op1"])
    assert an.status_code == 201 and an.json()["state"] == "AWAITING_INSPECTION"
    aid = an.json()["id"]
    assert c.post(f"/workflows/{aid}/confirm-outcome", json={"outcome": "leak_confirmed"}, headers=H["aud1"]).status_code == 403
    assert c.post(f"/workflows/{aid}/confirm-outcome", json={"outcome": "leak_confirmed", "note": "cracked"}, headers=H["op1"]).json()["state"] == "RESOLVED"
    q = c.get("/anomaly-quality", headers=H["aud1"]).json()
    assert q["confirmed_cases"] == 1 and q["accuracy"] == 1.0


# ---- input validation -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("body", [{"event_id": ""}, {"event_id": "a b"}, {"event_id": "x" * 101},
                                  {"event_id": "<script>"}, {"event_id": "ok", "extra": 1}, {}])
def test_bad_event_ids_rejected(env, H, body):
    assert env[2].post("/workflows/water-shortage", json=body, headers=H["owner1"]).status_code == 422


@pytest.mark.parametrize("body", [
    {"event_id": "a", "valve_id": "V-B1", "expected_flow_m3h": -1},
    {"event_id": "a", "valve_id": "V-B1", "expected_flow_m3h": 0},
    {"event_id": "a", "valve_id": "V-B1", "expected_flow_m3h": 99999},
    {"event_id": "a", "valve_id": "V-NOPE", "expected_flow_m3h": 45},
    {"event_id": "a", "valve_id": "V-B1"}])
def test_bad_anomaly_inputs_rejected(env, H, body):
    assert env[2].post("/workflows/anomaly", json=body, headers=H["owner1"]).status_code == 422


@pytest.mark.parametrize("wid", ["wf-zzz", "../../etc/passwd", "wf-0123456789", "an-0000000000", "x" * 300])
def test_unknown_or_malicious_workflow_ids_are_404(env, H, wid):
    c = env[2]
    assert c.get(f"/workflows/{wid}", headers=H["owner1"]).status_code in (404, 422)
    assert c.post(f"/workflows/{wid}/approve", headers=H["owner1"]).status_code in (404, 422)


# ---- simulator control ----------------------------------------------------------------------------------------
def test_fault_injection_admin_only_validated_and_audited(env, H):
    rt, app, c = env
    f = {"fault": "leak", "factor": 10}
    for u in ("owner1", "op1", "aud1"):
        assert c.post("/admin/simulator/faults", json=f, headers=H[u]).status_code == 403
    assert c.post("/admin/simulator/faults", json=f, headers=H["admin1"]).status_code == 200
    for bad in [{"fault": "leak"}, {"fault": "leak", "factor": -1}, {"fault": "leak", "factor": 1e9},
                {"fault": "valve_stuck_open", "target": "V-X"}, {"fault": "stale_sensor", "target": "evil"},
                {"fault": "rm -rf"}, {"fault": "pump_degraded", "factor": 0}]:
        assert c.post("/admin/simulator/faults", json=bad, headers=H["admin1"]).status_code == 422, bad
    assert any(e["event_type"] == "simulator_fault_injected" for e in rt.audit.trail())


def test_fault_injection_unavailable_outside_simulation_mode():
    rt = build_runtime("40", mode="advisory"); auth.create_user(rt.db, "admin1", PW, ["admin"])
    c = TestClient(create_app(rt, SECRET)); h = login(c, "admin1")
    assert c.post("/admin/simulator/faults", json={"fault": "pump_fault"}, headers=h).status_code == 409


def test_bad_data_blocks_via_api_and_never_guesses(env, H):
    rt, app, c = env
    c.post("/admin/simulator/faults", json={"fault": "stale_sensor", "target": "well_flow_m3h"}, headers=H["admin1"])
    r = c.post("/workflows/water-shortage", json={"event_id": "b1"}, headers=H["owner1"]).json()
    assert r["state"] == "BLOCKED" and r["plan"]["proposed_actions"] == []


# ---- reports ----------------------------------------------------------------------------------------------------
def test_reports_in_all_formats_with_csp(env, H):
    rt, app, c = env
    c.post("/workflows/water-shortage", json={"event_id": "rep"}, headers=H["owner1"])
    assert c.get("/reports/daily", headers=H["fin1"]).json()["meta"]["simulated"] is True
    html = c.get("/reports/daily?format=html&lang=ar", headers=H["aud1"])
    assert 'dir="rtl"' in html.text and "default-src 'none'" in html.headers["content-security-policy"]
    assert "SIMULATED" in c.get("/reports/daily?format=text", headers=H["aud1"]).text
    assert c.get("/reports/daily?format=xml", headers=H["aud1"]).status_code == 422
    assert c.get("/status", headers=H["aud1"]).json()["simulated_data"] is True
