"""Embedded Unix-domain-socket JSON-RPC server for the emulator.

The server listens on a Unix domain socket (``AF_UNIX``/``SOCK_STREAM``) on a
background daemon thread and exchanges newline-delimited JSON. All state-mutating
work is serialised onto the emulator thread: a request is parsed on the socket
thread, pushed onto a queue, and dispatched when the host loop calls
:meth:`DebugServer.drain` (once per frame, and while paused). This keeps the
CPU/VDP hot path free of locks.

Only the transport, dispatch, and pause plumbing live here. Method handlers are
registered in :meth:`DebugServer._register_handlers`.

Portability / crate-split note
------------------------------
This module is a self-contained *adapter*, not part of the emulator core: the
dependency direction is one-way. No module under ``msx/`` imports this file, and
``socket``/``queue``/``threading`` appear nowhere else in the core — this file is
the only place they live. The core (``machine``, ``cpu``, ``vdp``, ``memory``)
knows nothing about RPC; a break returns from ``step_frame`` with a stop reason
(an integer), and the host loop hands it to :meth:`DebugServer.on_core_stop`.
This adapter depends on the core, never the reverse.

A Rust/C++ port keeps the same layering as separate crates/targets:

    msx-core     cpu / vdp / memory / machine — no socket/json/thread deps;
                 a break is a stop reason in the step_frame result.
    msx-rpc      this DebugServer — depends on msx-core + serde_json, behind a
                 `#[cfg(feature = "rpc")]` gate (or a standalone crate) so JSON
                 never reaches the core.
    msx-frontend the SDL2 binary — depends on core, optionally on rpc.

Element mapping: Unix socket -> std `UnixListener` (no crate); JSON -> serde_json
(C++: nlohmann/json); daemon thread -> std::thread; queue bridge -> mpsc channel
(C++: queue + mutex + condition_variable); per-call reply Event -> oneshot channel
/ Condvar; dispatch table -> HashMap<String, fn>. Each handler's untyped
``params: dict[str, Any]`` (hand-validated via ``_require_int``/``_parse_addr``/
``_parse_hex_bytes``) maps to a `serde_json::Value` decoded into one typed
request/response struct per RPC method, dispatched via a tagged enum on the
``method`` field -- the Python side stays duck-typed at this boundary because
the wire payload's shape genuinely isn't known until parsed, matching every
other JSON-boundary module in this codebase (see ``machine_loader.py``'s
equivalent note for its YAML config boundary). The ``tools/`` MCP server and
CLI client are separate processes that speak only the newline-JSON wire protocol,
so they are language-agnostic and stay as-is under any core port.
"""
from __future__ import annotations

import base64
import enum
import json
import queue
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from host.debugger.disasm import disassemble
from host.input_state import InputState
from host.media import DiskFiles
from host.screenshot import encode_rgb24_png, scale_rgb24
from host.watch import watch_kind
from msx.boundary import (
    STATUS_OK,
    STOP_BREAKPOINT,
    STOP_REQUESTED,
    STOP_WATCHPOINT,
    VDP_KIND_V9938,
)
from msx.input import KEY_NAME_TO_CELL

if TYPE_CHECKING:
    from msx.core_api import CoreApi


class PauseReason(str, enum.Enum):
    """Why the emulator paused, as the RPC wire reports it (remote-socket-rpc
    "Debugger control methods"). Subclasses `str` so each member serializes to
    its wire value and compares equal to it.

    PORT-NOTE: a closed string-tagged enum at the JSON boundary.
    Rust equivalent: `enum PauseReason` with `#[serde(rename_all =
      "snake_case")]`.
    C++ equivalent: `enum class PauseReason` plus a to/from-string table.
    Kept as-is here because: it maps 1:1 to a closed enum.
    """

    USER_REQUEST = "user_request"
    BREAKPOINT = "breakpoint"
    WATCHPOINT = "watchpoint"
    STEP_COMPLETE = "step_complete"


