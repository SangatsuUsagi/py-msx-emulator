from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import NamedTuple, TypedDict

from msx.boundary import AUDIO_SAMPLE_RATE
from msx.cassette import CassettePort
from msx.clock import CoreClock
from msx.input import InputLines
from msx.mouse import MouseDevice
from msx.snapshot_schema import (
    check_bool,
    check_choice,
    check_int,
    check_int_list,
    check_object,
)

_REG_IO_PORT_A = 14
# Register 14 bit 6: the keyboard-layout line (not modeled, reads 1).
_PORT_A_LAYOUT_BIT = 0x40
# Register 14 bit 7: the cassette input.
_PORT_A_TAPE_SHIFT = 7

# What a port 0xA2 read keeps of each register (PSG.read_mask; the stored
# register keeps all 8 bits). The YM2149 reads back every bit. The
# AY-3-8910's register latches are narrower and read the unused bits as 0:
# R1/R3/R5/R13 4 bits, R6 and R8-R10 5 bits (lvd2 AY-3-8910 die model,
# rtl/ay_model.v). A real SONY HB-F1XD reads back the AY-3-8910 mask and a
# real SONY HB-10 the YM2149 one (write-then-read tests, 2026-11-11 and
# 2026-11-06).
YM2149_READ_MASK: tuple[int, ...] = (0xFF,) * 16
AY8910_READ_MASK: tuple[int, ...] = (
    0xFF, 0x0F, 0xFF, 0x0F, 0xFF, 0x0F, 0x1F, 0xFF,
    0x1F, 0x1F, 0x1F, 0xFF, 0xFF, 0x0F, 0xFF, 0xFF,
)

PSG_CLOCK: int = 223_722      # AY-3-8910: 3,579,545 Hz / 16
# Upper bound on recorded register writes per audio buffer. Far above any real
# frame's write count; caps memory if generate_samples is never called (headless).
_MAX_EVENTS: int = 4096

# Which joystick port a mouse (or, in read_port's joystick composition, an
# InputLines read) targets. IntEnum, not a bare int: its two values are used
# directly as the register 15 bit 6 (JOY_SELECT) value throughout this file,
# so IntEnum keeps that "== 0 / == 1" comparison code unchanged while still
# giving MouseSlot.port a named, exhaustive type instead of an unconstrained
# int -- maps directly to a Rust/C++ two-variant enum.
class JoystickPort(IntEnum):
    JOY1 = 0
    JOY2 = 1


# Mouse attached to a joystick port (see msx/mouse.py). A NamedTuple so
# "device" and "port" can't independently desync (unlike two separate
# Optional-shaped fields) — maps directly to Option<MouseSlot> in Rust.
class MouseSlot(NamedTuple):
    device: MouseDevice
    port: JoystickPort

# AY-3-8910 measured-silicon amplitude table (level 0–15 → 16-bit amplitude),
# derived from references/ayumi's AY_dac_table (an oscilloscope-measured DAC
# curve), rescaled from its [0, 1] range to this table's [0, 12287] headroom.
# Not a geometric (constant-dB-step) curve -- notably levels 7 and 8 sit much
# closer together than their neighbours, a real irregularity in the AY-3-8910
# DAC that references/emu2149's independently-measured table agrees on.
# Three channels at max sum to 36 861; SDL2 output is clamped to [0, 32 767].
_VOL_TABLE: tuple[int, ...] = (
    0,    123,  178,   259,   377,   560,   793,   1319,
    1555, 2519, 3590,  4581,  6052,  7806,  9898,  12287,
)


class PsgSynthState(TypedDict):
    """Save-state schema for PSG.snapshot_synth()/PSG.apply_synth_state().

    Covers only the internal tone/noise/envelope generator state; the
    register file (regs/latch) is captured directly as the snapshot's
    psg.registers/psg.latch and is out of scope here. Keys are the
    attribute names without their leading underscore.
    """

    tone_cnt: list[int]
    tone_out: list[int]
    noise_cnt: int
    lfsr: int
    env_cnt: int
    env_step: int
    env_attack: int
    env_alternate: bool
    env_hold_flag: bool
    env_holding: bool
    clk_frac: int


