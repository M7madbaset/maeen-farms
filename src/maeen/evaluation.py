"""Quality evaluation from HUMAN-CONFIRMED outcomes only (never from model output)."""
from __future__ import annotations

FAMILY = {"leak": "leak_confirmed", "sensor": "sensor_fault_confirmed", "equipment": "equipment_fault_confirmed"}


def family(predicted: str) -> str:
    if predicted.startswith("leak"): return "leak"
    if predicted.startswith("sensor"): return "sensor"
    if predicted.startswith("equipment"): return "equipment"
    return "other"


def evaluate_anomaly_cases(db) -> dict:
    rows = db.execute("SELECT predicted, confirmed FROM anomaly_case WHERE confirmed IS NOT NULL").fetchall()
    total, correct, false_alarms = len(rows), 0, 0
    by_family: dict = {}
    for r in rows:
        f = family(r["predicted"])
        st = by_family.setdefault(f, {"cases": 0, "correct": 0, "false_alarm": 0})
        st["cases"] += 1
        if r["confirmed"] == "false_alarm":
            false_alarms += 1; st["false_alarm"] += 1
        elif FAMILY.get(f) == r["confirmed"]:
            correct += 1; st["correct"] += 1
    pending = db.execute("SELECT COUNT(*) c FROM anomaly_case WHERE confirmed IS NULL").fetchone()["c"]
    return {"confirmed_cases": total, "pending_confirmation": pending,
            "accuracy": (correct / total) if total else None,
            "false_alarm_rate": (false_alarms / total) if total else None,
            "by_family": by_family,
            "note": "Small samples; not statistically meaningful until many cases are confirmed."}
