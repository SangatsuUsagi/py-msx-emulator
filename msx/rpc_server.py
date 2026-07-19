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
knows nothing about RPC; it only exposes a generic pause seam
(``Machine.set_pause_hook(Callable[[str, int], None])``). This adapter depends on
the core, never the reverse.

A Rust/C++ port keeps the same layering as separate crates/targets:

    msx-core     cpu / vdp / memory / machine — no socket/json/thread deps;
                 the pause seam becomes a `trait PauseSink` (and the reason
                 strings become an `enum PauseReason`).
    msx-rpc      this DebugServer — depends on msx-core + serde_json, behind a
                 `#[cfg(feature = "rpc")]` gate (or a standalone crate) so JSON
                 never reaches the core.
    msx-frontend the SDL2 binary — depends on core, optionally on rpc.

Element mapping: Unix socket -> std `UnixListener` (no crate); JSON -> serde_json
(C++: nlohmann/json); daemon thread -> std::thread; queue bridge -> mpsc channel
(C++: queue + mutex + condition_variable); per-call reply Event -> oneshot channel
/ Condvar; dispatch table -> HashMap<String, fn>. The ``tools/`` MCP server and
CLI client are separate processes that speak only the newline-JSON wire protocol,
so they are language-agnostic and stay as-is under any core port.
"""
from __future__ import annotations

import base64
import json
import queue
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from msx.debugger.disasm import disassemble
from msx.input import KEY_NAME_TO_CELL
from msx.machine import PauseReason
from msx.screenshot import encode_rgb24_png, render_current_rgb24, scale_rgb24

if TYPE_CHECKING:
    from msx.machine import Machine

# Pause reasons come from msx.machine.PauseReason, the single source of truth;
# aliased here for the handler call sites. The machine emits BREAKPOINT /
# WATCHPOINT directly through the pause hook.
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
    RPC handlers and the machine pause hook write it. Kept as a tiny mutable
    object (not a dataclass with slots) so both sides hold the same reference.

    This is the adapter-side pause flag; its core-side counterpart is
    ``Machine._pause_requested`` / ``_resume_skip_pc`` (which drive the debug
    frame loop). :meth:`DebugServer.on_pause` is where the two meet: the machine
    sets ``_pause_requested`` and calls the hook, which sets ``paused`` here.

    Threading / portability: these plain fields are written by the emulator
    thread (``on_pause``, handlers via ``drain``) and by the socket thread
    (``cpu.continue_sync``), and read by the frontend loop — safe here only
    because of the GIL. A Rust/C++ port must guard this with a mutex/atomics.
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
        machine: "Machine",
        sock_path: str = DEFAULT_SOCKET_PATH,
        pause_state: PauseState | None = None,
    ) -> None:
        self._machine = machine
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
        # Breakpoint / watchpoint registries (id -> spec), owned here so the RPC
        # surface is decoupled from the interactive debugger's REPL state.
        self._breakpoints: dict[int, int] = {}
        self._watchpoints: dict[int, tuple[int, str]] = {}
        self._next_bp_id = 0
        self._next_wp_id = 0
        # Desired joystick state per port (1, 2): direction/trigger booleans.
        self._joy: dict[int, dict[str, bool]] = {
            1: _blank_joy_state(),
            2: _blank_joy_state(),
        }
        # Pending named-key auto-releases: (monotonic_deadline, row, bit).
        self._key_releases: list[tuple[float, int, int]] = []
        self._register_handlers()

    # -- lifecycle ----------------------------------------------------------

    @property
    def pause_state(self) -> PauseState:
        return self._pause_state

    @property
    def machine(self) -> "Machine":
        return self._machine

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

    # -- pause hook ---------------------------------------------------------

    def on_pause(self, reason: str, pc: int) -> None:
        """Machine pause-hook target: record the pause and push a notification.

        Installed via ``machine.set_pause_hook(server.on_pause)``. Runs on the
        emulator thread when a break event fires.
        """
        self._pause_state.set_paused(reason)
        self._pause_event.set()
        self._push_paused(reason, pc)

    def _push_paused(self, reason: str, pc: int) -> None:
        frame = {
            "notification": "paused",
            "reason": reason,
            "pc": hex16(pc),
            "registers": self.registers_dict(),
        }
        self._send_to_client(frame)

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
        r = self._machine.cpu.registers
        return {
            "AF": hex16(r.AF), "BC": hex16(r.BC), "DE": hex16(r.DE), "HL": hex16(r.HL),
            "IX": hex16(r.IX), "IY": hex16(r.IY),
            "SP": hex16(r.SP), "PC": hex16(r.PC),
            "AF'": hex16(r.AF_), "BC'": hex16(r.BC_), "DE'": hex16(r.DE_), "HL'": hex16(r.HL_),
            "I": hex8(r.I), "R": hex8(r.R),
            "IFF1": self._machine.cpu.iff1, "IFF2": self._machine.cpu.iff2,
            "IM": self._machine.cpu.im,
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

        Portability seam: this runs on the socket thread yet calls into the core
        (``Machine.prepare_resume`` and reading ``cpu.registers.PC``). It is safe
        here only because of the GIL and because the machine is quiescent by the
        time the pause event fires (the debug loop has returned). A Rust/C++ port
        cannot take ``&mut Machine`` from two threads; it must route continue as a
        command handled on the emulator thread, keeping the pause hook as the only
        callback out of the core.
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
        self._machine.prepare_resume()
        self._pause_state.set_running()
        if self._pause_event.wait(timeout=timeout_s):
            return self._ok(req_id, {
                "paused": True,
                "reason": self._pause_state.reason,
                "pc": hex16(self._machine.cpu.registers.PC),
                "registers": self.registers_dict(),
            })
        return self._ok(req_id, {"paused": False, "reason": "timeout"})

    def _process_key_releases(self) -> None:
        if not self._key_releases:
            return
        now = time.monotonic()
        still_pending: list[tuple[float, int, int]] = []
        for deadline, row, bit in self._key_releases:
            if now >= deadline:
                self._machine.input.set_key_state(row, bit, False)
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
        return render_current_rgb24(self._machine.vdp)

    def alloc_breakpoint(self, address: int) -> int:
        bp_id = self._next_bp_id
        self._next_bp_id += 1
        self._breakpoints[bp_id] = address
        self._machine.set_breakpoints(list(self._breakpoints.values()))
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> bool:
        if bp_id not in self._breakpoints:
            return False
        del self._breakpoints[bp_id]
        self._machine.set_breakpoints(list(self._breakpoints.values()))
        return True

    def alloc_watchpoint(self, address: int, mode: str) -> int:
        wp_id = self._next_wp_id
        self._next_wp_id += 1
        self._watchpoints[wp_id] = (address, mode)
        self._machine.set_watchpoints(list(self._watchpoints.values()))
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
        "pc": hex16(server.machine.cpu.registers.PC),
        "reason": ps.reason,
    }


def _h_debugger_pause(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.pause_state.set_paused(REASON_USER)
    return {
        "paused": True,
        "pc": hex16(server.machine.cpu.registers.PC),
        "reason": REASON_USER,
    }


# ── CPU / execution ──────────────────────────────────────────────────────────

def _h_cpu_step(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    m = server.machine
    pc_before = m.cpu.registers.PC
    mnemonic, _size = disassemble(m.memory.read, pc_before)
    t_states = m.step()
    server.pause_state.set_paused(REASON_STEP)
    return {
        "pc": hex16(m.cpu.registers.PC),
        "t_states": t_states,
        "mnemonic": mnemonic,
        "registers": server.registers_dict(),
    }


def _h_cpu_continue(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    server.machine.prepare_resume()
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
    if mode not in ("r", "w", "rw"):
        raise RpcError(ERR_INVALID_PARAMS, f"invalid watchpoint mode: {mode}")
    wp_id = server.alloc_watchpoint(addr, mode)
    return {"id": wp_id, "address": hex16(addr), "mode": mode, "active": True}


def _h_remove_watchpoint(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"removed": server.remove_watchpoint(_require_int(params, "id"))}


# ── Memory ──────────────────────────────────────────────────────────────────

def _h_memory_read(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    length = _require_int(params, "length", default=16)
    read = server.machine.memory.read
    data = _fmt_bytes(read((addr + i) & 0xFFFF) for i in range(length))
    return {"address": hex16(addr), "data": data}


def _h_memory_write(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    server.require_paused()
    addr = _parse_addr(params.get("address"))
    values = _parse_hex_bytes(params.get("data"))
    write = server.machine.memory.write
    for i, value in enumerate(values):
        write((addr + i) & 0xFFFF, value)
    return {"written": len(values)}


def _h_memory_read_vram(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    length = _require_int(params, "length", default=32)
    vram = server.machine.vdp.vram
    size = len(vram)
    data = _fmt_bytes(vram[(addr + i) % size] for i in range(length))
    return {"address": hex16(addr), "data": data}


def _h_memory_disassemble(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    addr = _parse_addr(params.get("address"))
    count = _require_int(params, "count", default=10)
    read = server.machine.memory.read
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
    from msx.vdp.v9938 import V9938

    vdp = server.machine.vdp
    vtype = "V9938" if isinstance(vdp, V9938) else "TMS9918A"
    registers = {f"R{i}": hex8(v) for i, v in enumerate(vdp.regs)}
    return {"type": vtype, "registers": registers}


def _h_vdp_get_status(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    return {"status": hex8(server.machine.vdp.status)}


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
    server.machine.input.set_key_state(row, bit, True)
    server.schedule_key_release(row, bit, duration_ms)
    return {"key": name.upper(), "row": row, "bit": bit, "duration_ms": duration_ms}


def _h_input_joystick(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    port = _require_int(params, "port", default=1)
    if port not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "port must be 1 or 2")
    state = server.joystick_state[port]
    inp = server.machine.input
    port_idx = port - 1
    for name, bit in _JOY_BITS.items():
        pressed = bool(params.get(name, False))
        state[name] = pressed
        if pressed:
            inp.joystick_button_down(port_idx, bit)
        else:
            inp.joystick_button_up(port_idx, bit)
    return {"port": port, "state": dict(state)}


def _h_input_joystick_release(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    port = _require_int(params, "port", default=1)
    if port not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "port must be 1 or 2")
    state = server.joystick_state[port]
    inp = server.machine.input
    port_idx = port - 1
    for name, bit in _JOY_BITS.items():
        state[name] = False
        inp.joystick_button_up(port_idx, bit)
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
    from msx.machine import SCREEN_HEIGHT, SCREEN_WIDTH
    from msx.state import save_state

    path = params.get("path")
    title = Path(path).stem if isinstance(path, str) and path else "rpc_checkpoint"
    rgb, width, height = server.capture_rgb()
    # save_state writes a fixed native-resolution (256x192) thumbnail, so match
    # that size for V9938 modes whose frame can be taller (e.g. 212 lines).
    if (width, height) != (SCREEN_WIDTH, SCREEN_HEIGHT):
        rgb = scale_rgb24(rgb, width, height, SCREEN_WIDTH, SCREEN_HEIGHT)
    saved = save_state(server.machine, rgb, title)
    return {"path": str(saved)}


def _h_state_load(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    from msx.state import load_state

    path = params.get("path")
    if not isinstance(path, str) or not path:
        raise RpcError(ERR_INVALID_PARAMS, "path is required")
    load_state(server.machine, Path(path))
    return {"path": path, "loaded": True}


# ── Floppy disk ──────────────────────────────────────────────────────────────

def _h_fdd_swap(server: DebugServer, params: dict[str, Any]) -> dict[str, Any]:
    from msx.fdc.disk_image import DskDiskImage

    drive = _require_int(params, "drive")
    if drive not in (1, 2):
        raise RpcError(ERR_INVALID_PARAMS, "drive must be 1 or 2")
    fdc = server.machine.fdc
    if fdc is None:
        raise RpcError(ERR_INTERNAL, "machine has no floppy interface")
    path = params.get("path")
    drive_idx = drive - 1
    if path:
        fdc.swap(drive_idx, DskDiskImage(str(path)))
        return {"drive": drive, "path": str(path), "mounted": True}
    fdc.swap(drive_idx, None)
    return {"drive": drive, "path": None, "mounted": False}


# ---------------------------------------------------------------------------
# Handler helpers
# ---------------------------------------------------------------------------

# Per-joystick direction/trigger bit within the 6-bit active-low port state.
_JOY_BITS: dict[str, int] = {
    "up": 0, "down": 1, "left": 2, "right": 3, "trigger_a": 4, "trigger_b": 5,
}


class _Missing:
    pass


_MISSING = _Missing()


def _blank_joy_state() -> dict[str, bool]:
    return {name: False for name in _JOY_BITS}


def _set_key(server: DebugServer, row: int, bit: int, pressed: bool) -> None:
    try:
        server.machine.input.set_key_state(row, bit, pressed)
    except ValueError as exc:
        raise RpcError(ERR_INVALID_PARAMS, str(exc)) from None


def _parse_addr(value: Any) -> int:
    if isinstance(value, bool):
        raise RpcError(ERR_INVALID_PARAMS, "address must be an integer or hex string")
    if isinstance(value, int):
        return value & 0xFFFF
    if not isinstance(value, str) or not value:
        raise RpcError(ERR_INVALID_PARAMS, "address is required")
    try:
        return int(value, 16) & 0xFFFF
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


