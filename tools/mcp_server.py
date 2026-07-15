#!/usr/bin/env python3
"""MCP server for py-msx-emulator.

Wraps the emulator's Unix-socket JSON-RPC API as MCP tools so Claude Code can
control a running emulator directly: pause/step/continue the Z80, set
breakpoints and watchpoints, read/write memory and VRAM, inject keyboard and
joystick input, and capture screenshots as images.

Usage:
    # Start the emulator with the RPC server enabled:
    python . path/to/game.rom --rpc

    # Register this MCP server with Claude Code (once):
    claude mcp add --transport stdio --scope project msx-emulator \
        -- python tools/mcp_server.py

Claude Code launches this process automatically per session. The socket path is
read from MSX_RPC_SOCKET (default /tmp/py_msx_emu.sock).
"""
from __future__ import annotations

import json
import os
import socket
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

SOCK_PATH = os.environ.get("MSX_RPC_SOCKET", "/tmp/py_msx_emu.sock")

mcp = FastMCP(
    "msx-emulator",
    instructions=(
        "Control a running py-msx-emulator instance: pause/step/continue the Z80 CPU, "
        "set breakpoints and watchpoints, read/write memory and VRAM, inject keyboard "
        "and joystick input, and capture screenshots as images."
    ),
)

# ---------------------------------------------------------------------------
# Transport helper
# ---------------------------------------------------------------------------

_req_counter = 0


