# Tools

Standalone command-line utilities that work alongside the emulator: disk image
management, and the agent-facing RPC/MCP layer. None of these run inside the
emulator process; each is a separate `python tools/<script>.py` invocation.

| Script / section | Purpose |
| --- | --- |
| [`dskftp.py`](#dskftppy) | Interactive ftp-style shell for editing a `.dsk` image's files |
| [`dskblank.py`](#dskblankpy) | Create a blank, unformatted `.dsk` image |
| [`dskdd.py`](#dskddpy) | Copy raw sectors between a `.dsk` image and a physical USB floppy drive (Linux only) |
| [Remote control (Socket RPC & MCP)](#remote-control-socket-rpc--mcp) | Overview of the socket RPC + MCP control layer used by `rpc_client.py` and `mcp_server.py` |
| [`rpc_client.py`](#rpc_clientpy) | Thin CLI client for the emulator's socket RPC server, for manual testing |
| [`mcp_server.py`](#mcp_serverpy) | MCP server wrapping the socket RPC API, so Claude Code can drive a running emulator |

All scripts are plain Python 3.10+ with no extra dependencies beyond the
project's own (`requirements.txt` / `requirements-dev.txt`); run them with the
project venv, e.g. `venvs/py-msx-emulator/bin/python tools/dskftp.py`.

---

## `dskftp.py`

Interactive, ftp-style shell for MSX-DOS (FAT12) `.dsk` disk images: browse
directories, and upload/download/delete files, without booting the emulator.
Handles the standard MSX floppy formats (1DD 360KB/320KB, 2DD 720KB/640KB),
including images formatted by MSX BASIC's `CALL FORMAT` or by this tool's own
`format` command. Subdirectories (MSX-DOS2) are supported; an MSX-DOS1 image
simply behaves as one that only ever has a root directory.

All writes go straight to the image file — there is no separate save step.
Transfers are always binary; no CR/LF translation is performed.

```bash
python tools/dskftp.py game.dsk        # connect on startup
python tools/dskftp.py                 # connect later with `open`
```

Commands inside the shell (see `help` for the full list with usage strings):

| Command | Effect |
| --- | --- |
| `open <image.dsk>` | Connect to a disk image |
| `format <image.dsk> [720\|640\|360\|320]` | Create an empty image (KB, default 720) and connect to it |
| `dir` / `ls [path]` | List a remote directory (path may end in a wildcard) |
| `cd <dir>` | Change the remote directory |
| `lcd [dir]` | Change the local directory (no argument: print it) |
| `pwd` / `lpwd` | Print the remote / local working directory |
| `get <remote> [local]` | Download a file |
| `put <local> [remote]` (alias `send`) | Upload a file |
| `mget <pattern>` / `mput <pattern>` | Download / upload several files by glob pattern |
| `delete <file>` | Delete a remote file |
| `mkdir <dir>` / `rmdir <dir>` | Create / remove a remote directory |
| `quit` / `bye` (or Ctrl-D) | Leave the shell |

`format` writes a data disk: it includes the standard MSX-DOS boot sector
loader (the "Boot error / Press any key for retry" stub `CALL FORMAT` also
writes), but no `MSXDOS.SYS`. It cannot load DOS; it can only be read and
written as a plain FAT12 volume.

## `dskblank.py`

Create a blank `*.dsk` image for use with `--fdd1` / `--fdd2`: the file is
filled with `0xE5` (the standard floppy fill byte) so it looks like freshly
formatted-but-empty media, with no boot sector, BPB, or FAT written. Format it
afterwards — with MSX BASIC's `CALL FORMAT` inside the emulator, or with
`dskftp.py format`.

```bash
python tools/dskblank.py blank.dsk               # 720 KB (2DD), the default
python tools/dskblank.py blank.dsk --size 360    # 360 KB (1DD)
python tools/dskblank.py blank.dsk --force       # overwrite an existing file
```

## `dskdd.py`

Copy raw sectors between a `.dsk` image and a USB-connected 3-mode floppy
drive. **Linux only.** Implements both directions of "plain USB FDD + dd" from
[`extras/msx_2dd_disk_imaging_linux.md`](../extras/msx_2dd_disk_imaging_linux.md):

```bash
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk            # disk -> image
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk --retries 5
sudo python3 tools/dskdd.py write msxdisk.dsk /dev/sdb --verify  # image -> disk
```

On Linux a USB FDD shows up as `/dev/sdX` (a block device), not `/dev/fd0`.
Run `sudo ufiformat -i` to find the device node, and `sudo ufiformat -i
/dev/sdX` to confirm the inserted media, before running this.

Both directions refuse to run unless all of the following hold:

- the device is a block device
- it is not mounted
- it holds media
- it is small enough to plausibly be a floppy (≤4 MB, `--force` overrides)

`write` additionally makes the operator retype the
device path, because writing to the wrong device destroys whatever is on it;
`read` refuses to overwrite an existing image unless `--overwrite` is given.

| Subcommand | Options |
| --- | --- |
| `read <device> <image>` | `--sectors N` (default: whole device), `--retries N` (default: 2), `--overwrite`, `--force` |
| `write <image> <device>` | `--yes` (skip confirmation), `--verify` (read back and compare), `--force` |

Exit status: `0` success, `1` error, `2` the disk was imaged but some sectors
stayed unreadable (zero-filled in the image, listed on stdout).

## Remote control (Socket RPC & MCP)

The emulator can expose a small local control surface so external tools — shell
scripts, a test harness, or an AI coding agent — can pause, inspect, and drive a
running instance. There are two layers:

- **Socket RPC** — a Unix-domain-socket JSON-RPC server embedded in the emulator
  process (`msx/rpc_server.py`). It is **off by default**; enable it with `--rpc`.
- **MCP server** — [`mcp_server.py`](#mcp_serverpy) below, a standalone stdio
  server that wraps the socket RPC as
  [Model Context Protocol](https://modelcontextprotocol.io) tools, so a client
  like Claude Code can call emulator functions as native tools (and receive
  screenshots as inline images).

```
MCP client  ──stdio/MCP──▶  tools/mcp_server.py  ──Unix socket──▶  emulator (--rpc)
```

### Enabling the RPC server

```bash
# Start the emulator with the control socket enabled
python . path/to/game.rom --rpc

# Optional: use a custom socket path (e.g. for multiple instances)
python . path/to/game.rom --rpc --rpc-socket /tmp/py_msx_alt.sock
```

The RPC methods cover:

- debugger pause/step/continue
- breakpoints and watchpoints
- memory and VRAM read/write, disassembly
- VDP registers
- keyboard/joystick injection
- screenshot capture
- save-state
- disk swap

The wire protocol and full method reference are documented in
[`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md).

For manual testing without the MCP layer, see [`rpc_client.py`](#rpc_clientpy)
below; to let an MCP client drive the emulator, see
[`mcp_server.py`](#mcp_serverpy) below.

### Security notes

- The Unix socket is reachable only by local processes running as the same user.
- `memory.write` and `cpu.step` mutate machine state and are **paused-only**.
- There is no authentication; on a shared host, restrict the socket with
  `chmod 600` — otherwise any other local user can connect and drive
  `memory.write`/`cpu.step` to alter the running machine's state at will. The
  server is opt-in (`--rpc`) precisely because it is a control surface — no
  socket exists unless you ask for one.

## `rpc_client.py`

Thin CLI client for the emulator's Unix-socket JSON-RPC server, for manual
testing without the MCP layer. Sends one method call and prints the response.
Requires the emulator running with `--rpc`. See
[`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) for the full method
reference.

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

Params are given as a JSON object or as `key=value` pairs; each value is
coerced to `int`/`bool`/`str` as appropriate. Default socket path is
`/tmp/py_msx_emu.sock`.

## `mcp_server.py`

Wraps the emulator's socket RPC API as MCP tools, so an MCP client such as
Claude Code can control a running emulator directly. The main operations are:

- pause/step/continue the Z80
- set breakpoints and watchpoints
- read/write memory and VRAM
- inject keyboard and joystick input
- capture screenshots

See [`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) for the
architecture and the full tool list.

The MCP server needs the optional `mcp` dependency:

```bash
pip install -e '.[mcp]'      # or: pip install 'mcp[cli]>=1.0,<2.0'
```

```bash
# Start the emulator with the RPC server enabled:
python . path/to/game.rom --rpc

# Register this MCP server with Claude Code (once, writes .mcp.json):
claude mcp add --transport stdio --scope project msx-emulator \
    -- python tools/mcp_server.py
claude mcp list        # msx-emulator  ●  connected
```

Claude Code then launches this process automatically per session. The socket
path is read from `MSX_RPC_SOCKET` (default `/tmp/py_msx_emu.sock`) —
settable in the `.mcp.json` `env` block when registered as above.
