# py-msx-emulator: Socket RPC & MCP Server Specification

> **Purpose:** This document specifies two integration layers that together let Claude Code
> control a running `py-msx-emulator` instance as a first-class MCP tool:
>
> - **Level 2 — Socket RPC:** A Unix-domain-socket JSON-RPC server embedded in the
>   emulator process. Any local client (shell script, Python, `socat`) can call it.
> - **Level 3 — MCP Server:** A standalone `stdio`-transport MCP server
>   (`tools/mcp_server.py`) that wraps the Socket RPC. Claude Code registers it once
>   with `claude mcp add` and then calls emulator functions as native MCP tools.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  Claude Code session                                    │
│                                                         │
│  Tool call: emulator_step()                             │
│         ↓                                               │
│  MCP Client (built into Claude Code)                    │
└────────────────────┬────────────────────────────────────┘
                     │ stdio (JSON-RPC 2.0 / MCP protocol)
                     ▼
┌────────────────────────────────────────────────────────┐
│  tools/mcp_server.py  (Level 3 — MCP Server)           │
│                                                         │
│  FastMCP tool definitions                               │
│  ↕  calls _rpc(method, params) helper                  │
└────────────────────┬───────────────────────────────────┘
                     │ Unix socket  /tmp/py_msx_emu.sock
                     │ Newline-delimited JSON
                     ▼
