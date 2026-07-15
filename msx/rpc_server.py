"""Embedded Unix-domain-socket JSON-RPC server for the emulator.

The server listens on a Unix domain socket (``AF_UNIX``/``SOCK_STREAM``) on a
background daemon thread and exchanges newline-delimited JSON. All state-mutating
work is serialised onto the emulator thread: a request is parsed on the socket
thread, pushed onto a queue, and dispatched when the host loop calls
:meth:`DebugServer.drain` (once per frame, and while paused). This keeps the
CPU/VDP hot path free of locks.

Only the transport, dispatch, and pause plumbing live here. Method handlers are
registered in :meth:`DebugServer._register_handlers`.

Portability note: the socket/threading layer is host glue and would be rewritten
per platform in a Rust/C++ port; the dispatch table and handler contract
(``handler(server, params) -> dict``) are the portable part.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from msx.machine import Machine

DEFAULT_SOCKET_PATH = "/tmp/py_msx_emu.sock"
PROTOCOL_VERSION = "1.0"

# JSON-RPC / emulator error codes (see specs/socket-rpc).
ERR_PARSE = -32700
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_NEED_PAUSED = 1
ERR_NEED_RUNNING = 2
ERR_INTERNAL = 3

# Valid pause reasons pushed to clients and reported by debugger.status.
REASON_USER = "user_request"
REASON_BREAKPOINT = "breakpoint"
REASON_WATCHPOINT = "watchpoint"
REASON_STEP = "step_complete"

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
        self._push_paused(reason, pc)

    def _push_paused(self, reason: str, pc: int) -> None:
        frame = {
            "notification": "paused",
            "reason": reason,
            "pc": hex16(pc),
            "registers": self._registers_dict(),
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
        """Process all queued requests. MUST be called on the emulator thread."""
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

    def _registers_dict(self) -> dict[str, Any]:
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

    # -- handler registration ----------------------------------------------

    def _register_handlers(self) -> None:
        self._handlers.update({
            "debugger.status": _h_debugger_status,
            "debugger.pause": _h_debugger_pause,
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
