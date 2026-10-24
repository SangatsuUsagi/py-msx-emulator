# Tools

Standalone command-line utilities for use with the emulator: disk image
management, and the agent-facing RPC/MCP layer. None of these tools run in the
emulator process. Each one is a separate `python tools/<script>.py` command.

| Script / section | Purpose |
| --- | --- |
| [`dskftp.py`](#dskftppy) | Interactive ftp-style shell to edit the files in a `.dsk` image |
| [`dskblank.py`](#dskblankpy) | Create a blank, unformatted `.dsk` image |
| [`dskdd.py`](#dskddpy) | Copy raw sectors between a `.dsk` image and a physical USB floppy drive (Linux only) |
| [Remote control (Socket RPC & MCP)](#remote-control-socket-rpc--mcp) | Overview of the socket RPC + MCP control layer that `rpc_client.py` and `mcp_server.py` use |
| [`rpc_client.py`](#rpc_clientpy) | Thin CLI client for the socket RPC server of the emulator, for manual testing |
| [`mcp_server.py`](#mcp_serverpy) | MCP server that wraps the socket RPC API, so Claude Code can drive a running emulator |

All scripts are plain Python 3.10+. They need no dependencies other than the
dependencies of the project (`requirements.txt` / `requirements-dev.txt`). The
exception is `mcp_server.py`, which also needs the optional `mcp` extra (see
[`mcp_server.py`](#mcp_serverpy)). Run the scripts with the project venv, e.g.
`venvs/py-msx-emulator/bin/python tools/dskftp.py`.

---

## `dskftp.py`

An interactive, ftp-style shell for MSX-DOS (FAT12) `.dsk` disk images. You can
browse directories and upload, download, or delete files without a boot of the
emulator. The shell supports the standard MSX floppy formats (1DD
360KB/320KB, 2DD 720KB/640KB). This includes images that MSX BASIC
`CALL FORMAT` or the `format` command of this tool formatted. The shell
supports subdirectories (MSX-DOS2). An MSX-DOS1 image acts as an image that has
only a root directory.

The shell writes all changes directly to the image file. There is no separate
save step. Transfers are always binary, with no CR/LF conversion.

```bash
python tools/dskftp.py game.dsk        # connect on startup
python tools/dskftp.py                 # connect later with `open`
```

Commands in the shell (see `help` for the full list with usage strings):

| Command | Effect |
| --- | --- |
| `open <image.dsk>` | Connect to a disk image |
| `format <image.dsk> [720\|640\|360\|320]` | Create an empty image (KB, default 720) and connect to it |
| `dir` / `ls [path]` | List a remote directory (the path can end in a wildcard) |
| `cd <dir>` | Change the remote directory |
| `lcd [dir]` | Change the local directory (no argument: print it) |
| `pwd` / `lpwd` | Print the remote / local working directory |
| `get <remote> [local]` | Download a file |
| `put <local> [remote]` (alias `send`) | Upload a file |
| `mget <pattern>` / `mput <pattern>` | Download / upload several files that match a glob pattern |
| `delete <file>` | Delete a remote file |
| `mkdir <dir>` / `rmdir <dir>` | Create / remove a remote directory |
| `quit` / `bye` (or Ctrl-D) | Leave the shell |

`format` writes a data disk. The disk has the standard MSX-DOS boot sector
loader (the "Boot error / Press any key for retry" stub that `CALL FORMAT` also
writes), but no `MSXDOS.SYS`. It cannot load DOS. You can only read and write
it as a plain FAT12 volume.

## `dskblank.py`

Creates a blank `*.dsk` image for use with `--fdd1` / `--fdd2`. The script
fills the file with `0xE5`, the standard floppy fill byte. The file thus looks
like media that is formatted but empty. The script writes no boot sector, BPB,
or FAT. Format the image after you create it, with MSX BASIC `CALL FORMAT` in
the emulator or with `dskftp.py format`.

```bash
python tools/dskblank.py blank.dsk               # 720 KB (2DD), the default
python tools/dskblank.py blank.dsk --size 360    # 360 KB (1DD)
python tools/dskblank.py blank.dsk --force       # overwrite an existing file
```

## `dskdd.py`

Copies raw sectors between a `.dsk` image and a USB-connected 3-mode floppy
drive. **Linux only.** It implements both directions of "plain USB FDD + dd"
from
[`extras/msx_2dd_disk_imaging_linux.md`](../extras/msx_2dd_disk_imaging_linux.md):

```bash
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk            # disk -> image
sudo python3 tools/dskdd.py read /dev/sdb msxdisk.dsk --retries 5
sudo python3 tools/dskdd.py write msxdisk.dsk /dev/sdb --verify  # image -> disk
```

On Linux, a USB FDD appears as `/dev/sdX` (a block device), not as `/dev/fd0`.
Before you run this script, do these steps:

1. Run `sudo ufiformat -i` to find the device node.
2. Run `sudo ufiformat -i /dev/sdX` to confirm the inserted media.

Both directions refuse to run unless all of these conditions are true:

- The device is a block device.
- The device is not mounted.
- The device holds media.
- The device is small enough to be a floppy (≤4 MB, `--force` overrides this
  check).

`write` also makes the operator type the device path again, because a write to
the wrong device destroys all data on it. `read` does not overwrite an existing
image unless you give `--overwrite`.

| Subcommand | Options |
| --- | --- |
| `read <device> <image>` | `--sectors N` (default: whole device), `--retries N` (default: 2), `--overwrite`, `--force` |
| `write <image> <device>` | `--yes` (skip confirmation), `--verify` (read back and compare), `--force` |

Exit status: `0` success, `1` error, `2` the script imaged the disk, but some
sectors stayed unreadable (zero-filled in the image, listed on stdout).

## Remote control (Socket RPC & MCP)

The emulator can expose a small local control surface for external tools, such
as shell scripts, a test harness, or an AI coding agent. These tools can pause,
examine, and drive a running instance. There are two layers:

- **Socket RPC**: a Unix-domain-socket JSON-RPC server in the emulator process
  (`host/rpc_server.py`). It is **off by default**. Enable it with `--rpc`.
- **MCP server**: [`mcp_server.py`](#mcp_serverpy) below, a standalone stdio
  server that wraps the socket RPC as
  [Model Context Protocol](https://modelcontextprotocol.io) tools. A client
  such as Claude Code can thus call emulator functions as native tools, and get
  screenshots as inline images.

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

The RPC methods cover these functions:

- debugger pause/step/continue
- breakpoints and watchpoints
- memory read/write, VRAM read, disassembly
- VDP registers
- keyboard/joystick injection
- screenshot capture
- state save/load
- disk swap

[`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) documents the wire
protocol and the full method reference.

For manual testing without the MCP layer, see [`rpc_client.py`](#rpc_clientpy)
below. To let an MCP client drive the emulator, see
[`mcp_server.py`](#mcp_serverpy) below.

### Security notes

- Only local processes that run as the same user can reach the Unix socket.
- `memory.write` and `cpu.step` change the machine state, so they work
  **only while the emulator is paused**.
- There is no authentication. On a shared host, restrict the socket with
  `chmod 600`. If you do not, any other local user can connect and use
  `memory.write`/`cpu.step` to change the state of the running machine. The
  server is opt-in (`--rpc`) because it is a control surface. No socket exists
  unless you ask for one.

## `rpc_client.py`

A thin CLI client for the Unix-socket JSON-RPC server of the emulator, for
manual testing without the MCP layer. It sends one method call and prints the
response. The emulator must run with `--rpc`. See
[`../docs/socket-rpc-mcp.md`](../docs/socket-rpc-mcp.md) for the full method
reference.

```bash
python tools/rpc_client.py debugger.status
python tools/rpc_client.py debugger.pause
python tools/rpc_client.py memory.read address=0xC000 length=16
python tools/rpc_client.py debug.set_breakpoint '{"address": "0xC000"}'
python tools/rpc_client.py --socket /tmp/alt.sock cpu.get_registers
```

Give params as a JSON object or as `key=value` pairs. The client converts each
value to `int`, `bool`, or `str`, as applicable. The default socket path is
`/tmp/py_msx_emu.sock`.

## `mcp_server.py`

Wraps the socket RPC API of the emulator as MCP tools, so an MCP client such as
Claude Code can control a running emulator directly. The main operations are:

- pause/step/continue the Z80
- set breakpoints and watchpoints
- read/write memory, read VRAM
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

Claude Code then starts this process automatically for each session. The
server reads the socket path from `MSX_RPC_SOCKET` (default
`/tmp/py_msx_emu.sock`). If you registered the server as above, you can set
this variable in the `env` block of `.mcp.json`.
