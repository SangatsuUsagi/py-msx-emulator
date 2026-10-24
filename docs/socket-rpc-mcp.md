# Socket RPC & MCP Server

The emulator can expose a local control interface for an external tool, such as
a shell script, a test harness, or an AI coding agent like Claude Code. The
tool can pause the emulator, examine the Z80/VDP state, send input, and capture
screenshots, all without the SDL window. Two parts work together:

- **Socket RPC** (`host/rpc_server.py`): a JSON-RPC server on a Unix domain
  socket, built into the emulator. Enable it with `--rpc`.
- **MCP server** (`tools/mcp_server.py`): a standalone process that wraps the
  socket RPC as MCP tools, so Claude Code can call them directly.

Both are off by default, and they add no runtime dependency to a normal run.
They use the same breakpoint/watchpoint/step model as the terminal debugger
(see [`docs/debugger.md`](debugger.md)). The RPC surface lets a program control
this model in place of a person.

This document is the reference for both layers, and it stays in step with the
code. [`docs/msx_emulator_rpc_spec.md`](msx_emulator_rpc_spec.md) is the
original design record for the same feature. The author wrote it before the
implementation, and it has full source listings. Read it for the reasons
behind a decision. For the current behavior, use this document.

---

## Architecture

```
MCP client  <-- MCP (stdio) -->  tools/mcp_server.py  <-- Unix socket -->  host/rpc_server.py (in-process)
```

`tools/mcp_server.py` holds no emulator state of its own. For each tool call,
it opens a short-lived connection to the socket, sends one RPC request, and
closes the connection. The MCP server thus continues to work if the emulator
restarts between calls. If nothing listens on the socket yet, the request
fails.

In the emulator process, `DebugServer` (`host/rpc_server.py`) runs its socket
accept/read loop on a background daemon thread. This thread parses requests
but never executes them. It puts each request on a queue. The main thread of
the emulator processes the queue when the host loop calls
`DebugServer.drain()`. The host loop calls it once per frame, and continuously
while the debugger is paused. The CPU/VDP hot path thus needs no locks: no RPC
handler ever runs at the same time as a CPU step or a VDP render.

There is one exception. `cpu.continue_sync` blocks the calling socket thread
until the next pause event. The server thus handles it directly on that thread
and does not queue it (see `DebugServer._continue_sync`).

The core emulator (`machine`, `cpu`, `vdp`, `memory`) has no knowledge of RPC.
For this feature, `host/rpc_server.py` is the only place that uses `socket`,
`json`, and `threading`. The core gives only a generic pause seam
(`Machine.set_pause_hook`), where the RPC server installs itself. The
portability note at the top of `host/rpc_server.py` describes how this layering
maps to a non-Python port.

---

## Starting the emulator with RPC enabled

```bash
# Default socket path (/tmp/py_msx_emu.sock)
python . path/to/game.rom --machine cbios_msx2_jp --rpc

# Custom socket path
python . path/to/game.rom --machine cbios_msx2_jp --rpc --rpc-socket /tmp/alt.sock
```

`--rpc` applies only to the interactive SDL run mode, not to `--count-frame` or
`--benchmark`. At startup, the emulator prints the bound path:

```
rpc     : /tmp/py_msx_emu.sock
```

The server accepts one active client at a time. If a stale file already exists
at the socket path (e.g. from a crashed process), the server removes and
replaces it at the next start.

RPC uses a Unix domain socket, so it needs macOS or Linux. These are the same
platforms that the emulator targets. Windows is untested (see
[`QUICK_START.md`](../QUICK_START.md)).

---

## Wire protocol

The transport is `AF_UNIX` / `SOCK_STREAM`, with newline-delimited JSON
framing: one UTF-8 JSON object per line.

When a client connects, the server sends a banner notification:

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

You can omit `params` for methods with no arguments. The response always
contains the `id` from the request.

### Error codes

| Code | Meaning |
|------|---------|
| `-32700` | Parse error (malformed JSON) |
| `-32601` | Method not found |
| `-32602` | Invalid params |
| `1` | Operation requires the emulator to be paused |
| `3` | Internal emulator error, including the dispatch timeout below |

(`2` is unused. The protocol defines no meaning for it.)

### Server-push notifications

When the emulator changes to the paused state (breakpoint, watchpoint, step
completion, or a `debugger.pause` call), the server pushes an unsolicited frame
to the connected client. This frame has no `id`:

```json
{
  "notification": "paused",
  "reason": "breakpoint",
  "pc": "0xC080",
  "registers": { "AF": "0x0100", "BC": "0x0010", "...": "..." }
}
```

A client that does not need push updates can ignore frames with a
`notification` key. `tools/mcp_server.py` and `tools/rpc_client.py` both skip
these frames while they wait for the `id` of their own response.

---

## Method reference

These `reason` values occur in all methods: `"user_request"`, `"breakpoint"`,
`"watchpoint"`, `"step_complete"`.

### Debugger control

