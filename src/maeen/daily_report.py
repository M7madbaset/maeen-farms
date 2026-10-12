"""Daily executive report (workflow K). Reads ONLY recorded system data. Sections whose
source agent does not exist yet are reported as 'not available' - never estimated or invented."""
from __future__ import annotations
import html, json
from .evaluation import evaluate_anomaly_cases

NOT_AVAILABLE = {
    "crop_observations": "Palm Health agent not implemented",
    "stock_alerts": "Procurement & Inventory agent not implemented",
    "spending_vs_budget": "Finance agent not implemented",
    "workforce_status": "Workforce agent not implemented",
    "maintenance_status": "Equipment & Maintenance agent not implemented",
    "production_forecast": "Yield Forecasting agent not implemented",
}


def _wfs(db):
    out = []
    for r in db.execute("SELECT id,type,state,data,created_at FROM workflow ORDER BY created_at, id"):
        out.append({"id": r["id"], "type": r["type"], "state": r["state"],
                    "created_at": r["created_at"], **json.loads(r["data"])})
    return out


def build_daily_report(rt) -> dict:
    db, wfs = rt.db, _wfs(rt.db)
    water, anomalies, pending, delivered, unverified = [], [], [], 0.0, 0
    for w in wfs:
        if w["type"] == "water_shortage":
            d = (w.get("plan") or {}).get("data") or {}
            got = sum(r["detail"].get("delivered_m3", 0.0) for r in w.get("results", []) if r.get("verified"))
            unverified += sum(1 for r in w.get("results", []) if not r.get("verified"))
            delivered += got
            water.append({"id": w["id"], "state": w["state"], "available_m3": d.get("available_m3"),
                          "demand_m3": d.get("total_demand_m3"), "shortage_m3": d.get("shortage_m3"),
                          "verified_delivered_m3": round(got, 3),
                          "blocked_reason": None if (w.get("plan") or {}).get("status") in (None, "ok")
                          else (w["plan"]["status"])})
            if w["state"] == "AWAITING_APPROVAL":
                pending.append({"id": w["id"], "kind": "plan_approval",
                                "actions": len(w["plan"]["proposed_actions"]),
                                "shortage_m3": d.get("shortage_m3")})
        elif w["type"] == "irrigation_anomaly":
            d = (w.get("plan") or {}).get("data") or {}
            anomalies.append({"id": w["id"], "state": w["state"], "classification": d.get("classification"),
                              "severity": d.get("severity"), "confidence": (w.get("plan") or {}).get("confidence"),
                              "safe_state": (w.get("safe_state") or {}).get("status"),
                              "blocked_reason": None if (w.get("plan") or {}).get("status") in (None, "ok")
                              else w["plan"]["status"]})
            if w["state"] == "AWAITING_INSPECTION":
                pending.append({"id": w["id"], "kind": "inspection_confirmation"})
    notes = [dict(r) for r in db.execute("SELECT severity,text_en,text_ar,simulated FROM notification ORDER BY id")]
    sev = {}
    for n in notes: sev[n["severity"]] = sev.get(n["severity"], 0) + 1
    ev_types = [e["event_type"] for e in rt.audit.trail()]
    ok, bad_id = rt.audit.verify()
    states = {}
    for w in wfs: states[w["state"]] = states.get(w["state"], 0) + 1
    return {
        "meta": {"generated_at": rt.clock.now().isoformat(), "mode": rt.mode, "scenario": rt.cfg.scenario,
                 "simulated": True, "parameters_validated": rt.cfg.parameters_validated,
                 "assumptions": list(rt.cfg.assumptions)},
        "workflows": {"total": len(wfs), "by_state": states},
        "water": {"plans": water, "verified_delivered_m3": round(delivered, 3),
                  "unverified_or_failed_commands": unverified},
        "irrigation_exceptions": anomalies,
        "pending_decisions": pending,
        "alerts": {"by_severity": sev, "critical": [n for n in notes if n["severity"] == "critical"]},
        "governance": {"approvals_granted": ev_types.count("plan_approved"),
                       "approvals_denied": ev_types.count("approval_denied") + ev_types.count("outcome_denied"),
                       "commands_denied": ev_types.count("command_denied") + ev_types.count("safe_close_denied")
                       + ev_types.count("tool_denied"),
                       "safe_close_events": ev_types.count("safe_close_verified"),
                       "safe_close_failures": ev_types.count("safe_close_FAILED")},
        "anomaly_quality": evaluate_anomaly_cases(db),
        "audit": {"events": len(ev_types), "chain_intact": ok, "first_bad_event": bad_id},
        "not_available": dict(NOT_AVAILABLE),
    }


