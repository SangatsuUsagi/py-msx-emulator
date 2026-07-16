# Socket RPC & MCP Server

The emulator can expose a local control interface so an external tool — most
notably Claude Code — can pause it, inspect Z80/VDP state, inject input, and
capture screenshots, all without touching the SDL window. Two pieces work
together:

- **Socket RPC** (`msx/rpc_server.py`): an embedded JSON-RPC server on a Unix
  domain socket, built into the emulator itself. Enabled with `--rpc`.
- **MCP server** (`tools/mcp_server.py`): a standalone process that wraps the
  socket RPC as MCP tools, so Claude Code can call them directly.

Both are off by default and add no runtime dependency to a normal run. It's
the same breakpoint/watchpoint/step model as the terminal debugger (see
[`docs/debugger.md`](debugger.md)) — the RPC surface just lets a program
drive it instead of a person.

---

## Architecture

```
Claude Code  <-- MCP (stdio) -->  tools/mcp_server.py  <-- Unix socket -->  msx/rpc_server.py (in-process)
```

`tools/mcp_server.py` holds no emulator state of its own — every tool call
opens a short-lived connection to the socket, sends one RPC request, and
closes it. This means the MCP server survives the emulator restarting between
calls; it just fails the request if nothing is listening yet.

Inside the emulator process, `DebugServer` (`msx/rpc_server.py`) runs its
socket accept/read loop on a background daemon thread. Requests are parsed
there but never executed there: each one is pushed onto a queue and processed
on the emulator's main thread when the host loop calls `DebugServer.drain()`
(once per frame, and continuously while paused in the debugger). This keeps
the CPU/VDP hot path free of locks — no RPC handler ever runs concurrently
with a CPU step or VDP render.

One exception: `cpu.continue_sync` blocks the calling socket thread until the
next pause event, so it is handled inline on that thread rather than queued
(see `DebugServer._continue_sync`).

The core emulator (`machine`, `cpu`, `vdp`, `memory`) has no knowledge of RPC;
`msx/rpc_server.py` is the only place `socket`, `json`, and `threading` are
used for this feature. The core only exposes a generic pause seam
(`Machine.set_pause_hook`) that the RPC server installs on. See the
portability note at the top of `msx/rpc_server.py` for how this layering maps
onto a non-Python port.

---

## Starting the emulator with RPC enabled

```bash
# Default socket path (/tmp/py_msx_emu.sock)
python . path/to/game.rom --machine cbios_msx2_jp --rpc

# Custom socket path
python . path/to/game.rom --machine cbios_msx2_jp --rpc --rpc-socket /tmp/alt.sock
```

`--rpc` only applies to the interactive SDL run mode (not `--count-frame` /
`--benchmark`). On startup the emulator prints the bound path:

```
rpc     : /tmp/py_msx_emu.sock
```

The server accepts a single active client at a time. If the socket path
already exists as a stale file (e.g. left behind by a crashed process), it is
removed and replaced on the next start.

RPC uses a Unix domain socket, so it needs macOS or Linux — the same
platforms the emulator already targets. Windows is untested (see the main
README).

---

## Wire protocol

Transport is `AF_UNIX` / `SOCK_STREAM`, framed as newline-delimited JSON — one
UTF-8 JSON object per line.

On connect, the server sends a banner notification:

```json
{"notification": "connected", "version": "1.0", "emulator": "py-msx-emulator"}
```

### Requests and responses

```json
// request
{"id": "1", "method": "debugger.status", "params": {}}

// success
{"id": "1", "result": {"paused": false, "pc": "0xC010", "reason": "user_request"}}

// error
{"id": "1", "error": {"code": 1, "message": "emulator must be paused for this operation"}}
```

`params` may be omitted for zero-argument methods. The `id` from the request
is always echoed back.

### Error codes

| Code | Meaning |
|------|---------|
| `-32700` | Parse error (malformed JSON) |
| `-32601` | Method not found |
| `-32602` | Invalid params |
| `1` | Operation requires the emulator to be paused |
| `2` | Reserved: operation requires running state (no handler needs it yet) |
| `3` | Internal emulator error |

### Server-push notifications

Whenever the emulator transitions to paused (breakpoint, watchpoint, step
completion, or a `debugger.pause` call), the server pushes an unsolicited
frame to the connected client, with no `id`:

```json
{
  "notification": "paused",
  "reason": "breakpoint",
  "pc": "0xC080",
  "registers": { "AF": "0x0100", "BC": "0x0010", "...": "..." }
}
```

Clients that don't need push updates can ignore frames with a `notification`
key. `tools/mcp_server.py` and `tools/rpc_client.py` both skip them while
waiting for their own response's `id`.

---

## Method reference

`reason` values used throughout: `"user_request"`, `"breakpoint"`,
`"watchpoint"`, `"step_complete"`.

### Debugger control

- **`debugger.status`** → `{ paused, pc, reason }`. Reports state without
  changing it.
- **`debugger.pause`** → `{ paused: true, pc, reason: "user_request" }`.
  Equivalent to pressing Ctrl+C.