# PORT-NOTE: key set read from the TypedDict at import time (see
#   msx/vdp/v9938.py's _STATE_KEYS note: serde `deny_unknown_fields` in Rust,
#   a constexpr key array in C++).
_PSG_SYNTH_KEYS: frozenset[str] = frozenset(PsgSynthState.__annotations__)


@dataclass
class PSG:
    """The AY-3-8910 PSG: the register file on ports 0xA0-0xA2, the joystick and
    mouse ports, and the tone, noise and envelope generators (generate_samples)."""

    regs: list[int] = field(default_factory=lambda: [0] * 16)
    latch: int = 0
    # The port 0xA2 read-back mask per register, fixed by build_machine for
    # the machine's life (YM2149_READ_MASK or AY8910_READ_MASK).
    read_mask: tuple[int, ...] = YM2149_READ_MASK
    _input: InputLines | None = field(default=None, repr=False)
    _mouse: MouseSlot | None = field(default=None, repr=False)
    # The mouse port's pins 6, 7 and 8 (bits 0-2) at the last register 15
    # write; -1 before the first write, so that write always reaches the mouse.
    _mouse_pins: int = field(default=-1, repr=False)
    # The machine's cassette port (attach_cassette), or None with no port.
    _cassette: CassettePort | None = field(default=None, repr=False)

    # Sub-frame audio: register writes are timestamped (cycle, reg, value)
    # with the clock record's cycle count (msx/clock.py; build_machine gives
    # the machine's record) so generate_samples can place them at their
    # in-frame sample positions (reproduces software PCM). _regs_base /
    # _gen_base snapshot the register + generator state at the frame's first
    # write, for replay rewind.
    _clock: CoreClock = field(default_factory=CoreClock, repr=False)
    # Recorded writes as (cycle, reg, value); a port would use a small struct.
    # Kept as a plain tuple here to avoid per-write allocation on the I/O path.
    _events: list[tuple[int, int, int]] = field(default_factory=list, init=False, repr=False)
    _regs_base: list[int] = field(default_factory=list, init=False, repr=False)
    _gen_base: PsgSynthState | None = field(default=None, init=False, repr=False)

    # --- synthesiser state (not part of __init__) ---
    #
    # PORT-NOTE: _tone_cnt/_tone_out are 3-element list[int] fields, fixed
    #   size for the instance's whole lifetime, indexed in the per-sample hot
    #   loop (_render, below).
    # Rust equivalent: [i32; 3] fields, not Vec<i32>.
    # C++ equivalent: std::array<int32_t, 3>.
    # Kept as-is here because: Python has no cheap fixed-size numeric array
    #   type outside NumPy (which this project doesn't depend on), and a
    #   3-element list's overhead is immaterial next to the per-sample work
    #   _render already does.
    _tone_cnt: list[int] = field(default_factory=lambda: [1, 1, 1], init=False, repr=False)
    _tone_out: list[int] = field(default_factory=lambda: [0, 0, 0], init=False, repr=False)
    _noise_cnt: int = field(default=1, init=False, repr=False)
    _lfsr: int = field(default=1, init=False, repr=False)   # 17-bit; must never be 0
    # Envelope: 32-step down-counter + attack/alternate/hold model (openMSX/MAME;
    # the attack/alternate/hold shape bits are the AY-3-8910 data sheet's R15,
    # references/docs/General_Instrument_AY-3-8910.md).
    _env_cnt: int = field(default=1, init=False, repr=False)
    _env_step: int = field(default=0x1F, init=False, repr=False)   # 5-bit counter 0-31
    _env_attack: int = field(default=0, init=False, repr=False)    # 0x00 or 0x1F
    _env_alternate: bool = field(default=False, init=False, repr=False)
    # Power-on envelope state is that of R13 shape 0 (_reset_envelope): hold set.
    _env_hold_flag: bool = field(default=True, init=False, repr=False)
    _env_holding: bool = field(default=False, init=False, repr=False)
    _clk_frac: int = field(default=0, init=False, repr=False)

    def attach_cassette(self, cassette: CassettePort) -> None:
        """Connect the machine's cassette port: register 14 bit 7 then reads
        the tape input. With no port bit 7 reads 0."""
        self._cassette = cassette

    def attach_mouse(self, device: MouseDevice, port: JoystickPort) -> None:
        """Attach an MSX mouse to the given joystick port.

        The sole writer of `_mouse`: nothing outside this class sets it
        directly (see `Machine.attach_mouse`, which delegates here).
        """
        self._mouse = MouseSlot(device, port)

    @property
    def mouse(self) -> MouseDevice | None:
        """The attached mouse, or None (read-only; see attach_mouse)."""
        return self._mouse.device if self._mouse is not None else None

    # ------------------------------------------------------------------ ports

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA0:
            self.latch = value & 0x0F
        elif port == 0xA1:
            reg = self.latch
            if len(self._events) < _MAX_EVENTS:
                if not self._events:
                    # Frame's first write: snapshot state to rewind to at replay.
                    self._regs_base = self.regs[:]
                    self._gen_base = self.snapshot_synth()
                self._events.append((self._clock.cycle, reg, value))
            self._set_reg(reg, value)
            if reg == 13:
                self._reset_envelope()
            elif reg == 15 and self._mouse is not None:
                # The joystick port passes a write to its device only when
                # the port's output pins change, independent of JOY_SELECT
                # (bit 6) (openMSX JoystickPort::write).
                pins = self._mouse_port_pins(value)
                if pins != self._mouse_pins:
                    self._mouse_pins = pins
                    self._mouse.device.write_pin8(pins >> 2, self._clock.cycle)

    def _mouse_port_pins(self, value: int) -> int:
        """The mouse port's pins 6 and 7 (bits 0-1) and pin 8 (bit 2) in a
        register 15 value: Joy1 bits 0, 1 and 4; Joy2 bits 2, 3 and 5."""
        assert self._mouse is not None
        if self._mouse.port == JoystickPort.JOY1:
            return (value & 0x03) | ((value >> 2) & 0x04)
        return ((value >> 2) & 0x03) | ((value >> 3) & 0x04)

    def sync_mouse_pins(self) -> None:
        """After a state load: take the mouse port's last pins from the
        loaded register 15."""
        if self._mouse is not None:
            self._mouse_pins = self._mouse_port_pins(self.regs[15])

    def read_port(self, port: int) -> int:
        if port == 0xA2:
            if self.latch == _REG_IO_PORT_A and self._input is not None:
                # PORT A returns the *selected* joystick port's 6 signals on
                # bits 0-5 (dir 0-3, triggers 4-5). JOY_SELECT is PSG register
                # 15 bit 6: 0→Joy1, 1→Joy2. Bit 6 is the keyboard-layout
                # line (not modeled, reads 1) and bit 7 the cassette input.
                high = _PORT_A_LAYOUT_BIT
                if self._cassette is not None:
                    high |= self._cassette.read_level() << _PORT_A_TAPE_SHIFT
                joy_select = JoystickPort((self.regs[15] >> 6) & 1)
                if self._mouse is not None and joy_select == self._mouse.port:
                    return (self._mouse.device.read() & 0x3F) | high
                sel = self._input.joy1 if joy_select == JoystickPort.JOY1 else self._input.joy2
                return (sel & 0x3F) | high
            return self.regs[self.latch] & self.read_mask[self.latch]
        return 0xFF

    def _set_reg(self, reg: int, value: int) -> None:
        """Store a register value. A tone (R0-R5) or noise (R6) period change
        takes effect at once: the chip's period counter counts up and is
        compared with `>=` against the period register (lvd2 AY-3-8910 die
        model), so the remaining count of this down-counter model moves by
        the period difference, and a period below the elapsed count ends the
        half-cycle on the next tick."""
        if reg <= 5:
            ch = reg >> 1
            old = self._tone_period(ch)
            self.regs[reg] = value
            self._tone_cnt[ch] = max(1, self._tone_cnt[ch] + self._tone_period(ch) - old)
        elif reg == 6:
            old = max(1, self.regs[6] & 0x1F)
            self.regs[6] = value
            # The noise counter reloads to 2 * NP (see _step_noise).
            self._noise_cnt = max(1, self._noise_cnt + 2 * (max(1, value & 0x1F) - old))
        else:
            self.regs[reg] = value

    def _tone_period(self, ch: int) -> int:
        return max(1, ((self.regs[ch * 2 + 1] & 0x0F) << 8) | self.regs[ch * 2])

    # --------------------------------------------------------------- reset

    def reset(self) -> None:
        """Restore power-on register and synthesiser state (matches field defaults)."""
        self.regs = [0] * 16
        self.latch = 0
        self._tone_cnt = [1, 1, 1]
        self._tone_out = [0, 0, 0]
        self._noise_cnt = 1
        self._lfsr = 1
        self._env_cnt = 1
        self._env_step = 0x1F
        self._env_attack = 0
        self._env_alternate = False
        self._env_hold_flag = True
        self._env_holding = False
        self._clk_frac = 0
        self.discard_frame_events()

    # ------------------------------------------------------------ save-state
    # PORT-LIBRARY-NOTE: see msx/opll.py's snapshot()/restore() for the
    #   canonical note on this explicit-TypedDict-field save-state boundary
    #   pattern (shared by PSG/SCC/OPLL/FmPac) and its Rust serde /
    #   C++ nlohmann::json crate candidates.

    def snapshot_synth(self) -> PsgSynthState:
        """Capture internal tone/noise/envelope generator state for
        save-state (paired with parse_synth_state and apply_synth_state). The register file
        (regs/latch) is captured separately -- see the module-level
        PsgSynthState docstring.
        """
        return {
            "tone_cnt": list(self._tone_cnt),
            "tone_out": list(self._tone_out),
            "noise_cnt": self._noise_cnt,
            "lfsr": self._lfsr,
            "env_cnt": self._env_cnt,
            "env_step": self._env_step,
            "env_attack": self._env_attack,
            "env_alternate": self._env_alternate,
            "env_hold_flag": self._env_hold_flag,
            "env_holding": self._env_holding,
            "clk_frac": self._clk_frac,
        }

    def parse_synth_state(self, obj: object, label: str) -> PsgSynthState:
        """Validate a snapshot_synth() object without touching the PSG.

        Ranges are what the generators themselves produce: counters reload
        to at most 12-bit tone / 2 * 5-bit noise / 16-bit envelope periods,
        and a hold can leave _env_cnt a few ticks below zero (-16 margin).
        """
        state = check_object(obj, label, _PSG_SYNTH_KEYS)
        return {
            "tone_cnt": check_int_list(state["tone_cnt"], f"{label}.tone_cnt", 3, 1, 0xFFF),
            "tone_out": check_int_list(state["tone_out"], f"{label}.tone_out", 3, 0, 1),
            "noise_cnt": check_int(state["noise_cnt"], f"{label}.noise_cnt", 1, 62),
            "lfsr": check_int(state["lfsr"], f"{label}.lfsr", 1, 0x1FFFF),
            "env_cnt": check_int(state["env_cnt"], f"{label}.env_cnt", -16, 0xFFFF),
            "env_step": check_int(state["env_step"], f"{label}.env_step", 0, 0x1F),
            "env_attack": check_choice(
                state["env_attack"], f"{label}.env_attack", (0x00, 0x1F)
            ),
            "env_alternate": check_bool(state["env_alternate"], f"{label}.env_alternate"),
            "env_hold_flag": check_bool(state["env_hold_flag"], f"{label}.env_hold_flag"),
            "env_holding": check_bool(state["env_holding"], f"{label}.env_holding"),
            "clk_frac": check_int(
                state["clk_frac"], f"{label}.clk_frac", 0, AUDIO_SAMPLE_RATE - 1
            ),
        }

    def apply_synth_state(self, state: PsgSynthState) -> None:
        """Assign an already-parsed PsgSynthState (see parse_synth_state).
        Assigns only: the sub-frame rewind uses it too. A state load also
        calls discard_frame_events."""
        self._tone_cnt = list(state["tone_cnt"])
        self._tone_out = list(state["tone_out"])
        self._noise_cnt = state["noise_cnt"]
        self._lfsr = state["lfsr"]
        self._env_cnt = state["env_cnt"]
        self._env_step = state["env_step"]
        self._env_attack = state["env_attack"]
        self._env_alternate = state["env_alternate"]
        self._env_hold_flag = state["env_hold_flag"]
        self._env_holding = state["env_holding"]
        self._clk_frac = state["clk_frac"]

    def discard_frame_events(self) -> None:
        """Drop the frame's recorded writes and rewind base: after a state
        load, they belong to the state before the load."""
        self._events = []
        self._regs_base = []
        self._gen_base = None

    # --------------------------------------------------------- envelope reset

    def _env_period(self) -> int:
        return max(1, (self.regs[12] << 8) | self.regs[11])

    # _env_output_level, _step_noise, _step_envelope, and _mix_channel below are
    # the readable per-generator reference implementations. The hot path (_render)
    # inlines the same generator logic for speed; these are kept as the executable
    # spec and are exercised directly by the fine-grained unit tests. Keep them in
    # sync with _render (or delete both the method and its test if a generator is
    # ever dropped) — they are intentionally not called from production code.
    # (_mix_channel specifically models only the point-sampled digital AND-gate
    # result; _render's mixer additionally applies amplitude/envelope weighting
    # and duty-cycle integration on top of that same AND-gate result — see
    # "Tone integration" below. No reference method exists for tone's duty-cycle
    # integration itself: unlike a single/multi-tick counter step, it only has
    # meaning over an output sample's whole tick span, so it doesn't fit this
    # per-generator reference-method shape.)
    def _env_output_level(self) -> int:
        """Current 4-bit envelope level (0-15) from the step/attack model."""
        return (self._env_step ^ self._env_attack) >> 1

    def _reset_envelope(self) -> None:
        shape = self.regs[13] & 0x0F
        # attack bit (0x04) picks the ramp direction; step always starts at 0x1F.
        self._env_attack = 0x1F if (shape & 0x04) else 0
        if (shape & 0x08) == 0:
            # continue = 0: single-shot; alternate mirrors the attack bit.
            self._env_hold_flag = True
            self._env_alternate = self._env_attack != 0
        else:
            self._env_hold_flag = bool(shape & 0x01)
            self._env_alternate = bool(shape & 0x02)
        self._env_step = 0x1F
        self._env_holding = False
        # AY-3-8910: one full 32-step ramp = 256*EP/fMaster, so a single step
        # takes (256*EP/fMaster)/32 = EP/(fMaster/8) = EP PSG-clock ticks.  One
        # step therefore advances every `period` ticks (no *8 / *16 multiplier).
        self._env_cnt = self._env_period()

    # --------------------------------------------------------------- noise

    def _step_noise(self, ticks: int) -> None:
        period = max(1, self.regs[6] & 0x1F)
        self._noise_cnt -= ticks
        while self._noise_cnt <= 0:
            # Datasheet fN = fMaster/(16*NP) = PSG_CLOCK/(2*NP): the LFSR shifts
            # once every 2*NP PSG-clock ticks (single-period reload ran it 2x fast).
            self._noise_cnt += period * 2
            # 17-bit LFSR, polynomial x^17 + x^14 + 1 (feedback from bits 0 and 3)
            feedback = (self._lfsr ^ (self._lfsr >> 3)) & 1
            self._lfsr = ((self._lfsr >> 1) | (feedback << 16)) & 0x1FFFF

    # ------------------------------------------------------------ envelope

    def _step_envelope(self, ticks: int) -> None:
        if self._env_holding:
            return
        step_ticks = self._env_period()  # one 32-step count per EP PSG ticks
        self._env_cnt -= ticks
        while self._env_cnt <= 0:
            self._env_cnt += step_ticks
            self._env_step -= 1
            if self._env_step < 0:
                if self._env_hold_flag:
                    # Freeze at the boundary; alternate flips the final direction.
                    if self._env_alternate:
                        self._env_attack ^= 0x1F
                    self._env_holding = True
                    self._env_step = 0
                    break
                # Repeating shape: reload counter; alternate reverses the ramp
                # immediately (no dwell). (_env_step & 0x20) is the underflow bit.
                #
                # PORT-NOTE: _env_step went negative here (Python ints are
                #   arbitrary precision, so -1 & 0x20 == 0x20).
                # Rust equivalent: an unsigned type wraps instead of going
                #   negative; use a signed i32 for _env_step, or an explicit
                #   wrapping_sub + bit-5 check, so bit 5 still flags the -1
                #   boundary.
                # C++ equivalent: same — use a signed int32_t, since unsigned
                #   underflow wraps to a large positive value instead.
                # Kept as-is here because: semantic necessity, not
                #   performance — Python's arbitrary-precision two's-complement
                #   masking already gives the signed-underflow behavior a port
                #   must introduce explicitly via a signed type.
                if self._env_alternate and (self._env_step & 0x20):
                    self._env_attack ^= 0x1F
                self._env_step &= 0x1F

    # --------------------------------------------------------------- mixer

    def _mix_channel(self, ch: int) -> int:
        r7 = self.regs[7]
        tone_en = not ((r7 >> ch) & 1)
        noise_en = not ((r7 >> (ch + 3)) & 1)
        # AY-3-8910: channel output is tone AND noise; a disabled generator
        # contributes a constant 1, so it does not gate the enabled one.  Both
        # disabled → 1 & 1 = 1 (constant high, volume sets amplitude).
        tone_bit = self._tone_out[ch] if tone_en else 1
        noise_bit = (self._lfsr & 1) if noise_en else 1
        return tone_bit & noise_bit

    # ---------------------------------------------------- sample generation

    def _render(self, out: bytearray, start: int, end: int) -> None:
        """Synthesise samples [start, end) into `out` from the current registers.

        The registers are constant across the range (one segment), so all
        periods / mixer enables / volumes are precomputed once and the tone/
        noise/envelope generators are inlined with their state hoisted to locals
        and written back after the loop. generate_samples() calls this once per
        constant-register segment (once for the whole buffer when there are no
        mid-frame writes). Behaviour matches the _step_* methods kept above.

        Intentional exception to CLAUDE.md's "keep functions short" convention:
        this is the emulator's per-sample audio hot path, and the local-variable
        hoisting above is the reason for the length -- see the comment above
        _env_output_level for the readable, unhoisted equivalents this must stay
        in sync with.
        """
        regs = self.regs

        # --- precompute buffer-constant register-derived values ---
        tp0 = max(1, ((regs[1] & 0x0F) << 8) | regs[0])
        tp1 = max(1, ((regs[3] & 0x0F) << 8) | regs[2])
        tp2 = max(1, ((regs[5] & 0x0F) << 8) | regs[4])
        np2 = max(1, regs[6] & 0x1F) * 2          # noise reload = 2 * NP
        ep = max(1, (regs[12] << 8) | regs[11])   # envelope: one step per EP ticks
        r7 = regs[7]
        tone_en0 = not (r7 & 0x01)
        tone_en1 = not (r7 & 0x02)
        tone_en2 = not (r7 & 0x04)
        noise_en0 = not (r7 & 0x08)
        noise_en1 = not (r7 & 0x10)
        noise_en2 = not (r7 & 0x20)
        any_noise = noise_en0 or noise_en1 or noise_en2
        vr0, vr1, vr2 = regs[8], regs[9], regs[10]
        va0 = _VOL_TABLE[vr0 & 0x0F]
        va1 = _VOL_TABLE[vr1 & 0x0F]
        va2 = _VOL_TABLE[vr2 & 0x0F]
        env0 = bool(vr0 & 0x10)
        env1 = bool(vr1 & 0x10)
        env2 = bool(vr2 & 0x10)
        any_env = env0 or env1 or env2

        # --- hoist generator state to locals ---
        tc0, tc1, tc2 = self._tone_cnt
        to0, to1, to2 = self._tone_out
        nc = self._noise_cnt
        lfsr = self._lfsr
        env_cnt = self._env_cnt
        env_step = self._env_step
        env_attack = self._env_attack  # 0x00/0x1F XOR mask, see self._env_attack
        env_alternate = self._env_alternate
        env_hold_flag = self._env_hold_flag
        env_holding = self._env_holding
        clk_frac = self._clk_frac

        for i in range(start, end):
            # Advance PSG clock by one sample's worth of ticks (integer arithmetic).
            clk_frac += PSG_CLOCK
            ticks = clk_frac // AUDIO_SAMPLE_RATE
            clk_frac %= AUDIO_SAMPLE_RATE

            # tone channels (inline _step_tone).  Integrate the square wave over
            # the sample instead of point-sampling: hi0/hi1/hi2 count the PSG
            # ticks the output was high across the `ticks` clocks of this sample.
            # For audible tones (no toggle within a sample) hi == ticks or 0, so
            # this is identical to point-sampling; for ultrasonic tones (period
            # 0/1, the software-PCM carrier) it yields the ~50% duty average the
            # real analog output produces, instead of aliasing to full/zero.
            rem = ticks
            hi0 = 0
            while tc0 <= rem:
                if to0:
                    hi0 += tc0
                rem -= tc0
                to0 ^= 1
                tc0 = tp0
            if to0:
                hi0 += rem
            tc0 -= rem
            # channels 1 and 2: identical integration, unrolled for the hot loop
            rem = ticks
            hi1 = 0
            while tc1 <= rem:
                if to1:
                    hi1 += tc1
                rem -= tc1
                to1 ^= 1
                tc1 = tp1
            if to1:
                hi1 += rem
            tc1 -= rem
            rem = ticks
            hi2 = 0
            while tc2 <= rem:
                if to2:
                    hi2 += tc2
                rem -= tc2
                to2 ^= 1
                tc2 = tp2
            if to2:
                hi2 += rem
            tc2 -= rem

            # noise (inline _step_noise): 17-bit LFSR, taps at bits 0 and 3.
            # Skipped when no channel enables noise — its output is unused then, so
            # this only desyncs the LFSR's (pseudo-random, inaudible) phase for a
            # later re-enable, in exchange for dropping the hottest _render loop.
            if any_noise:
                nc -= ticks
                while nc <= 0:
                    nc += np2
                    feedback = (lfsr ^ (lfsr >> 3)) & 1
                    lfsr = ((lfsr >> 1) | (feedback << 16)) & 0x1FFFF
            noise_bit = lfsr & 1

            # envelope (inline _step_envelope). Skipped when no channel routes the
            # envelope (env_amp is unused then); a later re-enable is self-correcting
            # via the R13 write that calls _reset_envelope.
            if any_env:
                if not env_holding:
                    env_cnt -= ticks
                    while env_cnt <= 0:
                        env_cnt += ep
                        env_step -= 1
                        if env_step < 0:
                            if env_hold_flag:
                                if env_alternate:
                                    env_attack ^= 0x1F
                                env_holding = True
                                env_step = 0
                                break
                            # (env_step & 0x20) is the underflow bit -- see
                            # _step_envelope's PORT-NOTE (signed-type caveat) above.
                            if env_alternate and (env_step & 0x20):
                                env_attack ^= 0x1F
                            env_step &= 0x1F
                env_amp = _VOL_TABLE[(env_step ^ env_attack) >> 1]
            else:
                env_amp = 0

            # mixer: channel output = tone AND noise (disabled generator → 1).
            # Tone contributes its integrated level hiN/ticks; noise (broadband)
            # is point-sampled. Disabled tone → full level (hi == ticks).
            # A channel passes noise when noise is disabled for it, or enabled and
            # the LFSR bit is high (noise gates the channel; disabled → constant 1).
            sample = 0
            if noise_bit or not noise_en0:
                amp = env_amp if env0 else va0
                if not tone_en0 or hi0 == ticks:
                    sample += amp
                elif hi0:
                    sample += amp * hi0 // ticks
            if noise_bit or not noise_en1:
                amp = env_amp if env1 else va1
                if not tone_en1 or hi1 == ticks:
                    sample += amp
                elif hi1:
                    sample += amp * hi1 // ticks
            if noise_bit or not noise_en2:
                amp = env_amp if env2 else va2
                if not tone_en2 or hi2 == ticks:
                    sample += amp
                elif hi2:
                    sample += amp * hi2 // ticks

            # PORT-NOTE: sample is a non-negative i32 here (3 channels x 12287
            #   max = 36861 pre-clamp); amp * hiN // ticks is Python floor
            #   division.
            # Rust equivalent: integer `/` on non-negative i32 operands already
            #   truncates toward zero, which matches floor division here since
            #   both operands are non-negative — a direct `amp * hiN / ticks`.
            # C++ equivalent: same — `/` on non-negative ints truncates toward
            #   zero, matching this floor division.
            # Kept as-is here because: semantic necessity, not performance —
            #   noted so a port doesn't need to double-check whether `//` here
            #   requires special-casing; on these non-negative operands, plain
            #   truncating division already matches.
            if sample > 32767:
                sample = 32767

            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        # --- write generator state back ---
        self._tone_cnt[0], self._tone_cnt[1], self._tone_cnt[2] = tc0, tc1, tc2
        self._tone_out[0], self._tone_out[1], self._tone_out[2] = to0, to1, to2
        self._noise_cnt = nc
        self._lfsr = lfsr
        self._env_cnt = env_cnt
        self._env_step = env_step
        self._env_attack = env_attack
        self._env_alternate = env_alternate
        self._env_hold_flag = env_hold_flag
        self._env_holding = env_holding
        self._clk_frac = clk_frac

    def generate_samples(
        self, n: int, frame_start: int = 0, frame_end: int = 0
    ) -> bytearray:
        """Return n signed 16-bit little-endian mono PCM samples.

        Register writes recorded during the frame (write_port) are applied at
        their sub-frame sample positions, so software PCM played through rapid
        volume-register writes is reproduced. With no recorded writes — or no
        frame window (frame_end <= frame_start) — the whole buffer is one
        constant-register segment (the fast path, unchanged behaviour).
        """
        out = bytearray(n * 2)
        events = self._events
        if not events or frame_end <= frame_start or self._gen_base is None:
            self._events = []
            self._render(out, 0, n)
            return out
        # Sub-frame replay: rewind to the frame-start register + generator state,
        # then render segments split at each write's computed sample position.
        final = self.regs[:]
        self.regs[:] = self._regs_base
        self.apply_synth_state(self._gen_base)
        span = frame_end - frame_start
        lo = 0
        for cyc, reg, val in events:
            pos = (cyc - frame_start) * n // span
            if pos < 0:
                pos = 0
            elif pos > n:
                pos = n
            if pos > lo:
                self._render(out, lo, pos)
                lo = pos
            self._set_reg(reg, val)
            if reg == 13:
                self._reset_envelope()
        if lo < n:
            self._render(out, lo, n)
        # Restore the true final register state (also covers any capped events).
        self.regs[:] = final
        self._events = []
        self._gen_base = None
        return out
