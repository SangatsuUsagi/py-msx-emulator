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
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

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
    the core stops"). Does nothing for STOP_NONE."""
    if stop_reason == STOP_NONE:
        return
    stop = machine.last_stop()
    if stop.reason == STOP_WATCHPOINT:
        print(watch_hit_line(stop))
    if debugger is not None and (stop_reason == STOP_REQUESTED or rpc_server is None):
        debugger.enter()
    elif rpc_server is not None:
        rpc_server.on_core_stop(stop_reason)


@contextmanager
def sigint_requests_break(machine: CoreApi) -> Iterator[None]:
    """While active, Ctrl-C asks the core to stop (request_break) instead of
    raising KeyboardInterrupt. Restores the previous handler on exit. Off
    the main thread (where signal handlers cannot be set) it changes
    nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, lambda _sig, _frame: machine.request_break())
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


@contextmanager
def sigint_interrupts() -> Iterator[None]:
    """While active, Ctrl-C raises KeyboardInterrupt (the default), e.g. at
    the REPL prompt. Restores the previous handler on exit. Off the main
    thread it changes nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