### CPU / execution

- **`cpu.step`** *(requires paused)* → `{ pc, t_states, mnemonic, registers }`.
  Executes exactly one Z80 instruction.
- **`cpu.continue`** *(requires paused)* → `{ running: true }`. Resumes and
  returns immediately (non-blocking).
- **`cpu.continue_sync`** *(requires paused)* — params: `{ timeout_ms? }`
  (default 5000). Resumes and blocks until the next pause event or the
  timeout; returns `{ paused: true, reason, pc, registers }` or
  `{ paused: false, reason: "timeout" }`. Handled on the socket thread so it
  can block without stalling the emulator loop.
- **`cpu.get_registers`** → `{ registers }`. Works in any state.

The `registers` object: `AF`, `BC`, `DE`, `HL`, `IX`, `IY`, `SP`, `PC` (as
`0xXXXX` hex strings), the shadow registers `AF'`/`BC'`/`DE'`/`HL'`, `I`/`R`
(as `0xXX`), `IFF1`/`IFF2` (bool), `IM` (int).

### Breakpoints / watchpoints

- **`debug.set_breakpoint`** — params: `{ address }` → `{ id, address, active }`.
- **`debug.remove_breakpoint`** — params: `{ id }` → `{ removed }`.
- **`debug.list_breakpoints`** → `{ breakpoints: [{ id, address, active }] }`.
- **`debug.set_watchpoint`** — params: `{ address, mode? }` (`"r"` / `"w"` /
  `"rw"`, default `"rw"`) → `{ id, address, mode, active }`.
- **`debug.remove_watchpoint`** — params: `{ id }` → `{ removed }`.

