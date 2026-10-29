"""host/debugger/stop.py: the host side of a core stop (debug-monitor "The
REPL opens after the core stops", "Watchpoint hit message", "Debug REPL
entry"; machine-core "The host requests a break")."""
from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from host.debugger.prompt import Debugger
from host.debugger.stop import (
    handle_stop,
    sigint_interrupts,
    sigint_requests_break,
    watch_hit_line,
)
from host.rpc_server import DebugServer, PauseReason
from msx.boundary import (
    STOP_BREAKPOINT,
    STOP_NONE,
    STOP_REQUESTED,
    STOP_WATCHPOINT,
    WATCH_READ,
    WATCH_WRITE,
    StopInfo,
)
from msx.input import InputFrame
from msx.machine import Machine
from tests.factories import make_machine

# JP 4000h; at 4000h select slot 3 (RAM) for pages 2-3, store 5Ah at C000h
# from the instruction at 4006h, then loop at 4009h.
_STORE_ROM = bytearray(32768)
_STORE_ROM[0x0000:0x0003] = bytes([0xC3, 0x00, 0x40])
_STORE_ROM[0x4000:0x400B] = bytes([
    0x3E, 0xF0, 0xD3, 0xA8, 0x3E, 0x5A, 0x32, 0x00, 0xC0, 0x18, 0xFE,
])


def _machine() -> Machine:
    return make_machine(rom=bytes(_STORE_ROM))


def _step(m: Machine) -> int:
    return m.step_frame(InputFrame(), render_video=False).stop_reason


# -- watchpoint line ---------------------------------------------------------

def test_read_watchpoint_message() -> None:
    stop = StopInfo(STOP_WATCHPOINT, 0xC024, 0xCA4A, 0x02, WATCH_READ)
    assert watch_hit_line(stop) == "\n[WP] READ  CA4Ah = 02h  PC=C024h"


def test_write_watchpoint_message() -> None:
    stop = StopInfo(STOP_WATCHPOINT, 0xC030, 0xCA4A, 0x05, WATCH_WRITE)
    assert watch_hit_line(stop) == "\n[WP] WRITE CA4Ah = 05h  PC=C030h"


# -- handle_stop -------------------------------------------------------------

def test_no_stop_does_nothing() -> None:
    debugger, rpc = MagicMock(), MagicMock()
    handle_stop(_machine(), STOP_NONE, debugger, rpc)
    debugger.enter.assert_not_called()
    rpc.on_core_stop.assert_not_called()


def test_prompt_appears_on_breakpoint_hit() -> None:
    m = _machine()
    m.set_breakpoints([0x4006])
    debugger = MagicMock()
    reason = _step(m)
    assert reason == STOP_BREAKPOINT
    handle_stop(m, reason, debugger, None)
    debugger.enter.assert_called_once()


def test_a_watchpoint_stop_prints_the_line_then_opens_the_repl(capsys) -> None:
    m = _machine()
    m.set_watchpoints([(0xC000, "w")])
    debugger = MagicMock()
    reason = _step(m)
    handle_stop(m, reason, debugger, None)
    assert "[WP] WRITE C000h = 5Ah  PC=4006h" in capsys.readouterr().out
    debugger.enter.assert_called_once()


def test_a_breakpoint_with_an_rpc_server_pauses_the_rpc_gate(tmp_path: Path) -> None:
    m = _machine()
    m.set_breakpoints([0x4006])
    srv = DebugServer(m, sock_path=str(tmp_path / "s.sock"))
    debugger = MagicMock()
    handle_stop(m, _step(m), debugger, srv)
    debugger.enter.assert_not_called()
    assert srv.pause_state.paused
    assert srv.pause_state.reason == PauseReason.BREAKPOINT


@pytest.mark.parametrize(
    ("stop_reason", "wire"),
    [
        (STOP_BREAKPOINT, "breakpoint"),
        (STOP_WATCHPOINT, "watchpoint"),
        (STOP_REQUESTED, "user_request"),
    ],
)
def test_the_rpc_reason_for_each_stop(tmp_path: Path, stop_reason: int, wire: str) -> None:
    srv = DebugServer(_machine(), sock_path=str(tmp_path / "s.sock"))
    srv.on_core_stop(stop_reason)
    assert srv.pause_state.reason == wire


