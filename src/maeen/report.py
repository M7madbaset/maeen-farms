"""Seven-part recommendation explanation, English and Arabic."""
from __future__ import annotations


def explain(wf: dict, lang: str = "en") -> str:
    p = wf.get("plan") or {}
    d = p.get("data", {})
    ar = lang == "ar"
    if not d:
        return ("لا توجد خطة." if ar else "No plan produced.") + " " + "; ".join(p.get("warnings", []))
    acts = p.get("proposed_actions", [])
    res = wf.get("results", [])
    ev = ", ".join(f"{e['sensor_id']}={e['value']:.2f}{e['unit']}" for e in p["evidence"])
    steps = "; ".join(f"{a['block_id']}: {a['minutes']:.0f} {'دقيقة' if ar else 'min'} / {a['planned_volume_m3']:.0f} m3" for a in acts)
    out = ", ".join(f"{r['block']}={r['status']}" for r in res) or ("لم يُنفذ بعد" if ar else "not executed")
    if ar:
        return "\n".join([
            f"1) الملاحظة: المتاح {d['available_m3']:.0f} م³ مقابل احتياج {d['total_demand_m3']:.0f} م³ (عجز {d['shortage_m3']:.0f} م³).",
            f"2) الأدلة: {ev}",
            f"3) الإجراء المقترح: {steps or 'لا يوجد'}",
            "4) الفائدة/المخاطر: توزيع المياه حسب الأولوية؛ خطر إجهاد مائي في الكتل الأقل أولوية.",
            f"5) الثقة: {p['confidence']:.0%} (المعاملات الزراعية غير معتمدة بعد: {'نعم' if p['warnings'] else 'لا'}).",
            f"6) الموافقة مطلوبة: {'نعم' if p['requires_approval'] else 'لا'}",
            f"7) نتيجة التنفيذ: {out}"])
    return "\n".join([
        f"1) Observed: available {d['available_m3']:.0f} m3 vs demand {d['total_demand_m3']:.0f} m3 (shortage {d['shortage_m3']:.0f} m3).",
        f"2) Evidence: {ev}",
        f"3) Proposed action: {steps or 'none'}",
        "4) Benefit / downside: water allocated by priority; lower-priority blocks bear the deficit (stress risk).",
        f"5) Confidence: {p['confidence']:.0%}; warnings: {'; '.join(p['warnings']) or 'none'}",
        f"6) Approval required: {'yes' if p['requires_approval'] else 'no'}",
        f"7) Outcome: {out}"])