# The wire reason for each core stop reason (msx/boundary.py STOP_*).
_STOP_TO_REASON: dict[int, PauseReason] = {
    STOP_BREAKPOINT: PauseReason.BREAKPOINT,
    STOP_WATCHPOINT: PauseReason.WATCHPOINT,
    STOP_REQUESTED: PauseReason.USER_REQUEST,
}

REASON_USER = PauseReason.USER_REQUEST
REASON_STEP = PauseReason.STEP_COMPLETE

DEFAULT_SOCKET_PATH = "/tmp/py_msx_emu.sock"
PROTOCOL_VERSION = "1.0"

# JSON-RPC / emulator error codes (see specs/socket-rpc).
ERR_PARSE = -32700
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_NEED_PAUSED = 1
ERR_INTERNAL = 3

_DISPATCH_TIMEOUT_S = 30.0

Handler = Callable[["DebugServer", dict[str, Any]], dict[str, Any]]


class RpcError(Exception):
    """Raised by a handler to return a JSON-RPC error with a specific code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def hex16(value: int) -> str:
    """Format a 16-bit value as ``0xXXXX``."""
    return f"0x{value & 0xFFFF:04X}"


def hex8(value: int) -> str:
    """Format an 8-bit value as ``0xXX``."""
    return f"0x{value & 0xFF:02X}"


class PauseState:
    """Shared run/pause state for the RPC server and the host (frontend) loop.

    The frontend loop reads :attr:`paused` to decide whether to step the machine;
    RPC handlers and on_core_stop write it. Kept as a tiny mutable
    object (not a dataclass with slots) so both sides hold the same reference.

    This is the adapter-side pause flag. The core keeps no pause state of its
    own: a break returns from ``step_frame``, and the host loop calls
    :meth:`DebugServer.on_core_stop`, which sets ``paused`` here.

    PORT-NOTE: these plain fields are written by the emulator thread
      (``on_pause``, handlers via ``drain``) and by the socket thread
      (``cpu.continue_sync`` sets it running), and read by the frontend
      loop -- safe here only because of the GIL. The socket thread makes no
      core API call.
    Rust equivalent: guard with a Mutex, or use AtomicBool/an atomic enum for
      `paused`/`reason` -- Rust has no GIL to fall back on for cross-thread
      plain-field access.
    C++ equivalent: same -- std::mutex or std::atomic<bool> plus a
      separately-synchronized `reason`, since C++ has no GIL either.
    Kept as-is here because: this file is an adapter-only layer (not the
      core emulator), so the cross-thread cost here is negligible either
      way; noted for the port since Python's GIL is doing implicit work a
      port must make explicit.
    """

    def __init__(self) -> None:
        self.paused: bool = False
        self.reason: str = REASON_USER

    def set_paused(self, reason: str) -> None:
        self.paused = True
        self.reason = reason

    def set_running(self) -> None:
        self.paused = False


class DebugServer:
    """Socket listener + queue bridge for the JSON-RPC debug interface."""

    def __init__(
        self,
        machine: "CoreApi",
        sock_path: str = DEFAULT_SOCKET_PATH,
        pause_state: PauseState | None = None,
        disk_files: DiskFiles | None = None,
    ) -> None:
        self._machine = machine
        # The file of each drive's disk, shared with the frontend and the
        # debugger of the same run (host/media.py DiskFiles).
        self._disk_files = disk_files if disk_files is not None else DiskFiles()
        self._sock_path = Path(sock_path)
        self._pause_state = pause_state if pause_state is not None else PauseState()
        self._queue: queue.Queue[tuple[dict[str, Any], list[dict[str, Any]], threading.Event]] = (
            queue.Queue()
        )
        self._srv: socket.socket | None = None
        self._client_conn: socket.socket | None = None
        self._client_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._handlers: dict[str, Handler] = {}
        # Set when the machine pauses; cpu.continue_sync waits on it.
        self._pause_event = threading.Event()
        # The last core stop, as cpu.continue_sync answers it: written on the
        # emulator thread before _pause_event is set, read on the socket
        # thread after the wait, so the socket thread never calls the core.
        self._last_pause: dict[str, Any] = {}
        # Breakpoint / watchpoint registries (id -> spec), owned here so the RPC
        # surface is decoupled from the interactive debugger's REPL state.
        self._breakpoints: dict[int, int] = {}
        self._watchpoints: dict[int, tuple[int, int]] = {}
        self._next_bp_id = 0
        self._next_wp_id = 0
        # Desired joystick state per port (1, 2): direction/trigger booleans.
        self._joy: dict[int, dict[str, bool]] = {
            1: _blank_joy_state(),
            2: _blank_joy_state(),
        }
        # Pending named-key auto-releases: (monotonic_deadline, row, bit).
        self._key_releases: list[tuple[float, int, int]] = []
        # The frontend's per-frame input accumulator, when one runs (set by
        # frontend.sdl2_frontend.run). Injected input goes to it too, so the
        # next step_frame keeps it.
        # Where injected input is built: the frontend's own builder when one
        # runs (it replaces this), so injection carries into its next frame.
        self.input_target: InputState = InputState()
        self._register_handlers()

    # -- lifecycle ----------------------------------------------------------

    @property
    def pause_state(self) -> PauseState:
        return self._pause_state

    @property
    def machine(self) -> "CoreApi":
        return self._machine

    @property
    def disk_files(self) -> DiskFiles:
        return self._disk_files

    def apply_injected_input(self) -> None:
        """Hand the builder's current input to the machine at once, so the
        next PPI/PSG read sees an injection even while paused (remote-socket-rpc
        "Input injection methods"); the frontend's next frame carries it on."""
        self._machine.apply_input(self.input_target.frame())

    def start(self) -> None:
        """Bind the socket and start accepting connections on a daemon thread."""
        self._sock_path.parent.mkdir(parents=True, exist_ok=True)
        self._sock_path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self._sock_path))
        srv.listen(1)
        self._srv = srv
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()

    def stop(self) -> None:
        """Close the listener and any client, and remove the socket file."""
        srv, self._srv = self._srv, None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        with self._client_lock:
            conn, self._client_conn = self._client_conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass
        self._sock_path.unlink(missing_ok=True)

    # -- core stops ---------------------------------------------------------

    def on_core_stop(self, stop_reason: int) -> None:
        """Pause for a core stop (a nonzero ``FrameOutput.stop_reason``).
        Runs on the emulator thread, after ``step_frame`` returned."""
        self.on_pause(_STOP_TO_REASON[stop_reason], self._machine.cpu_registers().PC)

    def on_pause(self, reason: str, pc: int) -> None:
        """Record the pause, wake cpu.continue_sync and push a notification.
        Runs on the emulator thread (on_core_stop). The record is complete
        before the event is set (Event orders the write before the waiter's
        read)."""
        registers = self.registers_dict()
        self._last_pause = {
            "paused": True, "reason": reason, "pc": hex16(pc), "registers": registers,
        }
        self._pause_state.set_paused(reason)
        self._pause_event.set()
        self._send_to_client({
            "notification": "paused", "reason": reason, "pc": hex16(pc),
            "registers": registers,
        })

    # -- socket threads -----------------------------------------------------

    def _accept_loop(self, srv: socket.socket) -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return  # listener closed
            with self._client_lock:
                self._client_conn = conn
            self._send(conn, {
                "notification": "connected",
                "version": PROTOCOL_VERSION,
                "emulator": "py-msx-emulator",
            })
            try:
                self._read_loop(conn)
            finally:
                with self._client_lock:
                    if self._client_conn is conn:
                        self._client_conn = None
                try:
                    conn.close()
                except OSError:
                    pass

    def _read_loop(self, conn: socket.socket) -> None:
        buf = b""
        while True:
            try:
                chunk = conn.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                self._handle_line(conn, line)

    def _handle_line(self, conn: socket.socket, line: bytes) -> None:
        try:
            req = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send(conn, {"id": None, "error": {"code": ERR_PARSE, "message": "parse error"}})
            return
        if not isinstance(req, dict):
            self._send(conn, {"id": None, "error": {"code": ERR_PARSE, "message": "parse error"}})
            return
        # cpu.continue_sync blocks until the next pause; handle it on this socket
        # thread (never the emulator thread) so the machine keeps running.
        if req.get("method") == "cpu.continue_sync":
            self._send(conn, self._continue_sync(req))
            return
        result_box: list[dict[str, Any]] = []
        done = threading.Event()
        self._queue.put((req, result_box, done))
        if done.wait(timeout=_DISPATCH_TIMEOUT_S) and result_box:
            self._send(conn, result_box[0])
        else:
            self._send(
                conn,
                {
                    "id": req.get("id"),
                    "error": {"code": ERR_INTERNAL, "message": "dispatch timeout"},
                },
            )

    # -- main-thread dispatch ----------------------------------------------

    def drain(self) -> None:
        """Process pending auto-releases and queued requests.

        MUST be called on the emulator thread (once per host-loop iteration).
        """
        self._process_key_releases()
        while True:
            try:
                req, result_box, done = self._queue.get_nowait()
            except queue.Empty:
                return
            result_box.append(self._dispatch(req))
            done.set()

    def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            return {
                "id": req_id,
                "error": {"code": ERR_INVALID_PARAMS, "message": "params must be an object"},
            }
        handler = self._handlers.get(method)
        if handler is None:
            return {
                "id": req_id,
                "error": {"code": ERR_METHOD_NOT_FOUND, "message": f"method not found: {method}"},
            }
        try:
            result = handler(self, params)
            return {"id": req_id, "result": result}
        except RpcError as exc:
            return {"id": req_id, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # noqa: BLE001 — surface any handler bug as internal error
            return {"id": req_id, "error": {"code": ERR_INTERNAL, "message": str(exc)}}

    # -- send helpers -------------------------------------------------------

    def _send_to_client(self, obj: dict[str, Any]) -> None:
        with self._client_lock:
            conn = self._client_conn
        if conn is not None:
            self._send(conn, obj)

    def _send(self, conn: socket.socket, obj: dict[str, Any]) -> None:
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self._send_lock:
            try:
                conn.sendall(data)
            except OSError:
                pass

    # -- shared handler helpers --------------------------------------------

    def registers_dict(self) -> dict[str, Any]:
        """Serialise the Z80 register file to the spec's hex-string shape."""
        r = self._machine.cpu_registers()
        return {
            "AF": hex16(r.AF), "BC": hex16(r.BC), "DE": hex16(r.DE), "HL": hex16(r.HL),
            "IX": hex16(r.IX), "IY": hex16(r.IY),
            "SP": hex16(r.SP), "PC": hex16(r.PC),
            "AF'": hex16(r.AF_), "BC'": hex16(r.BC_), "DE'": hex16(r.DE_), "HL'": hex16(r.HL_),
            "I": hex8(r.I), "R": hex8(r.R),
            "IFF1": bool(r.IFF1), "IFF2": bool(r.IFF2),
            "IM": r.IM,
        }

    def require_paused(self) -> None:
        if not self._pause_state.paused:
            raise RpcError(ERR_NEED_PAUSED, "emulator must be paused for this operation")

    @staticmethod
    def _ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"id": req_id, "result": result}

    @staticmethod
    def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"id": req_id, "error": {"code": code, "message": message}}

    def _continue_sync(self, req: dict[str, Any]) -> dict[str, Any]:
        """Resume and block (on the socket thread) until the next pause event.

        It makes no core API call: it answers from the pause record
        on_pause writes on the emulator thread before it sets the event
        (remote-socket-rpc "Threaded queue execution model").

        PORT-NOTE: the pause flag and the record cross threads through
          PauseState and threading.Event.
        Rust equivalent: send the record over an mpsc/oneshot channel, or
          keep it in a Mutex next to a Condvar.
        C++ equivalent: the same, with std::mutex and
          std::condition_variable.
        """
        req_id = req.get("id")
        params = req.get("params") or {}
        if not isinstance(params, dict):
            return self._err(req_id, ERR_INVALID_PARAMS, "params must be an object")
        if not self._pause_state.paused:
            return self._err(req_id, ERR_NEED_PAUSED, "emulator must be paused to continue")
        timeout_ms = params.get("timeout_ms", 5000)
        try:
            timeout_s = float(timeout_ms) / 1000.0
        except (TypeError, ValueError):
            return self._err(req_id, ERR_INVALID_PARAMS, "timeout_ms must be a number")
        self._pause_event.clear()
        self._pause_state.set_running()
        if self._pause_event.wait(timeout=timeout_s):
            return self._ok(req_id, self._last_pause)
        return self._ok(req_id, {"paused": False, "reason": "timeout"})

    def _process_key_releases(self) -> None:
        if not self._key_releases:
            return
        now = time.monotonic()
        still_pending: list[tuple[float, int, int]] = []
        for deadline, row, bit in self._key_releases:
            if now >= deadline:
                self.input_target.set_key_state(row, bit, False)
                self.apply_injected_input()
            else:
                still_pending.append((deadline, row, bit))
        self._key_releases = still_pending

    def schedule_key_release(self, row: int, bit: int, duration_ms: int) -> None:
        self._key_releases.append((time.monotonic() + duration_ms / 1000.0, row, bit))

    def capture_rgb(self) -> tuple[bytes, int, int]:
        """Render the current VDP frame to RGB24 without advancing the frame counter.

        Returns:
            (rgb_bytes, width, height) for the current frame.
        """
        frame = self._machine.render_preview()
        return frame.pixels, frame.width, frame.height

    def alloc_breakpoint(self, address: int) -> int:
        """Add a breakpoint and return its id. Raises an invalid-params
        RpcError, keeping the others, when the core rejects it (a fifth
        one)."""
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._breakpoints[bp_id] = address
        if self._machine.set_breakpoints(list(self._breakpoints.values())) != STATUS_OK:
            del self._breakpoints[bp_id]
            raise RpcError(ERR_INVALID_PARAMS, self._machine.last_error_text().decode("utf-8"))
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> bool:
        if bp_id not in self._breakpoints:
            return False
        del self._breakpoints[bp_id]
        self._machine.set_breakpoints(list(self._breakpoints.values()))
        return True

    def alloc_watchpoint(self, address: int, kind: int) -> int:
        """Add a watchpoint and return its id. Raises an invalid-params
        RpcError, keeping the others, when the core rejects it (a fifth
        one)."""
        wp_id = self._next_wp_id
        self._next_wp_id += 1
        self._watchpoints[wp_id] = (address, kind)
        if self._machine.set_watchpoints(list(self._watchpoints.values())) != STATUS_OK:
            del self._watchpoints[wp_id]
            raise RpcError(ERR_INVALID_PARAMS, self._machine.last_error_text().decode("utf-8"))
        return wp_id

    def remove_watchpoint(self, wp_id: int) -> bool:
        if wp_id not in self._watchpoints:
            return False
        del self._watchpoints[wp_id]
        self._machine.set_watchpoints(list(self._watchpoints.values()))
        return True

    @property
    def breakpoints(self) -> dict[int, int]:
        return self._breakpoints

    @property
    def joystick_state(self) -> dict[int, dict[str, bool]]:
        return self._joy

    # -- handler registration ----------------------------------------------

    def _register_handlers(self) -> None:
        self._handlers.update({
            "debugger.status": _h_debugger_status,
            "debugger.pause": _h_debugger_pause,
            "cpu.step": _h_cpu_step,
            "cpu.continue": _h_cpu_continue,
            "cpu.get_registers": _h_cpu_get_registers,
            "debug.set_breakpoint": _h_set_breakpoint,
            "debug.remove_breakpoint": _h_remove_breakpoint,
            "debug.list_breakpoints": _h_list_breakpoints,
            "debug.set_watchpoint": _h_set_watchpoint,
            "debug.remove_watchpoint": _h_remove_watchpoint,
            "memory.read": _h_memory_read,
            "memory.write": _h_memory_write,
            "memory.read_vram": _h_memory_read_vram,
            "memory.disassemble": _h_memory_disassemble,
            "vdp.get_registers": _h_vdp_get_registers,
            "vdp.get_status": _h_vdp_get_status,
            "input.press_key": _h_input_press_key,
            "input.release_key": _h_input_release_key,
            "input.press_key_named": _h_input_press_key_named,
            "input.joystick": _h_input_joystick,
            "input.joystick_release": _h_input_joystick_release,
            "screen.capture": _h_screen_capture,
            "state.save": _h_state_save,
            "state.load": _h_state_load,
            "fdd.swap": _h_fdd_swap,
        })


