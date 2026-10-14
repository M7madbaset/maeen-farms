import pytest
from maeen import auth

auth.SCRYPT_N = 2 ** 10      # fast hashes for tests only; production default is 2**14
from maeen.runtime import build_runtime
from maeen.orchestrator import Orchestrator


@pytest.fixture
def rt():
    return build_runtime("40")


@pytest.fixture
def orch(rt):
    return Orchestrator(rt)