Addresses may be given as a JSON int or a hex string (`"0xC000"` or `"C000"`).
The underlying machine keeps at most 4 breakpoints and 4 watchpoints
(matching the [interactive debugger](debugger.md)'s `ba`/`wa` limits); a 5th `debug.set_*`
call still returns a new `id`, but only the first 4 (by allocation order)
are actually armed.

### Memory

- **`memory.read`** — params: `{ address, length? }` (default 16) →
  `{ address, data }`, `data` as space-separated hex bytes.
- **`memory.write`** *(requires paused)* — params: `{ address, data }` (hex
  bytes, e.g. `"3E 01 C9"`) → `{ written }`.
- **`memory.read_vram`** — params: `{ address, length? }` (default 32) →
  `{ address, data }`. Wraps within VRAM size.
- **`memory.disassemble`** — params: `{ address, count? }` (default 10) →
  `{ instructions: [{ address, bytes, mnemonic }] }`.

### VDP

- **`vdp.get_registers`** → `{ type: "V9938"|"TMS9918A", registers }`
  (`R0`.. as hex).
- **`vdp.get_status`** → `{ status }` (hex byte).

Both work in any state.

### Input injection

- **`input.press_key`** / **`input.release_key`** — params: `{ row, bit }`
  (raw keyboard-matrix cell).
- **`input.press_key_named`** — params: `{ key, duration_ms? }` (default 100).
  `key` is an MSX key name (`SPACE`, `RETURN`, `ESC`, `UP`/`DOWN`/`LEFT`/`RIGHT`,
  `SHIFT`, `CTRL`, `GRAPH`, `CAPS`, `CODE`, `STOP`, `HOME`, `INS`, `DEL`, `BS`,
  `TAB`, `SELECT`, `F1`-`F5`, `A`-`Z`, `0`-`9`), case-insensitive. Presses the
  key and schedules an automatic release after `duration_ms`.
- **`input.joystick`** — params: `{ port? (1|2, default 1), up?, down?, left?,
  right?, trigger_a?, trigger_b? }` (all bools) → `{ port, state }`. State is
  held until the next call.
- **`input.joystick_release`** — params: `{ port? }` → clears all directions
  and triggers on that port.

All input methods work in both paused and running state; the effect is
visible on the next PPI/joystick read.

### Screenshot

- **`screen.capture`** — params: `{ scale? }` (default 1) → `{ width, height,
  format: "png", encoding: "base64", data }`. Renders the current VDP frame
  without advancing the frame counter. Works in any state.

### State save / load

- **`state.save`** — params: `{ path? }` → `{ path }`. Captures CPU, RAM, VDP,
  PSG, and mapper bank state. Always writes a new timestamped file under
  `saves/states/`; an optional `path` only supplies its filename stem as the
  snapshot title (it is not used as the output location).
- **`state.load`** — params: `{ path }` (required) → `{ path, loaded: true }`.

### Floppy disk

- **`fdd.swap`** — params: `{ drive (1|2), path? }`. A `path` mounts that
  `.dsk` image; omitting or nulling it ejects → `{ drive, path, mounted }`.
  Errors if the machine has no floppy interface.

---

## Manual testing: `tools/rpc_client.py`

A thin CLI client for exercising the socket directly, without MCP:

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

Params are given either as `key=value` pairs (coerced to int/bool/string) or
as a single JSON object. Push notifications received while waiting for the
response are printed to stderr. The response is printed as pretty JSON to
stdout; the process exits non-zero on an error response.

---

## MCP server

`tools/mcp_server.py` is a `FastMCP`-based stdio server that translates each
tool call into one RPC request and formats the result as text (and, for
screenshots, an image). It reads the socket path from the `MSX_RPC_SOCKET`
environment variable (default `/tmp/py_msx_emu.sock`), so a project can point
it at a non-default socket via the MCP registration's `env` block.

### Tool set

Each tool maps 1:1 to the RPC method of the same shape described in
[Method reference](#method-reference) (tool names swap the `.` for `_`):

- `emulator_status`, `emulator_pause`
- `cpu_get_registers`, `cpu_step` (paused), `cpu_continue` (paused,
  non-blocking), `cpu_continue_until_pause(timeout_seconds=30)` (paused,
  blocks)
- `debug_set_breakpoint(address)`, `debug_remove_breakpoint(breakpoint_id)`,
  `debug_list_breakpoints`, `debug_set_watchpoint(address, mode="rw")`,
  `debug_remove_watchpoint(watchpoint_id)`
- `memory_read(address, length=16)`, `memory_write(address, data_hex)`
  (paused), `memory_disassemble(address, count=10)`,
  `memory_read_vram(address, length=32)`
- `vdp_get_registers`, `vdp_get_status`
- `input_press_key(key, duration_ms=100)`, `input_joystick(port=1, up=,
  down=, left=, right=, trigger_a=, trigger_b=)`,
  `input_joystick_release(port=1)`
- `screen_capture(scale=1)` — returns an actual image, not just text
- `state_save(path="")`, `state_load(path)`
- `fdd_swap(drive, path="")`

`(paused)` marks tools that require the emulator to already be paused.
Address arguments accept hex with or without a `0x` prefix; the server
normalizes them before calling the RPC method. If the socket is unreachable,
every tool raises an error naming the socket path and asking whether the
emulator is running with `--rpc`.

### Installing the optional dependency

The MCP server needs the `mcp[cli]` package, declared as an optional extra so
a normal emulator install stays dependency-free:

```bash
pip install -e ".[mcp]"
```

Without it, `tools/mcp_server.py` fails on startup with
`ModuleNotFoundError: No module named 'mcp'` — install the extra above
before registering the server with Claude Code.

---

## Using the emulator from Claude Code

### 1. Register the MCP server (once per project)

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
```

This writes an entry to `.mcp.json`. Claude Code launches
`tools/mcp_server.py` automatically at the start of each session that uses
it — there is nothing to keep running manually. Verify with:

```bash
claude mcp list
```

To point the server at a non-default socket, add an `env` block instead:

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    --env MSX_RPC_SOCKET=/tmp/alt.sock \
    -- python tools/mcp_server.py
```

### 2. Start the emulator with `--rpc`

```bash
python . path/to/game.rom --machine cbios_msx2_jp --rpc
```

Leave this running in its own terminal (or launch it via Claude Code's Bash
tool in the background) — the MCP tools are only useful while an emulator
process is listening on the socket.

### 3. Drive it from a Claude Code session

With the MCP server registered, ask Claude to use the `msx-emulator` tools
directly, for example:

- "Pause the emulator, dump the Z80 registers, and disassemble from the
  current PC."
- "Set a breakpoint at 0xC080, continue, and tell me what happened."
- "Take a screenshot at 3x scale so you can see what's on screen."
- "Press SPACE and check the screen after a second."

Under the hood each of these becomes one or more calls listed in
[MCP server](#mcp-server) above, each of which maps 1:1 to a socket-RPC
method. A typical debugging exchange looks like:

1. `emulator_pause` → confirms the PC and reason.
2. `memory_disassemble` / `cpu_get_registers` → inspect state.
3. `debug_set_breakpoint` + `cpu_continue_until_pause` → run to a location of
   interest.
4. `screen_capture` → visually confirm what's rendered.
5. `cpu_continue` → resume normal execution.

---

## Security notes

- The socket is local-user only (Unix domain socket, no network exposure) and
  has no authentication of its own; access control is via filesystem
  permissions on the socket file.
- `memory.write` and `cpu.step` (state-mutating, precise-timing operations)
  require the emulator to be paused first.
- Only one client can be connected at a time — see _Known limitations_ below
  for what that means in practice.
- RPC is strictly opt-in (`--rpc`); a normal run has no socket, no background
  thread, and no additional attack surface.

---

## Known limitations

- **Single active client.** The accept loop is single-threaded, so only one
  client is serviced at a time; running two MCP sessions (or an MCP session
  and `tools/rpc_client.py`) against the same socket concurrently means the
  second one blocks until the first disconnects.
- **No emulator autostart.** The MCP server does not launch the emulator for
  you — start it separately with `--rpc` first, or the first tool call will
  fail with a clear "cannot connect" error.
- **`cpu.continue_sync` timeout is per-call.** If nothing pauses the emulator
  before `timeout_ms` elapses, the call returns `{ paused: false, reason:
  "timeout" }` rather than blocking indefinitely; the emulator keeps running.