# ---------------------------------------------------------------------------
# Handlers  (signature: handler(server, params) -> result dict)
# ---------------------------------------------------------------------------

def _h_debugger_status(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    ps = server.pause_state
    return {
        "paused": ps.paused,
        "pc": hex16(server.machine.cpu_registers().PC),
        "reason": ps.reason,
    }


def _h_debugger_pause(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.pause_state.set_paused(REASON_USER)
    return {
        "paused": True,
        "pc": hex16(server.machine.cpu_registers().PC),
        "reason": REASON_USER,
    }


# ── CPU / execution ──────────────────────────────────────────────────────────

def _h_cpu_step(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    m = server.machine
    pc_before = m.cpu_registers().PC
    mnemonic, _size = disassemble(_byte_reader(m), pc_before)
    t_states = m.step()
    server.pause_state.set_paused(REASON_STEP)
    return {
        "pc": hex16(m.cpu_registers().PC),
        "t_states": t_states,
        "mnemonic": mnemonic,
        "registers": server.registers_dict(),
    }


def _h_cpu_continue(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    server.pause_state.set_running()
    return {"running": True}


def _h_cpu_get_registers(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"registers": server.registers_dict()}


# ── Breakpoints / watchpoints ────────────────────────────────────────────────

def _h_set_breakpoint(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    bp_id = server.alloc_breakpoint(addr)
    return {"id": bp_id, "address": hex16(addr), "active": True}


def _h_remove_breakpoint(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"removed": server.remove_breakpoint(_require_int(params, "id"))}


def _h_list_breakpoints(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "breakpoints": [
            {"id": i, "address": hex16(a), "active": True}
            for i, a in sorted(server.breakpoints.items())
        ]
    }


def _h_set_watchpoint(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    mode = params.get("mode", "rw")
    kind = watch_kind(mode) if mode in ("r", "w", "rw") else None
    if kind is None:
        raise RpcError(ERR_INVALID_PARAMS, f"invalid watchpoint mode: {mode}")
    wp_id = server.alloc_watchpoint(addr, kind)
    return {"id": wp_id, "address": hex16(addr), "mode": mode, "active": True}


def _h_remove_watchpoint(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"removed": server.remove_watchpoint(_require_int(params, "id"))}


# ── Memory ──────────────────────────────────────────────────────────────────

def _h_memory_read(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    length = _require_int(params, "length", default=16)
    if not 0 <= length <= 0x10000:
        raise RpcError(ERR_INVALID_PARAMS, f"length {length} is outside 0-0x10000")
    data = _fmt_bytes(server.machine.read_memory(addr, length))
    return {"address": hex16(addr), "data": data}


def _h_memory_write(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    addr = _parse_addr(params.get("address"))
    values = _parse_hex_bytes(params.get("data"))
    server.machine.write_memory(addr, bytes(values))
    return {"written": len(values)}


def _h_memory_read_vram(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    size = server.machine.vram_size()
    # The VRAM size is a power of two (0x4000 or 0x20000): the mask wraps the
    # address at it, as the core does.
    addr = _parse_addr(params.get("address"), size - 1)
    length = _require_int(params, "length", default=32)
    if not 0 <= length <= size:
        raise RpcError(ERR_INVALID_PARAMS, f"length {length} is outside 0-{size:#x}")
    data = _fmt_bytes(server.machine.read_vram(addr, length))
    # Four hex digits below 0x10000, five above (hex16 would cut it).
    return {"address": f"0x{addr:04X}", "data": data}


def _h_memory_disassemble(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    count = _require_int(params, "count", default=10)
    read = _byte_reader(server.machine)
    instructions: list[dict[str, Any]] = []
    cur = addr
    for _ in range(count):
        mnemonic, size = disassemble(read, cur)
        raw = _fmt_bytes(read((cur + i) & 0xFFFF) for i in range(size))
        instructions.append({"address": hex16(cur), "bytes": raw, "mnemonic": mnemonic})
        cur = (cur + size) & 0xFFFF
    return {"instructions": instructions}


# ── VDP ─────────────────────────────────────────────────────────────────────

def _h_vdp_get_registers(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    info = server.machine.vdp_info()
    vtype = "V9938" if info.kind == VDP_KIND_V9938 else "TMS9918A"
    registers = {f"R{i}": hex8(v) for i, v in enumerate(info.regs)}
    return {"type": vtype, "registers": registers}


def _h_vdp_get_status(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"status": hex8(server.machine.vdp_info().status[0])}


# ── Input injection ──────────────────────────────────────────────────────────

def _h_input_press_key(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    row = _require_int(params, "row")
    bit = _require_int(params, "bit")
    _set_key(server, row, bit, True)
    return {"row": row, "bit": bit, "pressed": True}


def _h_input_release_key(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    row = _require_int(params, "row")
    bit = _require_int(params, "bit")
    _set_key(server, row, bit, False)
    return {"row": row, "bit": bit, "pressed": False}


def _h_input_press_key_named(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("key")
    if not isinstance(name, str):
        raise RpcError(ERR_INVALID_PARAMS, "key must be a string")
    cell = KEY_NAME_TO_CELL.get(name.upper())
    if cell is None:
        raise RpcError(ERR_INVALID_PARAMS, f"unknown key name: {name}")
    duration_ms = _require_int(params, "duration_ms", default=100)
    row, bit = cell
    server.input_target.set_key_state(row, bit, True)
    server.apply_injected_input()
    server.schedule_key_release(row, bit, duration_ms)
    return {"key": name.upper(), "row": row, "bit": bit, "duration_ms": duration_ms}


def _h_input_joystick(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    port = _require_int(params, "port", default=1)
    if port not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "port must be 1 or 2")
    state = server.joystick_state[port]
    port_idx = port - 1
    for name, bit in _JOY_BITS.items():
        pressed = bool(params.get(name, False))
        state[name] = pressed
        if pressed:
            server.input_target.joystick_button_down(port_idx, bit)
        else:
            server.input_target.joystick_button_up(port_idx, bit)
    server.apply_injected_input()
    return {"port": port, "state": dict(state)}


def _h_input_joystick_release(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    port = _require_int(params, "port", default=1)
    if port not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "port must be 1 or 2")
    state = server.joystick_state[port]
    port_idx = port - 1
    for name, bit in _JOY_BITS.items():
        state[name] = False
        server.input_target.joystick_button_up(port_idx, bit)
    server.apply_injected_input()
    return {"port": port, "released": True}


# ── Screenshot ───────────────────────────────────────────────────────────────

def _h_screen_capture(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    scale = _require_int(params, "scale", default=1)
    rgb, width, height = server.capture_rgb()
    if scale > 1:
        out_w, out_h = width * scale, height * scale
        rgb = scale_rgb24(rgb, width, height, out_w, out_h)
        width, height = out_w, out_h
    png = encode_rgb24_png(rgb, width, height)
    return {
        "width": width,
        "height": height,
        "format": "png",
        "encoding": "base64",
        "data": base64.b64encode(png).decode("ascii"),
    }


# ── State save / load ────────────────────────────────────────────────────────

def _h_state_save(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    from host.state_files import save_state
    from msx.boundary import RgbFrame

    path = params.get("path")
    title = Path(path).stem if isinstance(path, str) and path else "rpc_checkpoint"
    rgb, width, height = server.capture_rgb()
    saved = save_state(server.machine, RgbFrame(width, height, rgb), title, server.disk_files)
    return {"path": str(saved)}


def _h_state_load(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    from host.state_files import load_state

    path = params.get("path")
    if not isinstance(path, str) or not path:
        raise RpcError(ERR_INVALID_PARAMS, "path is required")
    load_state(server.machine, Path(path))
    return {"path": path, "loaded": True}


# ── Floppy disk ──────────────────────────────────────────────────────────────

def _h_fdd_swap(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    from host.media import swap_disk

    drive = _require_int(params, "drive")
    if drive not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "drive must be 1 or 2")
    if server.machine.drive_count() == 0:
        raise RpcError(ERR_INTERNAL, "machine has no floppy interface")
    path = params.get("path")
    drive_idx = drive - 1
    if path:
        swap_disk(server.machine, server.disk_files, drive_idx, Path(str(path)))
        return {"drive": drive, "path": str(path), "mounted": True}
    swap_disk(server.machine, server.disk_files, drive_idx, None)
    return {"drive": drive, "path": None, "mounted": False}


# ---------------------------------------------------------------------------
# Handler helpers
# ---------------------------------------------------------------------------

# Per-joystick direction/trigger bit within the 6-bit active-low port state.
_JOY_BITS: dict[str, int] = {
    "up": 0, "down": 1, "left": 2, "right": 3, "trigger_a": 4, "trigger_b": 5,
}


def _byte_reader(core: "CoreApi") -> Callable[[int], int]:
    """A one-byte memory reader over the core API, for the disassembler."""

    def read(addr: int) -> int:
        return core.read_memory(addr, 1)[0]

    return read


class _Missing:
    pass


_MISSING = _Missing()


def _blank_joy_state() -> dict[str, bool]:
    return {name: False for name in _JOY_BITS}


def _set_key(server: DebugServer, row: int, bit: int, pressed: bool) -> None:
    try:
        server.input_target.set_key_state(row, bit, pressed)
    except ValueError as exc:
        raise RpcError(ERR_INVALID_PARAMS, str(exc)) from None
    server.apply_injected_input()


def _parse_addr(value: Any, mask: int = 0xFFFF) -> int:
    """An address param (an integer or a hex string), wrapped by `mask`."""
    if isinstance(value, bool):
        raise RpcError(ERR_INVALID_PARAMS, "address must be an integer or hex string")
    if isinstance(value, int):
        return value & mask
    if not isinstance(value, str) or not value:
        raise RpcError(ERR_INVALID_PARAMS, "address is required")
    try:
        return int(value, 16) & mask
    except ValueError:
        raise RpcError(ERR_INVALID_PARAMS, f"invalid address: {value}") from None


def _require_int(params: dict[str, Any], name: str, default: Any = _MISSING) -> int:
    if name not in params:
        if isinstance(default, _Missing):
            raise RpcError(ERR_INVALID_PARAMS, f"missing required param: {name}")
        return int(default)
    value = params[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RpcError(ERR_INVALID_PARAMS, f"{name} must be an integer")
    return int(value)


def _fmt_bytes(values: Any) -> str:
    return " ".join(f"{v & 0xFF:02X}" for v in values)


def _parse_hex_bytes(data: Any) -> list[int]:
    if not isinstance(data, str):
        raise RpcError(ERR_INVALID_PARAMS, "data must be a space-separated hex string")
    out: list[int] = []
    for token in data.split():
        try:
            out.append(int(token, 16) & 0xFF)
        except ValueError:
            raise RpcError(ERR_INVALID_PARAMS, f"invalid hex byte: {token}") from None
    return out


