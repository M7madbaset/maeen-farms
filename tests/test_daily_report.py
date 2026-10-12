import json
from maeen.runtime import build_runtime
from maeen.orchestrator import Orchestrator
from maeen.daily_report import build_daily_report, render_html, render_text, NOT_AVAILABLE


def busy_day():
    rt = build_runtime("40"); o = Orchestrator(rt)
    w1 = o.start_water_shortage("e1"); o.approve(w1["id"], "owner1")
    o.start_water_shortage("e2")
    rt.sim.open_valve = "V-B1"; rt.sim.inject("leak", "25")
    o.start_irrigation_anomaly("e3", "V-B1", 45.0)
    return rt, o


def test_empty_day_has_no_fabricated_numbers():
    rep = build_daily_report(build_runtime("40"))
    assert rep["workflows"]["total"] == 0 and rep["water"]["plans"] == []
    assert rep["water"]["verified_delivered_m3"] == 0 and rep["pending_decisions"] == []
    assert rep["meta"]["simulated"] is True and rep["audit"]["chain_intact"] is True


def test_report_numbers_match_recorded_system_state():
    rt, _ = busy_day(); rep = build_daily_report(rt)
    assert rep["water"]["verified_delivered_m3"] == round(sum(rt.sim.delivered.values()), 3)
    kinds = {(p["kind"]) for p in rep["pending_decisions"]}
    assert kinds == {"plan_approval", "inspection_confirmation"}
    exc = rep["irrigation_exceptions"][0]
    assert exc["classification"] == "leak_upstream_of_valve" and exc["safe_state"] == "verified"
    assert rep["governance"]["safe_close_events"] == 1 and rep["governance"]["approvals_granted"] == 1
    assert rep["alerts"]["by_severity"]["critical"] == 2


def test_failures_and_denials_surface_in_report():
    rt = build_runtime("40"); o = Orchestrator(rt)
    rt.sim.inject("valve_stuck_closed", "V-B2")
    w = o.start_water_shortage("e1"); o.approve(w["id"], "op1")        # denied
    o.approve(w["id"], "owner1")                                          # fails verification
    rep = build_daily_report(rt)
    assert rep["governance"]["approvals_denied"] == 1
    assert rep["water"]["unverified_or_failed_commands"] == 1
    assert rep["workflows"]["by_state"].get("FAILED_VERIFICATION") == 1


def test_sections_without_agents_are_declared_not_invented():
    rep = build_daily_report(busy_day()[0])
    assert set(rep["not_available"]) == set(NOT_AVAILABLE)
    txt = render_text(rep, "en")
    assert "Finance agent not implemented" in txt and "budget" not in txt.lower().replace("spending_vs_budget", "")


def test_tamper_detected_in_report():
    rt, _ = busy_day()
    rt.db.execute("DROP TRIGGER audit_no_update")
    rt.db.execute("UPDATE audit_event SET actor='evil' WHERE id=3")
    rep = build_daily_report(rt)
    assert rep["audit"]["chain_intact"] is False and rep["audit"]["first_bad_event"] == 3
    assert "NO" in render_text(rep, "en")


def test_arabic_html_is_rtl_with_intact_text_and_numbers():
    rep = build_daily_report(busy_day()[0]); h = render_html(rep, "ar")
    assert 'dir="rtl"' in h and 'lang="ar"' in h and "بيانات محاكاة" in h
    assert "900" in h and "المياه" in h
    assert 'dir="ltr"' in render_html(rep, "en")


def test_untrusted_notification_text_cannot_inject_html():
    rt = build_runtime("40")
    rt.db.execute("INSERT INTO notification(ts,workflow_id,audience,severity,text_en,text_ar,simulated)"
                  " VALUES ('t','w','a','critical','<script>alert(1)</script>','<img src=x onerror=1>',1)")
    rt.db.commit()
    for lang in ("en", "ar"):
        h = render_html(build_daily_report(rt), lang)
        assert "<script>" not in h and "<img" not in h and "&lt;" in h


def test_report_is_json_serialisable_and_labelled_simulated():
    rep = build_daily_report(busy_day()[0])
    json.dumps(rep, ensure_ascii=False)
    assert "SIMULATED" in render_text(rep, "en") and "محاكاة" in render_text(rep, "ar")
