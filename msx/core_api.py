"""The core API: every operation that code outside msx/ uses on a machine
(machine-core "Host and frontend reach the core only through the core API").

`Machine` implements it structurally; a wrapper of a native core can
implement it later (include/msx_core.h is the C form).

Values. Every parameter and result is an integer, bytes, or a flat record,
list or tuple of these, never None; an absent integer argument is -1
(machine-core "Core API values are integers, bytes or flat records"). The
ids and the machine properties are plain integers (their enums are in
msx/boundary.py), watch kinds and mouse buttons are bit flags, and
step_frame's InputFrame and FrameOutput hold no nested or optional record
(a frame that was not rendered has a video width of 0). No member takes or
returns a file path, and none takes a callback the core calls (machine-core
"The core API has no callback into the host"): a break is the step_frame
stop reason and the last-stop query. The inspection queries change no
emulated state.

Errors. No member raises for an invalid argument. Every control operation
returns an integer status (msx/boundary.py STATUS_*):

- the break setters: STATUS_BAD_ARGUMENT for a value outside its range or
  more than MAX_BREAKPOINTS / MAX_WATCHPOINTS entries;
- the flag setters and mount_disk's write_protected: 0 or 1, else
  STATUS_BAD_ARGUMENT;
- read_media: b"" for an id the machine lacks (any integer);
- read_memory, read_vram: b"" for a length outside 0-0x10000 /
  0-vram_size;
- slot_info: a content, mapper and size of -1 for a slot, sub-slot or
  page outside 0-3 (-1 is "none" for the last two). Its record is
  integers only; the host builds the sl/st text.

last_error_text() gives the message of the last failure. A disk is
ejected with eject_disk.

Debug settings. The slot queries (slot_layout, slot_info) and the sprite
display flag (set_sprites_hidden, sprites_hidden) serve the debugger's sl,
st and ds. The trace members (set_trace_kinds, trace_kinds, trace_sources,
drain_trace) carry the VDP and mapper trace and the debug log as flat
integer events (msx/boundary.py TraceEvent) that the host formats;
set_trace_kinds returns STATUS_BAD_ARGUMENT for an unknown kind. The flag
and the kinds are outside the saved state.

Construction. msx.machine_loader.create_machine builds the machine and
reports every rejected configuration as a status, a device size and an
RTC start time included (allium/core-frontend.allium).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from msx.boundary import (
        CpuRegisters,
        FrameOutput,
        MediaId,
        RgbFrame,
        SlotInfo,
        SlotLayout,
        StopInfo,
        TraceEvent,
        VdpInfo,
    )
    from msx.input import InputFrame
    from msx.machine import Machine


@runtime_checkable
class CoreApi(Protocol):
    """The machine as code outside msx/ sees it."""

    # -- machine properties --------------------------------------------------

    @property
    def cycle_count(self) -> int: ...
    @property
    def video_standard(self) -> int: ...  # a VideoStandard value
    @property
    def keyboard_layout(self) -> int: ...  # a KeyboardLayout value
    def samples_per_frame(self) -> int: ...

    # -- running ---------------------------------------------------------------

    def step_frame(self, frame_input: InputFrame, render_video: bool) -> FrameOutput: ...
    def apply_input(self, frame_input: InputFrame) -> int: ...
    def step(self) -> int: ...  # the T-states of the instruction
    def reset(self) -> int: ...

    # -- media -------------------------------------------------------------------

    def dirty_media(self) -> list[MediaId]: ...
    def read_media(self, media_id: MediaId) -> bytes: ...  # b"" when absent
    def mark_persisted(self, media_id: MediaId) -> int: ...
    def mount_disk(self, drive: int, data: bytes, write_protected: bool) -> int: ...
    def eject_disk(self, drive: int) -> int: ...
    def disk_mounted(self, drive: int) -> bool: ...

    # -- save states -------------------------------------------------------------

    def save_state(self) -> bytes: ...
    def load_state(self, data: bytes) -> int: ...
    def last_error_text(self) -> bytes: ...

    # -- attachments -----------------------------------------------------------

    def attach_mouse(self, port: int) -> int: ...  # once, before the first step_frame

    # -- break settings ------------------------------------------------------------

    def set_breakpoints(self, addrs: list[int]) -> int: ...
    def set_watchpoints(self, entries: list[tuple[int, int]]) -> int: ...
    def set_temp_breakpoint(self, addr: int) -> int: ...  # -1 clears
    def set_step_out(self, sp: int) -> int: ...
    def set_frame_breakpoint(self, frame: int) -> int: ...  # -1 clears
    def set_break_halt_di(self, enabled: bool) -> int: ...
    def set_sprites_hidden(self, hidden: bool) -> int: ...
    def set_trace_kinds(self, kinds: int) -> int: ...
    def set_sp_range(self, low: int, high: int) -> int: ...  # (-1, -1) clears

    # -- break control ---------------------------------------------------------------

    def request_break(self) -> int: ...  # safe from a signal handler
    def last_stop(self) -> StopInfo: ...

    # -- inspection queries --------------------------------------------------------

    def cpu_registers(self) -> CpuRegisters: ...
    def read_memory(self, addr: int, length: int) -> bytes: ...
    def write_memory(self, addr: int, data: bytes) -> int: ...
    def vram_size(self) -> int: ...
    def read_vram(self, addr: int, length: int) -> bytes: ...
    def vdp_info(self) -> VdpInfo: ...
    def frame_number(self) -> int: ...
    def drive_count(self) -> int: ...
    def main_ram_range(self) -> tuple[int, int]: ...
    def slot_layout(self) -> SlotLayout: ...
    def slot_info(self, primary: int, secondary: int, page: int) -> SlotInfo: ...  # -1: none
    def breakpoints(self) -> tuple[int, ...]: ...
    def watchpoints(self) -> tuple[tuple[int, int], ...]: ...
    def break_halt_di(self) -> bool: ...
    def sprites_hidden(self) -> bool: ...
    def trace_kinds(self) -> int: ...
    def trace_sources(self) -> int: ...
    def drain_trace(self) -> tuple[TraceEvent, ...]: ...
    def render_preview(self) -> RgbFrame: ...


if TYPE_CHECKING:
    # mypy fails here when Machine stops matching the core API.
    _MACHINE_IS_CORE_API: type[CoreApi] = Machine