def test_c_resumes_and_continues_the_stopped_frame() -> None:
    m = _machine()
    m.set_breakpoints([0x4006])
    assert _step(m) == STOP_BREAKPOINT
    m.set_breakpoints([])
    with patch("builtins.input", side_effect=["c"]):
        handle_stop(m, STOP_BREAKPOINT, Debugger(m), None)
    out = m.step_frame(InputFrame(), render_video=False)
    assert out.frame_complete and out.stop_reason == STOP_NONE


# -- Ctrl-C ------------------------------------------------------------------

def test_sigint_becomes_a_break_request() -> None:
    machine = MagicMock()
    before = signal.getsignal(signal.SIGINT)
    with sigint_requests_break(machine):
        os.kill(os.getpid(), signal.SIGINT)
    machine.request_break.assert_called_once()
    assert signal.getsignal(signal.SIGINT) is before


def test_a_break_request_from_ctrl_c_opens_the_repl() -> None:
    m = _machine()
    debugger = MagicMock()
    with sigint_requests_break(m):
        os.kill(os.getpid(), signal.SIGINT)
        reason = _step(m)
    assert reason == STOP_REQUESTED
    handle_stop(m, reason, debugger, None)
    debugger.enter.assert_called_once()


def test_ctrl_c_opens_the_repl_with_an_rpc_server(tmp_path: Path) -> None:
    # A person at the terminal made the request, so the REPL opens even
    # though an RPC server runs; the RPC pause state does not change.
    m = _machine()
    srv = DebugServer(m, sock_path=str(tmp_path / "s.sock"))
    debugger = MagicMock()
    with sigint_requests_break(m):
        os.kill(os.getpid(), signal.SIGINT)
        reason = _step(m)
    assert reason == STOP_REQUESTED
    handle_stop(m, reason, debugger, srv)
    debugger.enter.assert_called_once()
    assert not srv.pause_state.paused


def test_sigint_interrupts_while_active() -> None:
    with sigint_requests_break(MagicMock()):
        with pytest.raises(KeyboardInterrupt), sigint_interrupts():
            os.kill(os.getpid(), signal.SIGINT)


def test_ctrl_c_at_the_prompt_exits(capsys) -> None:
    # The run loop's break handler is active; the REPL restores the default,
    # so Ctrl-C at the prompt raises and the REPL exits the emulator.
    m = _machine()

    def ctrl_c(_prompt: str = "") -> str:
        os.kill(os.getpid(), signal.SIGINT)
        return "c"

    with sigint_requests_break(m), patch("builtins.input", side_effect=ctrl_c):
        with pytest.raises(SystemExit) as exc:
            Debugger(m).enter()
    assert exc.value.code == 0
    assert "Exiting emulator." in capsys.readouterr().out


# -- s over a watched access --------------------------------------------------

def test_s_over_a_watched_access_stays_in_the_repl(capsys) -> None:
    m = _machine()
    while m.cpu_registers().PC != 0x4006:
        m.step()
    m.set_watchpoints([(0xC000, "w")])
    with patch("builtins.input", side_effect=["s", "c"]) as prompts:
        Debugger(m).enter()
    out = capsys.readouterr().out
    wp = out.index("[WP] WRITE C000h = 5Ah  PC=4006h")
    assert out.index("PC=4009", wp) > wp       # the step's register summary follows
    assert out.count("Debugger entered.") == 1  # no second REPL
    assert prompts.call_count == 2              # the same prompt again, then c


def test_s_advances_the_cycle_count_at_the_prompt() -> None:
    # debug-monitor "s [N]": the prompt's cyc rises by the step's T-states.
    m, twin = _machine(), _machine()
    m.set_breakpoints([0x4006])
    twin.set_breakpoints([0x4006])
    assert _step(m) == STOP_BREAKPOINT and _step(twin) == STOP_BREAKPOINT
    n = twin.step()
    prompts: list[str] = []

    def answer(prompt: str = "") -> str:
        prompts.append(prompt)
        return "s" if len(prompts) == 1 else "c"

    before = m.cycle_count
    with patch("builtins.input", side_effect=answer):
        Debugger(m).enter()
    assert prompts[0].startswith(f"(msx-dbg cyc={before} ")
    assert prompts[1].startswith(f"(msx-dbg cyc={before + n} ")