- **`debugger.status`** → `{ paused, pc, reason }`. Reports the state and does
  not change it.
- **`debugger.pause`** → `{ paused: true, pc, reason: "user_request" }`. The
  same as Ctrl+C.

### CPU / execution

- **`cpu.step`** *(requires paused)* → `{ pc, t_states, mnemonic, registers }`.
  Executes exactly one Z80 instruction.
- **`cpu.continue`** *(requires paused)* → `{ running: true }`. Resumes and
  returns immediately (non-blocking).
- **`cpu.continue_sync`** *(requires paused)*: params `{ timeout_ms? }`
  (default 5000). Resumes and blocks until the next pause event or the
  timeout. Returns `{ paused: true, reason, pc, registers }` or
  `{ paused: false, reason: "timeout" }`. The server handles it on the socket
  thread, so it can block and the emulator loop does not stop.
- **`cpu.get_registers`** → `{ registers }`. Works in any state.

The `registers` object contains `AF`, `BC`, `DE`, `HL`, `IX`, `IY`, `SP`, `PC`
(as `0xXXXX` hex strings), the shadow registers `AF'`/`BC'`/`DE'`/`HL'`,
`I`/`R` (as `0xXX`), `IFF1`/`IFF2` (bool), and `IM` (int).

### Breakpoints / watchpoints

- **`debug.set_breakpoint`**: params `{ address }` →
  `{ id, address, active }`.
- **`debug.remove_breakpoint`**: params `{ id }` → `{ removed }`.
- **`debug.list_breakpoints`** → `{ breakpoints: [{ id, address, active }] }`.
- **`debug.set_watchpoint`**: params `{ address, mode? }` (`"r"` / `"w"` /
  `"rw"`, default `"rw"`) → `{ id, address, mode, active }`.
- **`debug.remove_watchpoint`**: params `{ id }` → `{ removed }`.

You can give an address as a JSON int or a hex string (`"0xC000"` or
`"C000"`). The machine keeps at most 4 breakpoints and 4 watchpoints, the same
as the `ba`/`wa` limits of the [interactive debugger](debugger.md). A 5th
`debug.set_*` call still returns a new `id`. But only the first 4 (in
allocation order) are armed.

### Memory

- **`memory.read`**: params `{ address, length? }` (default 16) →
  `{ address, data }`, with `data` as space-separated hex bytes.
- **`memory.write`** *(requires paused)*: params `{ address, data }` (hex
  bytes, e.g. `"3E 01 C9"`) → `{ written }`.
- **`memory.read_vram`**: params `{ address, length? }` (default 32) →
  `{ address, data }`. The address wraps within the VRAM size.
- **`memory.disassemble`**: params `{ address, count? }` (default 10) →
  `{ instructions: [{ address, bytes, mnemonic }] }`.

### VDP

- **`vdp.get_registers`** → `{ type: "V9938"|"TMS9918A", registers }`
  (`R0`.. as hex).
- **`vdp.get_status`** → `{ status }` (hex byte).

Both methods work in any state.

### Input injection

- **`input.press_key`** / **`input.release_key`**: params `{ row, bit }` (raw
  keyboard-matrix cell).
- **`input.press_key_named`**: params `{ key, duration_ms? }` (default 100).
  `key` is an MSX key name (`SPACE`, `RETURN`, `ESC`, `UP`/`DOWN`/`LEFT`/`RIGHT`,
  `SHIFT`, `CTRL`, `GRAPH`, `CAPS`, `CODE`, `STOP`, `HOME`, `INS`, `DEL`, `BS`,
  `TAB`, `SELECT`, `F1`-`F5`, `A`-`Z`, `0`-`9`), case-insensitive. The method
  presses the key and releases it automatically after `duration_ms`.
- **`input.joystick`**: params `{ port? (1|2, default 1), up?, down?, left?,
  right?, trigger_a?, trigger_b? }` (all bools) → `{ port, state }`. The state
  stays until the next call.
- **`input.joystick_release`**: params `{ port? }`. Clears all directions and
  triggers on that port.

All input methods work in the paused state and in the running state. The
effect is visible at the next PPI/joystick read.

### Screenshot

- **`screen.capture`**: params `{ scale? }` (default 1) → `{ width, height,
  format: "png", encoding: "base64", data }`. Renders the current VDP frame
  and does not advance the frame counter. Works in any state.

### State save / load

- **`state.save`**: params `{ path? }` → `{ path }`. Saves the CPU, RAM, VDP,
  PSG, and mapper bank state. It always writes a new timestamped file in
  `saves/states/`. An optional `path` gives only its filename stem, as the
  snapshot title. The method does not use it as the output location.
- **`state.load`**: params `{ path }` (required) → `{ path, loaded: true }`.

### Floppy disk

- **`fdd.swap`**: params `{ drive (1|2), path? }` → `{ drive, path, mounted }`.
  A `path` mounts that `.dsk` image. An absent or null `path` ejects the disk.
  Returns an error if the machine has no floppy interface.