def _rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send one RPC call to the emulator and return the result dict.

    Opens a short-lived connection per call (draining the connection banner),
    so the server survives the emulator being restarted between calls.

    Raises:
        RuntimeError: if the socket is unreachable or the emulator returns an error.
    """
    global _req_counter
    _req_counter += 1
    payload: dict[str, Any] = {"id": str(_req_counter), "method": method}
    if params:
        payload["params"] = params

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(60)
        sock.connect(SOCK_PATH)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot connect to emulator socket {SOCK_PATH}. "
            "Is py-msx-emulator running with --rpc?"
        ) from exc

    try:
        _readline(sock)  # drain the connection banner
        sock.sendall((json.dumps(payload) + "\n").encode())
        # Skip any server-push notifications (no "id") until our response arrives.
        while True:
            resp = json.loads(_readline(sock))
            if "notification" not in resp:
                break
    finally:
        sock.close()

    if "error" in resp:
        err = resp["error"]
        raise RuntimeError(f"Emulator error {err['code']}: {err['message']}")
    result: dict[str, Any] = resp.get("result", {})
    return result


def _readline(sock: socket.socket) -> bytes:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf.strip()


def _norm_addr(address: str) -> str:
    return address if address.lower().startswith("0x") else "0x" + address


# ---------------------------------------------------------------------------
# Debugger state
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CPU / execution
# ---------------------------------------------------------------------------

@mcp.tool()
def cpu_get_registers() -> str:
    """Return the current Z80 register file (AF, BC, DE, HL, IX, IY, SP, PC, ...)."""
    r = _rpc("cpu.get_registers")
    regs = r.get("registers", r)
    return "\n".join(f"{k}={v}" for k, v in regs.items())


@mcp.tool()
def cpu_step() -> str:
    """Execute exactly one Z80 instruction. The emulator must be paused first."""
    r = _rpc("cpu.step")
    regs = r.get("registers", {})
    return (
        f"PC={r['pc']}  {r['mnemonic']}  ({r['t_states']} T-states)\n"
        + "\n".join(f"{k}={v}" for k, v in regs.items())
    )


@mcp.tool()
def cpu_continue() -> str:
    """Resume execution (non-blocking). The emulator must be paused."""
    _rpc("cpu.continue")
    return "Execution resumed (non-blocking)."


@mcp.tool()
def cpu_continue_until_pause(timeout_seconds: int = 30) -> str:
    """Resume and wait until the next breakpoint/watchpoint or the timeout.

    Args:
        timeout_seconds: Maximum wait time in seconds (default 30).
    """
    r = _rpc("cpu.continue_sync", {"timeout_ms": timeout_seconds * 1000})
    if r.get("paused"):
        return f"Stopped: reason={r['reason']}  PC={r['pc']}"
    return f"Timed out after {timeout_seconds}s (emulator still running)."


# ---------------------------------------------------------------------------
# Breakpoints / watchpoints
# ---------------------------------------------------------------------------

@mcp.tool()
def debug_set_breakpoint(address: str) -> str:
    """Set a breakpoint at a Z80 address.

    Args:
        address: Hex address string, e.g. '0xC000' or 'C000'.
    """
    r = _rpc("debug.set_breakpoint", {"address": _norm_addr(address)})
    return f"Breakpoint #{r['id']} set at {r['address']}"


@mcp.tool()
def debug_remove_breakpoint(breakpoint_id: int) -> str:
    """Remove a breakpoint by its numeric ID.

    Args:
        breakpoint_id: The ID returned by debug_set_breakpoint.
    """
    r = _rpc("debug.remove_breakpoint", {"id": breakpoint_id})
    return f"Breakpoint #{breakpoint_id} removed." if r.get("removed") else "No such breakpoint."


@mcp.tool()
def debug_list_breakpoints() -> str:
    """List all current breakpoints."""
    bps = _rpc("debug.list_breakpoints").get("breakpoints", [])
    if not bps:
        return "No breakpoints set."
    return "\n".join(
        f"  #{bp['id']}  {bp['address']}  {'active' if bp['active'] else 'disabled'}"
        for bp in bps
    )


@mcp.tool()
def debug_set_watchpoint(address: str, mode: str = "rw") -> str:
    """Set a memory watchpoint.

    Args:
        address: Hex address string, e.g. '0xE000'.
        mode: 'r' (read), 'w' (write), or 'rw' (both). Default 'rw'.
    """
    r = _rpc("debug.set_watchpoint", {"address": _norm_addr(address), "mode": mode})
    return f"Watchpoint #{r['id']} set at {r['address']} ({r['mode']})"


@mcp.tool()
def debug_remove_watchpoint(watchpoint_id: int) -> str:
    """Remove a watchpoint by its numeric ID.

    Args:
        watchpoint_id: The ID returned by debug_set_watchpoint.
    """
    r = _rpc("debug.remove_watchpoint", {"id": watchpoint_id})
    return f"Watchpoint #{watchpoint_id} removed." if r.get("removed") else "No such watchpoint."


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@mcp.tool()
def memory_read(address: str, length: int = 16) -> str:
    """Read bytes from the Z80 address space.

    Args:
        address: Start address as hex string, e.g. '0xC000'.
        length: Number of bytes to read (default 16).
    """
    r = _rpc("memory.read", {"address": _norm_addr(address), "length": length})
    return f"{r['address']}: {r['data']}"


@mcp.tool()
def memory_write(address: str, data_hex: str) -> str:
    """Write bytes into the Z80 address space. Emulator must be paused.

    Args:
        address: Target address as hex string, e.g. '0xC000'.
        data_hex: Space-separated hex bytes, e.g. '3E 01 C9'.
    """
    r = _rpc("memory.write", {"address": _norm_addr(address), "data": data_hex})
    return f"Wrote {r['written']} bytes to {_norm_addr(address)}."


@mcp.tool()
def memory_disassemble(address: str, count: int = 10) -> str:
    """Disassemble Z80 instructions starting at an address.

    Args:
        address: Start address as hex string, e.g. '0xC000'.
        count: Number of instructions to disassemble (default 10).
    """
    r = _rpc("memory.disassemble", {"address": _norm_addr(address), "count": count})
    return "\n".join(
        f"  {i['address']}  {i['bytes']:<12}  {i['mnemonic']}" for i in r["instructions"]
    )


@mcp.tool()
def memory_read_vram(address: str, length: int = 32) -> str:
    """Read bytes from VRAM (TMS9918A or V9938).

    Args:
        address: VRAM address as hex string, e.g. '0x0000'.
        length: Number of bytes to read (default 32).
    """
    r = _rpc("memory.read_vram", {"address": _norm_addr(address), "length": length})
    return f"VRAM {r['address']}: {r['data']}"


# ---------------------------------------------------------------------------
# VDP
# ---------------------------------------------------------------------------

@mcp.tool()
def vdp_get_registers() -> str:
    """Return all VDP control registers (TMS9918A or V9938)."""
    r = _rpc("vdp.get_registers")
    lines = [f"VDP type: {r['type']}"]
    lines += [f"  {k} = {v}" for k, v in r["registers"].items()]
    return "\n".join(lines)


@mcp.tool()
def vdp_get_status() -> str:
    """Return the VDP status register byte."""
    return f"VDP status: {_rpc('vdp.get_status')['status']}"


# ---------------------------------------------------------------------------
# Input injection
# ---------------------------------------------------------------------------

@mcp.tool()
def input_press_key(key: str, duration_ms: int = 100) -> str:
    """Press an MSX key by name and release it after duration_ms. Works running or paused.

    Args:
        key: Key name (case-insensitive): SPACE, RETURN, ESC, UP, DOWN, LEFT, RIGHT,
             SHIFT, CTRL, GRAPH, CAPS, CODE, STOP, HOME, INS, DEL, BS, TAB,
             F1-F5, A-Z, 0-9.
        duration_ms: How long to hold the key in milliseconds (default 100).
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
    """Set the joystick state for port 1 or 2, held until changed or released.

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
    dirs = "+".join(d for d in ("up", "down", "left", "right") if s[d])
    fires = "+".join(f for f in ("trigger_a", "trigger_b") if s[f])
    return f"Joy{port}: dirs=[{dirs or 'none'}] fires=[{fires or 'none'}]"


@mcp.tool()
def input_joystick_release(port: int = 1) -> str:
    """Release all directions and buttons on a joystick port.

    Args:
        port: Joystick port (1 or 2, default 1).
    """
    _rpc("input.joystick_release", {"port": port})
    return f"Joy{port} released."


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

@mcp.tool()
def screen_capture(scale: int = 1) -> list[TextContent | ImageContent]:
    """Capture the current VDP frame as an image so Claude can inspect the screen.

    Args:
        scale: Pixel scale factor (default 1). Use 3 to match the default window size.
    """
    r = _rpc("screen.capture", {"scale": scale})
    return [
        TextContent(type="text", text=f"Screenshot: {r['width']}x{r['height']} px"),
        ImageContent(type="image", data=r["data"], mimeType="image/png"),
    ]


# ---------------------------------------------------------------------------
# State save / load
# ---------------------------------------------------------------------------

@mcp.tool()
def state_save(path: str = "") -> str:
    """Save a full machine snapshot (CPU, RAM, VDP, PSG, mapper banks).

    Args:
        path: Optional file path. If empty, a timestamped file under saves/states/ is used.
    """
    params: dict[str, Any] = {"path": path} if path else {}
    r = _rpc("state.save", params or None)
    return f"State saved to: {r['path']}"


@mcp.tool()
def state_load(path: str) -> str:
    """Load a previously saved machine snapshot.

    Args:
        path: Path to the .state file.
    """
    r = _rpc("state.load", {"path": path})
    return f"State loaded from: {r['path']}"


# ---------------------------------------------------------------------------
# Floppy disk
# ---------------------------------------------------------------------------

@mcp.tool()
def fdd_swap(drive: int, path: str = "") -> str:
    """Mount or unmount a floppy disk image in a drive.

    Args:
        drive: Drive number (1 = A:, 2 = B:).
        path: Path to a .dsk image file. Leave empty to unmount.
    """
    r = _rpc("fdd.swap", {"drive": drive, "path": path or None})
    if r.get("mounted"):
        return f"Drive {drive}: mounted {r['path']}"
    return f"Drive {drive}: unmounted."


if __name__ == "__main__":
    mcp.run(transport="stdio")
