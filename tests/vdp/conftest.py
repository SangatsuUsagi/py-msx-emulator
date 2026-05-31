import pytest
from msx.vdp.vdp import VDP


@pytest.fixture
def bare_vdp() -> VDP:
    return VDP()