┌────────────────────────────────────────────────────────┐
│  py-msx-emulator process                               │
│                                                         │
│  msx/rpc_server.py   (Level 2 — Socket RPC)            │
│  ↕  queue.Queue  ↕                                     │
│  msx/machine.py  (main emulator loop)                  │
│    ├─ msx/cpu/z80.py                                   │
│    ├─ msx/vdp/  (TMS9918A / V9938)                     │
│    ├─ msx/input.py   (keyboard / joystick matrix)      │
│    ├─ msx/debugger/repl.py                             │
│    └─ msx/screenshot.py                                │
└────────────────────────────────────────────────────────┘
```

### Why two layers?

| Concern | Socket RPC | MCP Server |
|---|---|---|
| Who uses it | Shell scripts, `socat`, test harness | Claude Code (MCP client) |
| Protocol | Newline-delimited JSON over Unix socket | MCP (JSON-RPC 2.0 over stdio) |
| Structured tool descriptions | No | Yes — Claude sees typed parameters and docstrings |
| Screenshot as image content | Returns base64 string | Returns `ImageContent` block — Claude can *see* the frame |
| Required for Claude Code integration | Needed by MCP server | Yes |

The MCP server is a thin adapter; all real work happens in the Socket RPC layer.

---

## Feasibility Summary

| Capability | Feasibility | Rationale |
|---|---|---|
| **Debugger control** | ✅ Green | `msx/debugger/repl.py` dispatches string commands through a central handler; the same function can be called from a socket thread without touching the REPL loop. |
| **Game input injection** | ✅ Green | `msx/input.py` holds an `InputState` object with an `_rows[11]` byte array; any code with a reference to that object can set/clear bits directly, and the PPI reads the same array every frame. |
| **Screenshot capture** | ✅ Green | `msx/screenshot.py` already exports a function that returns a PNG byte stream (used by F10 and save-state); the RPC returns base64-encoded PNG; the MCP server returns it as `ImageContent` so Claude can visually inspect the frame. |

---

---

# Part 1: Level 2 — Socket RPC

## Transport

| Property | Value |
|---|---|
| Socket type | Unix domain socket (`AF_UNIX`, `SOCK_STREAM`) |
| Default path | `/tmp/py_msx_emu.sock` (configurable via `--rpc-socket PATH`) |
| Framing | Newline-delimited JSON — one JSON object per line (`\n`) |
| Encoding | UTF-8 |
| Concurrency | Single active client at a time; server serialises all calls via a queue into the emulator thread |

## Wire Format

### Request

```json
{ "id": "<string>", "method": "<string>", "params": { ... } }
```

- `id` — caller-chosen correlation string; echoed back in the response.
- `method` — one of the methods listed below.
- `params` — method-specific object; may be omitted for zero-argument methods.

### Success Response

```json
{ "id": "<string>", "result": { ... } }
```

### Error Response

```json
{ "id": "<string>", "error": { "code": <int>, "message": "<string>" } }
```

#### Error Codes

| Code | Meaning |
|---|---|
| `-32700` | Parse error (malformed JSON) |
| `-32601` | Method not found |
| `-32602` | Invalid params |
| `1` | Emulator not in debug (paused) state |
| `2` | Emulator is paused; operation requires running state |
| `3` | Internal emulator error |

## Execution Model

The emulator runs its main loop on the **main thread**. The RPC server runs on a
**background thread**. All state-mutating calls are dispatched through a `queue.Queue`
and processed by the main thread between frames (or during the debugger REPL pause).

---

## Socket RPC Method Reference

### `debugger.pause`

Pause emulation (equivalent to Ctrl+C).

**Params:** none

**Result:**
```json
{ "paused": true, "pc": "0x4012", "reason": "user_request" }
```
`reason`: `"user_request"` | `"breakpoint"` | `"watchpoint"` | `"step_complete"`

---

### `debugger.status`

Return current pause/run state without changing it.

**Params:** none

**Result:**
```json
{ "paused": true, "pc": "0xC000", "reason": "breakpoint" }
```

---

### `cpu.step`

Execute exactly one Z80 instruction. **Requires paused state.**

**Params:** none

**Result:**
```json
{
  "pc": "0x4015",
  "t_states": 7,
  "mnemonic": "LD A, (HL)",
  "registers": {
    "AF": "0x1234", "BC": "0x5678", "DE": "0x9ABC", "HL": "0xDEF0",
    "IX": "0x0000", "IY": "0xF3C0",
    "SP": "0xF380", "PC": "0x4015",
    "AF'": "0x0000", "BC'": "0x0000", "DE'": "0x0000", "HL'": "0x0000",
    "I": "0x00", "R": "0x12",
    "IFF1": true, "IFF2": true, "IM": 1
  }
}
```

---

### `cpu.continue`

Resume execution. Returns immediately. **Requires paused state.**

**Params:** none  
**Result:** `{ "running": true }`

---

### `cpu.continue_sync`

Resume and block until the next pause event.

**Params:** `{ "timeout_ms": 5000 }`  
**Result (pause):** `{ "paused": true, "reason": "breakpoint", "pc": "0xC100", "registers": { ... } }`  
**Result (timeout):** `{ "paused": false, "reason": "timeout" }`

---

### `cpu.get_registers`

Read all CPU registers (works in any state).

**Params:** none  
**Result:** same `registers` object as in `cpu.step`.

---

### `debug.set_breakpoint`

**Params:** `{ "address": "0xC000" }`  
**Result:** `{ "id": 0, "address": "0xC000", "active": true }`

---

### `debug.remove_breakpoint`

**Params:** `{ "id": 0 }`  
**Result:** `{ "removed": true }`

---

### `debug.list_breakpoints`

**Params:** none  
**Result:** `{ "breakpoints": [ { "id": 0, "address": "0xC000", "active": true } ] }`

---

### `debug.set_watchpoint`

**Params:** `{ "address": "0xE000", "mode": "rw" }`  
`mode`: `"r"` | `"w"` | `"rw"` (default `"rw"`)  
**Result:** `{ "id": 0, "address": "0xE000", "mode": "rw", "active": true }`

---

### `debug.remove_watchpoint`

**Params:** `{ "id": 0 }`  
**Result:** `{ "removed": true }`

---

### `memory.read`

**Params:** `{ "address": "0xC000", "length": 16 }`  
**Result:** `{ "address": "0xC000", "data": "3E 01 32 00 C0 C9 ..." }`

---

### `memory.write`

**Requires paused state.**

**Params:** `{ "address": "0xC000", "data": "3E 01 C9" }`  
**Result:** `{ "written": 3 }`

---

### `memory.read_vram`

**Params:** `{ "address": "0x0000", "length": 32 }`  
**Result:** `{ "address": "0x0000", "data": "..." }`

---

### `memory.disassemble`

**Params:** `{ "address": "0xC000", "count": 10 }`

**Result:**
```json
{
  "instructions": [
    { "address": "0xC000", "bytes": "3E 01", "mnemonic": "LD A, 0x01" },
    { "address": "0xC002", "bytes": "C9",    "mnemonic": "RET" }
  ]
}
```

---

### `vdp.get_registers`

**Params:** none  
**Result:** `{ "type": "V9938", "registers": { "R0": "0x00", ... } }`

---

### `vdp.get_status`

**Params:** none  
**Result:** `{ "status": "0x9F" }`

---

### `input.press_key`

Assert a key by matrix row/bit. Takes effect on the next PPI read (next frame).
Works in both paused and running state.

**Params:** `{ "row": 8, "bit": 0 }`  
**Result:** `{ "row": 8, "bit": 0, "pressed": true }`

#### MSX Keyboard Matrix

| Row | Bit 7 | Bit 6 | Bit 5 | Bit 4 | Bit 3 | Bit 2 | Bit 1 | Bit 0 |
|-----|-------|-------|-------|-------|-------|-------|-------|-------|
| 0   | 7     | 6     | 5     | 4     | 3     | 2     | 1     | 0     |
| 1   | ;     | ]     | [     | \     | =     | -     | 9     | 8     |
| 2   | B     | A     | \`    | /     | .     | ,     | '     | "     |
| 3   | J     | I     | H     | G     | F     | E     | D     | C     |
| 4   | R     | Q     | P     | O     | N     | M     | L     | K     |
| 5   | Z     | Y     | X     | W     | V     | U     | T     | S     |
| 6   | F3    | F2    | F1    | CODE  | CAPS  | GRAPH | CTRL  | SHIFT |
| 7   | RET   | SEL   | BS    | STOP  | TAB   | ESC   | F5    | F4    |
| 8   | →     | ↓     | ↑     | ←     | DEL   | INS   | HOME  | SPACE |
| 9   | NUM4  | NUM3  | NUM2  | NUM1  | NUM0  | —     | —     | —     |

Common keys: SPACE(8,0) RETURN(7,7) ESC(7,2) UP(8,5) DOWN(8,6) LEFT(8,4) RIGHT(8,7) SHIFT(6,0) CTRL(6,1)

---

### `input.release_key`

**Params:** `{ "row": 8, "bit": 0 }`  
**Result:** `{ "row": 8, "bit": 0, "pressed": false }`

---

### `input.press_key_named`

Press a key by MSX name; auto-release after `duration_ms`.

**Params:** `{ "key": "SPACE", "duration_ms": 100 }`  
Valid keys: `SPACE` `RETURN` `ESC` `UP` `DOWN` `LEFT` `RIGHT` `SHIFT` `CTRL` `GRAPH`
`CAPS` `CODE` `STOP` `HOME` `INS` `DEL` `BS` `TAB` `F1`–`F5` `A`–`Z` `0`–`9`  
**Result:** `{ "key": "SPACE", "row": 8, "bit": 0, "duration_ms": 100 }`

---

### `input.joystick`

**Params:**
```json
{ "port": 1, "up": false, "down": false, "left": false, "right": false,
  "trigger_a": true, "trigger_b": false }