---

## Manual testing: `tools/rpc_client.py`

A thin CLI client that sends requests to the socket directly, without MCP:

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

Give params as `key=value` pairs or as a single JSON object. The client
converts `key=value` values to int, bool, or string. It writes the push
notifications that it gets during the wait for the response to stderr. It
writes the response as formatted JSON to stdout. On an error response, the
process exits with a non-zero code.

---

## MCP server

`tools/mcp_server.py` is a stdio server based on `FastMCP`. It changes each
tool call into one RPC request and formats the result as text (and, for
screenshots, as an image). It reads the socket path from the `MSX_RPC_SOCKET`
environment variable (default `/tmp/py_msx_emu.sock`). A project can thus
point it at a non-default socket through the `env` block of the MCP
registration.

### Tool set

Each tool wraps the RPC method with the same name, with `_` in place of `.`.
[Method reference](#method-reference) describes these methods. The two raw
keyboard-matrix methods (`input.press_key` / `input.release_key`, addressed by
row and bit) have no tool. The tool `input_press_key` is the named-key
variant:

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
- `screen_capture(scale=1)`: returns an image as well as text
- `state_save(path="")`, `state_load(path)`
- `fdd_swap(drive, path="")`

`(paused)` marks the tools that need the emulator to be paused already.
Address arguments accept hex with or without a `0x` prefix. The server
normalizes them before it calls the RPC method. If the socket is unreachable,
every tool raises an error. The error gives the socket path and asks whether
the emulator runs with `--rpc`.

### Installing the optional dependency

The MCP server needs the `mcp[cli]` package. The project declares it as an
optional extra, so a normal emulator installation stays free of this
dependency:

```bash
pip install -e ".[mcp]"
```

Without this package, `tools/mcp_server.py` fails at startup with
`ModuleNotFoundError: No module named 'mcp'`. Install the extra above before
you register the server with Claude Code.

---

## Using the emulator from Claude Code

### 1. Register the MCP server (once per project)

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
```

This command writes an entry to `.mcp.json`. Claude Code starts
`tools/mcp_server.py` automatically at the start of each session that uses
it. You do not need to keep a process running manually. To verify the
registration, run:

```bash
claude mcp list
```

To point the server at a non-default socket, add an `env` block:

```bash
claude mcp add --transport stdio --scope project msx-emulator \
    --env MSX_RPC_SOCKET=/tmp/alt.sock \
    -- python tools/mcp_server.py
```

### 2. Start the emulator with `--rpc`

```bash
python . path/to/game.rom --machine cbios_msx2_jp --rpc
```

Keep this process running in its own terminal, or start it in the background
with the Bash tool of Claude Code. The MCP tools work only while an emulator
process listens on the socket.

### 3. Drive it from a Claude Code session

When the MCP server is registered, ask Claude to use the `msx-emulator` tools
directly. Examples:

- "Pause the emulator, dump the Z80 registers, and disassemble from the
  current PC."
- "Set a breakpoint at 0xC080, continue, and tell me what happened."
- "Take a screenshot at 3x scale so you can see what's on screen."
- "Press SPACE and check the screen after a second."

A typical debugging session has these steps:

1. `emulator_pause`: confirm the PC and the reason.
2. `memory_disassemble` / `cpu_get_registers`: examine the state.
3. `debug_set_breakpoint` + `cpu_continue_until_pause`: run to a location of
   interest.
4. `screen_capture`: visually confirm what the emulator rendered.
5. `cpu_continue`: resume normal execution.

---

## Security notes

- Only the local user can reach the socket (Unix domain socket, no network
  exposure). The socket has no authentication of its own. The filesystem
  permissions on the socket file control access.
- `memory.write` and `cpu.step` change the state and need precise timing. Thus
  they need the emulator to be paused first.
- RPC is strictly opt-in (`--rpc`). A normal run has no socket, no background
  thread, and no additional attack surface.

---

## Known limitations

- **Single active client.** The accept loop is single-threaded, so the server
  serves only one client at a time. If two MCP sessions (or an MCP session and
  `tools/rpc_client.py`) use the same socket at the same time, the second one
  blocks until the first one disconnects.
- **No emulator autostart.** The MCP server does not start the emulator. Start
  the emulator separately with `--rpc` first. If you do not, the first tool
  call fails with a clear "cannot connect" error.
- **`cpu.continue_sync` timeout is per-call.** If nothing pauses the emulator
  before `timeout_ms` elapses, the call returns `{ paused: false, reason:
  "timeout" }` and does not block indefinitely. The emulator continues to run.
- **Requests need a running host loop.** The server queues every method except
  `cpu.continue_sync` for `DebugServer.drain()`. The host calls this function
  once per frame, and continuously while paused. If the emulator is stuck and
  does not drain the queue within 30 seconds, the request fails with a
  `"dispatch timeout"` internal error (code `3`). The client thus does not
  hang.
