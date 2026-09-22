from dataclasses import dataclass
from typing import Callable, Union

import pytest

from msx.vdp.renderer import render_frame as _render_frame_tms9918a
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as _render_frame_v9938
from msx.vdp.vdp import VDP


@pytest.fixture
def bare_vdp() -> VDP:
    return VDP()


@dataclass(frozen=True)
class RendererFlavor:
    """One VDP backend under test: its VDP constructor and render_frame.

    Lets a test module written against the base VDP/TMS9918A renderer run
    unmodified logic against the V9938 renderer too, for the TMS9918A-
    compatible modes (GRAPHIC1/2, MULTICOLOUR, TEXT1, sprite mode 1) where
    both chips are meant to agree on 16 KB-range register values."""

    name: str
    make_vdp: Callable[[], Union[VDP, V9938]]
    render_frame: Callable[..., bytearray]


_FLAVORS = [
    RendererFlavor("tms9918a", VDP, _render_frame_tms9918a),
    RendererFlavor("v9938", V9938, _render_frame_v9938),
]


@pytest.fixture(params=_FLAVORS, ids=[f.name for f in _FLAVORS])
def flavor(request: pytest.FixtureRequest) -> RendererFlavor:
    return request.param
