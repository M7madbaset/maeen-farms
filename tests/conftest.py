import pytest
from maeen.runtime import build_runtime
from maeen.orchestrator import Orchestrator


@pytest.fixture
def rt():
    return build_runtime("40")


@pytest.fixture
def orch(rt):
    return Orchestrator(rt)