L = {
 "en": {"title": "MAeen Daily Executive Report", "sim": "SIMULATED DATA - not real farm measurements",
        "overview": "Overview", "water": "Water", "exc": "Irrigation exceptions", "pend": "Pending decisions",
        "alerts": "Alerts", "gov": "Governance & audit", "na": "Sections without a data source",
        "assump": "Assumptions (unvalidated)", "none": "none", "mode": "Mode", "scenario": "Scenario",
        "gen": "Generated", "deliv": "Verified water delivered (m3)", "avail": "available", "dem": "demand",
        "short": "shortage", "chain": "Audit chain intact", "events": "Audit events", "yes": "yes", "no": "NO"},
 "ar": {"title": "التقرير التنفيذي اليومي - معين", "sim": "بيانات محاكاة - ليست قياسات حقيقية للمزرعة",
        "overview": "نظرة عامة", "water": "المياه", "exc": "استثناءات الري", "pend": "قرارات معلقة",
        "alerts": "التنبيهات", "gov": "الحوكمة والتدقيق", "na": "أقسام بلا مصدر بيانات",
        "assump": "الافتراضات (غير معتمدة)", "none": "لا يوجد", "mode": "الوضع", "scenario": "السيناريو",
        "gen": "وقت الإنشاء", "deliv": "المياه المسلّمة والمتحقق منها (م³)", "avail": "المتاح", "dem": "الاحتياج",
        "short": "العجز", "chain": "سلسلة التدقيق سليمة", "events": "أحداث التدقيق", "yes": "نعم", "no": "لا"},
}


def _sections(r: dict, lang: str):
    t = L[lang]; col = "text_ar" if lang == "ar" else "text_en"
    s = []
    s.append((t["overview"], [f"{t['mode']}: {r['meta']['mode']}", f"{t['scenario']}: {r['meta']['scenario']}",
                              f"{t['gen']}: {r['meta']['generated_at']}",
                              "workflows: " + ", ".join(f"{k}={v}" for k, v in r["workflows"]["by_state"].items())]))
    w = [f"{t['deliv']}: {r['water']['verified_delivered_m3']:.1f}"]
    for p in r["water"]["plans"]:
        if p["available_m3"] is None:
            w.append(f"{p['id']}: {p['state']} ({p['blocked_reason']})")
        else:
            w.append(f"{p['id']}: {p['state']} - {t['avail']} {p['available_m3']:.0f}, {t['dem']} "
                     f"{p['demand_m3']:.0f}, {t['short']} {p['shortage_m3']:.0f}")
    s.append((t["water"], w))
    s.append((t["exc"], [f"{a['id']}: {a['state']} - {a['classification'] or a['blocked_reason']}"
                         + (f" ({a['severity']}, {a['confidence']:.0%})" if a['confidence'] else "")
                         + (f" safe-close={a['safe_state']}" if a['safe_state'] else "")
                         for a in r["irrigation_exceptions"]] or [t["none"]]))
    s.append((t["pend"], [f"{p['id']}: {p['kind']}" for p in r["pending_decisions"]] or [t["none"]]))
    s.append((t["alerts"], [f"{k}: {v}" for k, v in r["alerts"]["by_severity"].items()]
              + [f"[critical] {n[col]}" for n in r["alerts"]["critical"]] or [t["none"]]))
    g = r["governance"]
    s.append((t["gov"], [f"approvals granted/denied: {g['approvals_granted']}/{g['approvals_denied']}",
                         f"commands denied: {g['commands_denied']}",
                         f"safe-close ok/failed: {g['safe_close_events']}/{g['safe_close_failures']}",
                         f"{t['events']}: {r['audit']['events']}",
                         f"{t['chain']}: {t['yes'] if r['audit']['chain_intact'] else t['no']}"]))
    s.append((t["na"], [f"{k}: {v}" for k, v in r["not_available"].items()]))
    s.append((t["assump"], r["meta"]["assumptions"]))
    return t, s


def render_text(r: dict, lang: str = "en") -> str:
    t, secs = _sections(r, lang)
    out = [t["title"], "!! " + t["sim"], ""]
    for h, items in secs:
        out.append(f"== {h}")
        out += [f"  - {i}" for i in items]
    return "\n".join(out)


def render_html(r: dict, lang: str = "en") -> str:
    t, secs = _sections(r, lang)
    e = html.escape            # every dynamic string is escaped: notifications may contain untrusted text
    body = "".join(f"<h2>{e(h)}</h2><ul>" + "".join(f"<li>{e(str(i))}</li>" for i in items) + "</ul>"
                   for h, items in secs)
    d = "rtl" if lang == "ar" else "ltr"
    return (f'<!doctype html><html lang="{lang}" dir="{d}"><head><meta charset="utf-8">'
            f"<title>{e(t['title'])}</title><style>body{{font-family:system-ui,'Noto Naskh Arabic',sans-serif;"
            "max-width:820px;margin:2rem auto;padding:0 1rem;line-height:1.6}"
            ".sim{background:#b00020;color:#fff;padding:.5rem 1rem;font-weight:bold}h2{border-bottom:1px solid #ccc}"
            f"</style></head><body><h1>{e(t['title'])}</h1><div class='sim'>{e(t['sim'])}</div>{body}</body></html>")
