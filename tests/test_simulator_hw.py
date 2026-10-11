from maeen.runtime import build_runtime


def test_hw_dry_run_protection_independent_of_software():
    rt = build_runtime("40"); rt.sim.inject("pump_fault")
    assert rt.sim.command("V-B1", "run_valve_timed", 60)["reason"] == "hw_interlock_dry_run_protection"


def test_hw_blocks_second_valve_and_timer_limit():
    rt = build_runtime("40")
    assert rt.sim.command("V-B1", "run_valve_timed", 60)["ok"]
    assert not rt.sim.command("V-B2", "run_valve_timed", 60)["ok"]
    rt.sim.command("V-B1", "close_valve")
    assert rt.sim.command("V-B2", "run_valve_timed", 5000)["reason"] == "hw_interlock_timer_limit"


def test_all_readings_flagged_simulated_and_faults_visible():
    rt = build_runtime("40"); rt.sim.inject("offline_sensor", "eto_mm_day")
    assert rt.sim.read("eto_mm_day") is None
    assert rt.sim.read("well_flow_m3h").simulated is True