```
**Result:** echo of port + state

---

### `input.joystick_release`

**Params:** `{ "port": 1 }`  
**Result:** `{ "port": 1, "released": true }`

---

### `screen.capture`

**Params:** `{ "scale": 1 }`  
**Result:**
```json
{ "width": 256, "height": 192, "format": "png", "encoding": "base64",
  "data": "<base64-encoded PNG>" }
```

---

### `state.save`

**Params:** `{ "path": "saves/states/debug_checkpoint.state" }` (path optional)  
**Result:** `{ "path": "saves/states/debug_checkpoint.state" }`

---

### `state.load`

**Params:** `{ "path": "saves/states/debug_checkpoint.state" }`  
**Result:** `{ "path": "...", "loaded": true }`

---

### `fdd.swap`

**Params:** `{ "drive": 1, "path": "disks/game_disk2.dsk" }` (use `null` to unmount)  
**Result:** `{ "drive": 1, "path": "...", "mounted": true }`

---

## Server-Push Notifications

The server sends unsolicited frames (no `id` field) when the emulator pauses:

```json
{
  "notification": "paused",
  "reason": "breakpoint",
  "pc": "0xC100",
  "registers": { ... }
}
```

Clients that do not need push may ignore these frames.

---

## Socket RPC Implementation

### New files

| File | Purpose |
|---|---|
| `msx/rpc_server.py` | `DebugServer` class — socket listener + queue bridge |
| `tools/rpc_client.py` | Thin CLI client for manual testing |

### Changes to existing files

| File | Change |
|---|---|
| `msx/machine.py` | Instantiate `DebugServer`; call `_drain_rpc_queue()` in frame loop |
| `msx/input.py` | Add `set_key_state(row, bit, pressed)` public method |
| `msx/debugger/repl.py` | Extract `handle_command(cmd: str) -> dict` from REPL loop |
| `__main__.py` | Add `--rpc-socket PATH` / `--no-rpc` CLI flags |

### Frame loop integration

```python
# msx/machine.py — per-frame loop
while True:
    self._drain_rpc_queue()   # process all pending RPC calls
    self.cpu.step()
    self.vdp.render()
    self._pump_sdl_events()
    self.frame_timer.wait()
