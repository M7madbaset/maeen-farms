"""Sensor data-quality rules. Missing/stale/out-of-range data is REPORTED, never
silently replaced with a fabricated value."""
from __future__ import annotations
import math


def validate_reading(r, now, max_age_s: float, rng):
    if r is None:
        return "missing", "no reading available"
    age = (now - r.ts).total_seconds()
    if age > max_age_s:
        return "stale", f"reading is {int(age)}s old (limit {int(max_age_s)}s)"
    if age < -60:
        return "future_timestamp", "timestamp is in the future"
    if rng is not None:
        lo, hi = rng
        if math.isnan(r.value) or not (lo <= r.value <= hi):
            return "out_of_range", f"value {r.value} outside [{lo}, {hi}]"
    if r.quality != "good":
        return "suspect", f"quality flag '{r.quality}'"
    return "ok", ""


def contradictory(a: float, b: float, tol: float) -> bool:
    m = max(abs(a), abs(b))
    return m > 0 and abs(a - b) / m > tol
