"""Farm configuration. EVERY agronomic number here is a PLACEHOLDER that must be
validated by a qualified agronomist before operational use (see `assumptions`)."""
from __future__ import annotations
from dataclasses import dataclass, field

FEDDAN_M2 = 4200.83   # 1 feddan = 4200.83 m2 (standard Egyptian unit)

SIMULATION, ADVISORY, CONTROLLED = "simulation", "advisory", "controlled"
MODES = (SIMULATION, ADVISORY, CONTROLLED)


@dataclass(frozen=True)
class BlockCfg:
    block_id: str
    area_feddan: float
    palms_per_feddan: int
    stage: str            # key into kc_by_stage
    priority: int         # 1 = served first (illustrative; agronomist sets this)
    valve_id: str
    rated_flow_m3h: float
    min_fraction: float   # floor share of demand served before higher fills

    @property
    def palm_count(self) -> int:
        return round(self.area_feddan * self.palms_per_feddan)

    @property
    def area_m2(self) -> float:
        return self.area_feddan * FEDDAN_M2


@dataclass
class FarmConfig:
    scenario: str
    blocks: tuple
    kc_by_stage: dict
    irrigation_efficiency: float = 0.85
    ecw_dsm: float = 2.0
    ece_threshold_dsm: float = 4.0
    apply_leaching: bool = True
    allocation_m3_day: float | None = None
    allocation_verified: bool = False          # unverified => NOT used as availability
    pump_hours_available: float = 20.0
    max_data_age_s: int = 900
    flow_tolerance: float = 0.15               # well-vs-meter contradiction threshold
    verify_tolerance: float = 0.25             # commanded-vs-measured flow/volume
    max_valve_minutes: int = 720               # software safety limit (gateway)
    command_ttl_s: int = 900
    approval_ttl_s: int = 3600
    min_well_flow_m3h: float = 0.5
    parameters_validated: bool = False
    commissioned_valves: frozenset = frozenset()   # none until Phase 5
    sensor_ranges: dict = field(default_factory=lambda: {
        "well_flow_m3h": (0.0, 500.0), "main_meter_m3h": (0.0, 500.0),
        "eto_mm_day": (0.0, 15.0), "flow:": (0.0, 500.0), "volume_m3:": (0.0, 1e7)})
    assumptions: tuple = (
        "Kc values are illustrative placeholders, not validated for Minya Medjool.",
        "Demand is area-based (ETc x area); palm-canopy/age effects are not yet modelled.",
        "Leaching requirement uses the FAO-29 style LR=ECw/(5*ECe-ECw), unvalidated.",
        "Block priorities and minimum fractions are illustrative; agronomist must set them.",
        "Palms per feddan = 50 (planning assumption, configurable per block).",
    )

    def block(self, block_id: str) -> BlockCfg:
        return next(b for b in self.blocks if b.block_id == block_id)

    def valve_ids(self):
        return [b.valve_id for b in self.blocks]

    def range_for(self, sensor_id: str):
        if sensor_id in self.sensor_ranges:
            return self.sensor_ranges[sensor_id]
        for prefix in ("flow:", "volume_m3:"):
            if sensor_id.startswith(prefix):
                return self.sensor_ranges[prefix]
        return None


KC = {"mature": 0.95, "juvenile": 0.70, "young": 0.45}   # PLACEHOLDERS
FLOOR = {"mature": 0.60, "juvenile": 0.55, "young": 0.50}


def _mk(i, area, stage, prio):
    return BlockCfg(f"B{i}", area, 50, stage, prio, f"V-B{i}", 150.0, FLOOR[stage])


def build_config(scenario: str = "40") -> FarmConfig:
    if scenario == "40":
        blocks = (_mk(1, 10, "mature", 1), _mk(2, 10, "mature", 2),
                  _mk(3, 10, "juvenile", 3), _mk(4, 10, "young", 4))
        return FarmConfig("40-feddan", blocks, dict(KC))
    if scenario == "142":
        stages = ["mature"] * 3 + ["juvenile"] * 2 + ["young"] * 2
        areas = [20, 20, 20, 20, 20, 20, 22]
        blocks = tuple(_mk(i + 1, areas[i], stages[i], i + 1) for i in range(7))
        # 1,800 m3/day is a DISCUSSED scenario input, unverified -> not an availability cap
        return FarmConfig("142-feddan", blocks, dict(KC),
                          allocation_m3_day=1800.0, allocation_verified=False)
    raise ValueError("scenario must be '40' or '142'")


USERS = {  # seed users; real authentication is NOT implemented in slice 1
    "owner1": {"owner"}, "agro1": {"agronomist"},
    "op1": {"irrigation_operator"}, "aud1": {"auditor"}, "fin1": {"finance_officer"},
}