```

### `msx/rpc_server.py` skeleton

```python
import json, queue, socket, threading
from pathlib import Path

class DebugServer:
    def __init__(self, machine, sock_path: str = "/tmp/py_msx_emu.sock") -> None:
        self._machine = machine
        self._sock_path = Path(sock_path)
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._client_lock = threading.Lock()
        self._client_conn: socket.socket | None = None

    def start(self) -> None:
        self._sock_path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self._sock_path))
        srv.listen(1)
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()

    def _accept_loop(self, srv: socket.socket) -> None:
        while True:
            conn, _ = srv.accept()
            with self._client_lock:
                self._client_conn = conn
            # Send banner
            self._send(conn, {
                "notification": "connected",
                "version": "1.0",
                "emulator": "py-msx-emulator",
            })
            self._read_loop(conn)
            with self._client_lock:
                self._client_conn = None

    def _read_loop(self, conn: socket.socket) -> None:
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    self._send(conn, {"error": {"code": -32700, "message": "parse error"}})
                    continue
                result_box: list = []
                done = threading.Event()
                self._queue.put((req, result_box, done))
                done.wait(timeout=30)
                self._send(conn, {"id": req.get("id"), "result": result_box[0]}
                           if result_box else
                           {"id": req.get("id"), "error": {"code": 3, "message": "timeout"}})

    def _send(self, conn: socket.socket, obj: dict) -> None:
        conn.sendall((json.dumps(obj) + "\n").encode())

    # Called from the main emulator thread between frames
    def drain(self) -> None:
        while not self._queue.empty():
            req, result_box, done = self._queue.get_nowait()
            result = self._dispatch(req)
            result_box.append(result)
            done.set()

    def _dispatch(self, req: dict) -> dict:
        method = req.get("method", "")
        params = req.get("params", {}) or {}
        handler = self._HANDLERS.get(method)
        if not handler:
            raise ValueError(f"unknown method: {method}")
        return handler(self._machine, params)

    # Handler registration populated below in full implementation
    _HANDLERS: dict = {}
```

---

---

# Part 2: Level 3 — MCP Server

## Overview

`tools/mcp_server.py` is a **standalone process** that Claude Code launches as a child
process via `stdio` transport. It translates MCP tool calls into Socket RPC calls and
returns results — including screenshots as `ImageContent` blocks that Claude can visually
inspect.

```
Claude Code  ←──── stdio / MCP ────→  tools/mcp_server.py  ←── Unix socket ──→  emulator
```

## Registration (one-time setup)

```bash
# Register for this project only (stored in .mcp.json → commit to git)
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py

# Or user-wide (stored in ~/.claude.json)
claude mcp add --transport stdio --scope user msx-emulator \
    -- python tools/mcp_server.py

