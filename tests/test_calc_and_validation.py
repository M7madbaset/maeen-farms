import math, pytest
from datetime import timedelta
from maeen.config import build_config, FEDDAN_M2
from maeen.calc.water import block_demand, allocate, leaching_requirement
from maeen.validation import validate_reading, contradictory
from maeen.models import Reading
from maeen.simulator import SimClock


def test_leaching_requirement_known_value():
    assert leaching_requirement(2.0, 4.0) == pytest.approx(2 / 18)


def test_leaching_invalid_inputs():
    with pytest.raises(ValueError):
        leaching_requirement(20.0, 4.0)


def test_block_demand_hand_calculation():
    cfg = build_config("40"); b = cfg.blocks[0]
    d = block_demand(b, 7.5, cfg)
    net = 7.5 * 0.95 / 1000 * 10 * FEDDAN_M2
    assert d.net_m3 == pytest.approx(net)
    assert d.gross_m3 == pytest.approx(net / 0.85 / (1 - 2 / 18))


def test_unknown_stage_is_an_error_not_a_default():
    cfg = build_config("40"); cfg.kc_by_stage.pop("mature")
    with pytest.raises(ValueError):
        block_demand(cfg.blocks[0], 7.5, cfg)


def test_rain_reduces_demand_and_never_negative():
    cfg = build_config("40"); b = cfg.blocks[0]
    assert block_demand(b, 7.5, cfg, 100).gross_m3 == 0


@pytest.mark.parametrize("avail", [0, 100, 500, 900, 1272, 5000])
def test_allocation_invariants(avail):
    cfg = build_config("40")
    dem = {b.block_id: block_demand(b, 7.5, cfg).gross_m3 for b in cfg.blocks}
    alloc, rem = allocate(dem, cfg.blocks, avail)
    assert sum(alloc.values()) <= avail + 1e-6
    assert all(0 <= alloc[k] <= dem[k] + 1e-6 for k in dem)
    assert rem >= -1e-6
    if avail >= sum(dem.values()):
        assert all(alloc[k] == pytest.approx(dem[k]) for k in dem)


def test_priority_order_when_scarce():
    cfg = build_config("40")
    dem = {b.block_id: block_demand(b, 7.5, cfg).gross_m3 for b in cfg.blocks}
    alloc, _ = allocate(dem, cfg.blocks, 900)
    assert alloc["B1"] / dem["B1"] >= alloc["B4"] / dem["B4"]


def test_142_scenario_blocks_sum_to_142_feddan_and_7100_palms():
    cfg = build_config("142")
    assert sum(b.area_feddan for b in cfg.blocks) == 142
    assert sum(b.palm_count for b in cfg.blocks) == 7100
    assert cfg.allocation_m3_day == 1800 and cfg.allocation_verified is False


def test_validation_statuses():
    clock = SimClock(); now = clock.now()
    mk = lambda v=1.0, age=0, q="good": Reading("x", v, "u", now - timedelta(seconds=age), q)
    assert validate_reading(None, now, 900, (0, 10))[0] == "missing"
    assert validate_reading(mk(age=5000), now, 900, (0, 10))[0] == "stale"
    assert validate_reading(mk(v=99), now, 900, (0, 10))[0] == "out_of_range"
    assert validate_reading(mk(v=math.nan), now, 900, (0, 10))[0] == "out_of_range"
    assert validate_reading(mk(q="suspect"), now, 900, (0, 10))[0] == "suspect"
    assert validate_reading(mk(), now, 900, (0, 10))[0] == "ok"


def test_contradiction():
    assert contradictory(45, 22, 0.15) and not contradictory(45, 44, 0.15)
    assert not contradictory(0, 0, 0.15)
