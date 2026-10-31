"""What the host does when the core stops (machine-core "A break stops
step_frame and the next call continues the frame", debug-monitor "The REPL
opens after the core stops").

The core reports a break only through the step_frame result and its
last-stop query; this module turns that into the RPC pause or the debug
REPL, prints the watchpoint line, and maps Ctrl-C to a break request.
"""
from __future__ import annotations

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import TYPE_CHECKING, ContextManager

from msx.boundary import STOP_NONE, STOP_REQUESTED, STOP_WATCHPOINT, WATCH_READ, StopInfo

if TYPE_CHECKING:
    from host.debugger.prompt import Debugger
    from host.rpc_server import DebugServer
    from msx.core_api import CoreApi


def watch_hit_line(stop: StopInfo) -> str:
    """The debug-monitor "Watchpoint hit message" for a watchpoint stop."""
    kind = "READ " if stop.watch_access == WATCH_READ else "WRITE"
    return (
        f"\n[WP] {kind} {stop.watch_addr:04X}h = {stop.watch_value:02X}h"
        f"  PC={stop.pc:04X}h"
    )


def handle_stop(
    machine: CoreApi,
    stop_reason: int,
    debugger: Debugger | None,
    rpc_server: DebugServer | None,
) -> None:
    """Act on a step_frame stop: print the watchpoint line, then open the
    debug REPL or pause the RPC gate. A break request (Ctrl-C in the
    terminal) opens the REPL whenever there is a debugger, since a person
    at the terminal made it; any other stop pauses the RPC gate when a
    server runs, else opens the REPL (debug-monitor "The REPL opens after
    the core stops"). Does nothing for STOP_NONE. `stop_reason` is the
    FrameOutput's; after the early return the last-stop record is read once
    and gives the reason for every check (the two are equal)."""
    if stop_reason == STOP_NONE:
        return
    stop = machine.last_stop()
    if stop.reason == STOP_WATCHPOINT:
        print(watch_hit_line(stop))
    if debugger is not None and (stop.reason == STOP_REQUESTED or rpc_server is None):
        debugger.enter()
    elif rpc_server is not None:
        rpc_server.on_core_stop(stop.reason)


@contextmanager
def _sigint_handler(handler: Callable[[int, FrameType | None], object]) -> Iterator[None]:
    """While active, `handler` takes Ctrl-C. Restores the previous handler on
    exit. Off the main thread (where signal handlers cannot be set) it changes
    nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def sigint_requests_break(machine: CoreApi) -> ContextManager[None]:
    """While active, Ctrl-C asks the core to stop (request_break) instead of
    raising KeyboardInterrupt.

    Python runs the handler only between bytecodes of the main thread. The
    Python core runs as bytecode, so the request can stop the current
    step_frame call at the start of vertical blanking. A native core
    returns to Python only when step_frame returns, so the request stops
    the next call at its start, up to one frame later."""
    return _sigint_handler(lambda _sig, _frame: machine.request_break())


def sigint_interrupts() -> ContextManager[None]:
    """While active, Ctrl-C raises KeyboardInterrupt (the default), e.g. at
    the REPL prompt."""
    return _sigint_handler(signal.default_int_handler)