# Verify
claude mcp list
# > msx-emulator  stdio  ● connected   18 tools
```

Inside a Claude Code session, run `/mcp` to confirm the server is connected and see all
available tools.

## Dependencies

```
mcp[cli]>=1.0          # pip install mcp  or  uv add --dev mcp
```

No additional dependencies beyond what `py-msx-emulator` already requires.

---

## `tools/mcp_server.py` — Full Implementation

```python
#!/usr/bin/env python3
"""
MCP server for py-msx-emulator.

Wraps the emulator's Unix-socket JSON-RPC API as MCP tools so that
Claude Code can control the running emulator directly.

Usage:
    # Start the emulator first:
    python . path/to/game.rom

    # Register the MCP server with Claude Code (once):
    claude mcp add --transport stdio --scope project msx-emulator -- python tools/mcp_server.py

    # Claude Code will start this process automatically on each session.
"""

from __future__ import annotations

import base64
import json
import os
import socket
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOCK_PATH = os.environ.get("MSX_RPC_SOCKET", "/tmp/py_msx_emu.sock")

mcp = FastMCP(
    "msx-emulator",
    description=(
        "Control a running py-msx-emulator instance: pause/step/continue the Z80 CPU, "
        "set breakpoints and watchpoints, read/write memory and VRAM, inject keyboard "
        "and joystick input, and capture screenshots as images."
    ),
)

# ---------------------------------------------------------------------------
# Transport helpers
# ---------------------------------------------------------------------------

_req_counter = 0


def _rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send one RPC call to the emulator and return the result dict.

    Raises RuntimeError if the emulator returns an error or is unreachable.
    """
    global _req_counter
    _req_counter += 1
    req_id = str(_req_counter)

    payload = {"id": req_id, "method": method}
    if params:
        payload["params"] = params

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(SOCK_PATH)
        # Drain the banner line sent by the server on connect
        _readline(sock)
        sock.sendall((json.dumps(payload) + "\n").encode())
        resp_line = _readline(sock)
        sock.close()
    except (OSError, ConnectionRefusedError) as exc:
        raise RuntimeError(
            f"Cannot connect to emulator socket {SOCK_PATH}. "
            "Is py-msx-emulator running?"
        ) from exc

    resp = json.loads(resp_line)
    if "error" in resp:
        raise RuntimeError(f"Emulator error {resp['error']['code']}: {resp['error']['message']}")
    return resp.get("result", {})


def _readline(sock: socket.socket) -> bytes:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.strip()


# ---------------------------------------------------------------------------
# MCP Tool definitions
# ---------------------------------------------------------------------------

# ── Debugger state ──────────────────────────────────────────────────────────

@mcp.tool()
def emulator_status() -> str:
    """Return whether the emulator is paused or running, and the current PC."""
    r = _rpc("debugger.status")
    state = "PAUSED" if r.get("paused") else "RUNNING"
    return f"{state}  PC={r.get('pc', '?')}  reason={r.get('reason', '-')}"


@mcp.tool()
def emulator_pause() -> str:
    """Pause emulation (equivalent to pressing Ctrl+C in the terminal)."""
    r = _rpc("debugger.pause")
    return f"Paused at PC={r['pc']} ({r['reason']})"


# ── CPU / execution ─────────────────────────────────────────────────────────

@mcp.tool()
def cpu_get_registers() -> str:
    """Return the current Z80 register file (AF, BC, DE, HL, IX, IY, SP, PC, …)."""
    r = _rpc("cpu.get_registers")
    regs = r.get("registers", r)
    lines = [f"{k}={v}" for k, v in regs.items()]
    return "\n".join(lines)


@mcp.tool()
def cpu_step() -> str:
    """
    Execute exactly one Z80 instruction and return the result.
    The emulator must be paused first (call emulator_pause if needed).
    """
    r = _rpc("cpu.step")
    regs = r.get("registers", {})
    return (
        f"PC={r['pc']}  {r['mnemonic']}  ({r['t_states']} T-states)\n"
        + "\n".join(f"{k}={v}" for k, v in regs.items())
    )


@mcp.tool()
def cpu_continue() -> str:
    """
    Resume execution until the next breakpoint or watchpoint.
    Returns immediately; use cpu_continue_until_pause to block.
    The emulator must be paused.
    """
    _rpc("cpu.continue")
    return "Execution resumed (non-blocking)."


@mcp.tool()
def cpu_continue_until_pause(timeout_seconds: int = 30) -> str:
    """
    Resume execution and wait until the emulator hits a breakpoint, watchpoint,
    or the timeout expires.

    Args:
        timeout_seconds: Maximum wait time in seconds (default 30).

    Returns a description of the stop reason and the PC where it stopped.
    """
    r = _rpc("cpu.continue_sync", {"timeout_ms": timeout_seconds * 1000})
    if r.get("paused"):
        return f"Stopped: reason={r['reason']}  PC={r['pc']}"
    return f"Timed out after {timeout_seconds}s (emulator still running)."


# ── Breakpoints / watchpoints ────────────────────────────────────────────────

@mcp.tool()
def debug_set_breakpoint(address: str) -> str:
    """
    Set a breakpoint at a Z80 address.

    Args:
        address: Hex address string, e.g. '0xC000' or 'C000'.
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("debug.set_breakpoint", {"address": address})
    return f"Breakpoint #{r['id']} set at {r['address']}"


