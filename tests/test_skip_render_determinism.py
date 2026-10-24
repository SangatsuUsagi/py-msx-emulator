"""Frame skip never changes emulated state (machine-core "Skipping video never
changes emulated state" / "Same snapshot with and without rendering")."""
from __future__ import annotations

from msx.machine import Machine
from msx.state import _snapshot_from_machine
from tests.factories import make_machine, make_machine_msx2

_FRAMES = 30
# DI; JR $ -- the CPU idles while the test drives the VDP's sprite tables.
_SPIN_ROM = bytes([0xF3, 0x18, 0xFE]) + bytes(0x8000 - 3)


def _overlapping_sprites_tms(machine: Machine) -> None:
    vdp = machine.vdp
    vdp.regs[1] = 0x40               # BL=1, 8x8 sprites
    vdp.regs[5] = 0x20               # SAT at 0x1000
    for i in range(5):               # five sprites on one line, overlapping
        vdp.vram[0x1000 + i * 4: 0x1000 + i * 4 + 4] = bytes((10, i * 4, 0, 0x0F))
    vdp.vram[0x1000 + 5 * 4] = 0xD0
    vdp.vram[0] = 0xFF


def _overlapping_sprites_v9938(machine: Machine) -> None:
    vdp = machine.vdp
    vdp.regs[0] = 0x06               # GRAPHIC4, sprite mode 2
    vdp.regs[1] = 0x40
    vdp.regs[5] = 0x77
    sat = 0x77 << 7
    for i in range(9):
        vdp.vram[sat + i * 4: sat + i * 4 + 4] = bytes((10, i, 0, 0))
        vdp.vram[((sat & ~0x3FF) & 0x1FFFF) + i * 16] = 0x0F
    vdp.vram[sat + 9 * 4] = 216
    vdp.vram[0] = 0xFF


def _run(machine: Machine, render: bool) -> list[bytes]:
    audio = []
    for _ in range(_FRAMES):
        machine.run_frame(skip_render=not render)
        audio.append(machine.take_audio())
    return audio


def _assert_same_with_and_without_rendering(build, setup) -> None:  # type: ignore[no-untyped-def]
    rendered, skipped = build(), build()
    setup(rendered)
    setup(skipped)
    audio_r = _run(rendered, render=True)
    audio_s = _run(skipped, render=False)
    assert rendered.vdp.status & 0x40              # 5S/9S was raised: not vacuous
    assert audio_r == audio_s
    assert _snapshot_from_machine(rendered) == _snapshot_from_machine(skipped)


def test_msx1_same_snapshot_with_and_without_rendering() -> None:
    _assert_same_with_and_without_rendering(
        lambda: make_machine(_SPIN_ROM), _overlapping_sprites_tms)


def test_msx2_same_snapshot_with_and_without_rendering() -> None:
    _assert_same_with_and_without_rendering(
        lambda: make_machine_msx2(_SPIN_ROM, bytes(0x4000)), _overlapping_sprites_v9938)