@mcp.tool()
def debug_remove_breakpoint(breakpoint_id: int) -> str:
    """
    Remove a breakpoint by its numeric ID.

    Args:
        breakpoint_id: The ID returned by debug_set_breakpoint.
    """
    _rpc("debug.remove_breakpoint", {"id": breakpoint_id})
    return f"Breakpoint #{breakpoint_id} removed."


@mcp.tool()
def debug_list_breakpoints() -> str:
    """List all current breakpoints and watchpoints."""
    r = _rpc("debug.list_breakpoints")
    bps = r.get("breakpoints", [])
    if not bps:
        return "No breakpoints set."
    return "\n".join(
        f"  #{bp['id']}  {bp['address']}  {'active' if bp['active'] else 'disabled'}"
        for bp in bps
    )


@mcp.tool()
def debug_set_watchpoint(address: str, mode: str = "rw") -> str:
    """
    Set a memory watchpoint.

    Args:
        address: Hex address string, e.g. '0xE000'.
        mode: 'r' (read), 'w' (write), or 'rw' (both). Default 'rw'.
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("debug.set_watchpoint", {"address": address, "mode": mode})
    return f"Watchpoint #{r['id']} set at {r['address']} ({r['mode']})"


# ── Memory ──────────────────────────────────────────────────────────────────

@mcp.tool()
def memory_read(address: str, length: int = 16) -> str:
    """
    Read bytes from the Z80 address space.

    Args:
        address: Start address as hex string, e.g. '0xC000'.
        length: Number of bytes to read (1–4096, default 16).
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("memory.read", {"address": address, "length": length})
    return f"{r['address']}: {r['data']}"


@mcp.tool()
def memory_write(address: str, data_hex: str) -> str:
    """
    Write bytes into the Z80 address space. Emulator must be paused.

    Args:
        address: Target address as hex string, e.g. '0xC000'.
        data_hex: Space-separated hex bytes, e.g. '3E 01 C9'.
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("memory.write", {"address": address, "data": data_hex})
    return f"Wrote {r['written']} bytes to {address}."


@mcp.tool()
def memory_disassemble(address: str, count: int = 10) -> str:
    """
    Disassemble Z80 instructions starting at an address.

    Args:
        address: Start address as hex string, e.g. '0xC000'.
        count: Number of instructions to disassemble (default 10).
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("memory.disassemble", {"address": address, "count": count})
    lines = [
        f"  {instr['address']}  {instr['bytes']:<12}  {instr['mnemonic']}"
        for instr in r["instructions"]
    ]
    return "\n".join(lines)


@mcp.tool()
def memory_read_vram(address: str, length: int = 32) -> str:
    """
    Read bytes from VRAM (TMS9918A or V9938).

    Args:
        address: VRAM address as hex string, e.g. '0x0000'.
        length: Number of bytes to read (default 32).
    """
    if not address.startswith("0x"):
        address = "0x" + address
    r = _rpc("memory.read_vram", {"address": address, "length": length})
    return f"VRAM {r['address']}: {r['data']}"


# ── VDP ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def vdp_get_registers() -> str:
    """Return all VDP control registers (TMS9918A or V9938)."""
    r = _rpc("vdp.get_registers")
    lines = [f"VDP type: {r['type']}"] + [
        f"  {k} = {v}" for k, v in r["registers"].items()
    ]
    return "\n".join(lines)


@mcp.tool()
def vdp_get_status() -> str:
    """Return the VDP status register byte."""
    r = _rpc("vdp.get_status")
    return f"VDP status: {r['status']}"


# ── Input injection ──────────────────────────────────────────────────────────

@mcp.tool()
def input_press_key(key: str, duration_ms: int = 100) -> str:
    """
    Press an MSX keyboard key by name and release it after duration_ms milliseconds.
    Works while the emulator is running or paused.

    Args:
        key: Key name (case-insensitive). Valid names:
             SPACE, RETURN, ESC, UP, DOWN, LEFT, RIGHT,
             SHIFT, CTRL, GRAPH, CAPS, CODE, STOP,
             HOME, INS, DEL, BS, TAB,
             F1, F2, F3, F4, F5,
             A–Z, 0–9.
        duration_ms: How long to hold the key (milliseconds, default 100).
    """
    r = _rpc("input.press_key_named", {"key": key.upper(), "duration_ms": duration_ms})
    return f"Pressed {r['key']} (row={r['row']}, bit={r['bit']}) for {r['duration_ms']} ms."


@mcp.tool()
def input_joystick(
    port: int = 1,
    up: bool = False,
    down: bool = False,
    left: bool = False,
    right: bool = False,
    trigger_a: bool = False,
    trigger_b: bool = False,
) -> str:
    """
    Set the joystick state for Joy 1 or Joy 2.
    The state is held until the next call to input_joystick or input_joystick_release.

    Args:
        port: Joystick port (1 or 2, default 1).
        up / down / left / right: Directional inputs.
        trigger_a / trigger_b: Fire buttons.
    """
    r = _rpc("input.joystick", {
        "port": port,
        "up": up, "down": down, "left": left, "right": right,
        "trigger_a": trigger_a, "trigger_b": trigger_b,
    })
    s = r["state"]
    dirs = "+".join(d for d in ("up","down","left","right") if s[d])
    fires = "+".join(f for f in ("trigger_a","trigger_b") if s[f])
    return f"Joy{port}: dirs=[{dirs or 'none'}] fires=[{fires or 'none'}]"


@mcp.tool()
def input_joystick_release(port: int = 1) -> str:
    """
    Release all directions and buttons on a joystick port.

    Args:
        port: Joystick port (1 or 2, default 1).
    """
    _rpc("input.joystick_release", {"port": port})
    return f"Joy{port} released."


# ── Screenshot ───────────────────────────────────────────────────────────────

@mcp.tool()
def screen_capture(scale: int = 1) -> list[TextContent | ImageContent]:
    """
    Capture the current VDP frame as an image.
    Returns the screenshot so Claude can visually inspect the game screen.
    Works while running or paused.

    Args:
        scale: Pixel scale factor (default 1 → 256×192 for MSX1).
               Use 3 to match the default SDL window resolution.
    """
    r = _rpc("screen.capture", {"scale": scale})
    png_bytes = base64.b64decode(r["data"])
    return [
        TextContent(type="text", text=f"Screenshot: {r['width']}×{r['height']} px"),
        ImageContent(type="image", data=base64.b64encode(png_bytes).decode(), mimeType="image/png"),
    ]


# ── State save / load ────────────────────────────────────────────────────────

@mcp.tool()
def state_save(path: str = "") -> str:
    """
    Save a full machine snapshot (CPU, RAM, VDP, PSG, mapper banks).
    Equivalent to pressing F8 in the emulator.

    Args:
        path: Optional explicit file path. If empty, uses the standard
              timestamped filename under saves/states/.
    """
    params: dict[str, Any] = {}
    if path:
        params["path"] = path
    r = _rpc("state.save", params or None)
    return f"State saved to: {r['path']}"


@mcp.tool()
def state_load(path: str) -> str:
    """
    Load a previously saved machine snapshot.

    Args:
        path: Path to the .state file.
    """
    r = _rpc("state.load", {"path": path})
    return f"State loaded from: {r['path']}"


# ── Floppy disk ──────────────────────────────────────────────────────────────

@mcp.tool()
def fdd_swap(drive: int, path: str = "") -> str:
    """
    Mount or unmount a floppy disk image in a drive.

    Args:
        drive: Drive number (1 = A:, 2 = B:).
        path: Path to a .dsk image file. Leave empty to unmount.
    """
    r = _rpc("fdd.swap", {"drive": drive, "path": path or None})
    if r.get("mounted"):
        return f"Drive {drive}: mounted {r['path']}"
    return f"Drive {drive}: unmounted."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## MCP Tool Summary

| MCP Tool | Socket RPC method | Pause required |
|---|---|---|
| `emulator_status` | `debugger.status` | No |
| `emulator_pause` | `debugger.pause` | No |
| `cpu_get_registers` | `cpu.get_registers` | No |
| `cpu_step` | `cpu.step` | **Yes** |
| `cpu_continue` | `cpu.continue` | **Yes** |
| `cpu_continue_until_pause` | `cpu.continue_sync` | **Yes** |
| `debug_set_breakpoint` | `debug.set_breakpoint` | No |
| `debug_remove_breakpoint` | `debug.remove_breakpoint` | No |
| `debug_list_breakpoints` | `debug.list_breakpoints` | No |
| `debug_set_watchpoint` | `debug.set_watchpoint` | No |
| `memory_read` | `memory.read` | No |
| `memory_write` | `memory.write` | **Yes** |
| `memory_disassemble` | `memory.disassemble` | No |
| `memory_read_vram` | `memory.read_vram` | No |
| `vdp_get_registers` | `vdp.get_registers` | No |
| `vdp_get_status` | `vdp.get_status` | No |
| `input_press_key` | `input.press_key_named` | No |
| `input_joystick` | `input.joystick` | No |
| `input_joystick_release` | `input.joystick_release` | No |
| `screen_capture` | `screen.capture` | No |
| `state_save` | `state.save` | No |
| `state_load` | `state.load` | No |
| `fdd_swap` | `fdd.swap` | No |

---

## Typical Claude Code Debug Session

Claude Code can now reason about a misbehaving game with a natural workflow:

```
User: Game freezes after the title screen. Debug it.

Claude Code:
1. emulator_pause()
   → PAUSED at PC=0xC2A4

2. screen_capture()
   → [image: title screen with cursor blinking]

3. debug_set_breakpoint("0xC300")
   → Breakpoint #0 set at 0xC300

4. input_press_key("SPACE")
   → Pressed SPACE for 100 ms

5. cpu_continue_until_pause(timeout_seconds=5)
   → Stopped: reason=breakpoint  PC=0xC300

6. cpu_get_registers()
   → AF=0xFF00  BC=0x0000  HL=0xD800  ...

7. memory_read("0xD800", 16)
   → 0xD800: 00 00 00 00 00 00 00 00 ...

8. memory_disassemble("0xC300", 15)
   → 0xC300  CD A4 C2  CALL 0xC2A4
     0xC303  18 FE     JR -2            ← infinite loop here

9. state_save()
   → State saved to: saves/states/salamander_20260715_143200.state
```

Claude can see the actual game screen via `screen_capture()`, making it much easier
to correlate what the game is displaying with what the CPU is doing.

---

## Environment Variable

| Variable | Default | Description |
|---|---|---|
| `MSX_RPC_SOCKET` | `/tmp/py_msx_emu.sock` | Override the socket path if running multiple emulator instances |

Set it in `.mcp.json` to point at a project-specific socket:

```json
{
  "mcpServers": {
    "msx-emulator": {
      "command": "python",
      "args": ["tools/mcp_server.py"],
      "env": {
        "MSX_RPC_SOCKET": "/tmp/py_msx_salamander.sock"
      }
    }
  }
}
```

---

## Security Notes

- The Unix socket is accessible only to local processes running as the same user.
- `memory_write` can modify arbitrary Z80 memory; it is intentionally paused-only.
- The MCP server performs no authentication; if multiple users share the machine,
  restrict the socket path permissions with `chmod 600`.
