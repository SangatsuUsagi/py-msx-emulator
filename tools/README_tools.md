# Tools

Standalone command-line utilities for use with the emulator: disk image
management, tape WAV converters, CPU instruction test runners, and the
agent-facing RPC/MCP layer. None of these tools run in the
emulator process. Each one is a separate `python tools/<script>.py` command.

| Script / section | Purpose |
| --- | --- |
| [`dskftp.py`](#dskftppy) | Interactive ftp-style shell to edit the files in a `.dsk` image |
| [`dskblank.py`](#dskblankpy) | Create a blank, unformatted `.dsk` image |
| [`dskdd.py`](#dskddpy) | Copy raw sectors between a `.dsk` image and a physical USB floppy drive (Linux only) |
| [`bas2wav.py`](#bas2wavpy) | Convert an ASCII BASIC program to a tape WAV that a real MSX loads with `LOAD"CAS:"` |
| [`wav2cas.py`](#wav2caspy) | Convert a WAV of an MSX tape to a `.cas` file for the emulator's `--tape` |
| [`run_z80_exercisers.py`](#run_z80_exerciserspy) | Run Z80 instruction exercisers (zexdoc, zexall, z80test) on the bare CPU core |
| [`run_singlestep_tests.py`](#run_singlestep_testspy) | Run the SingleStepTests/z80 single-instruction cases on the bare CPU core |
| [`golden_state.py`](#golden_statepy) | Run the golden cases of the whole core, and write or check their hashes |
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
like a disk with formatted tracks but no file system. The script writes no boot
sector, BPB, or FAT. Format the image after you create it, with MSX BASIC `CALL FORMAT` in
the emulator or with `dskftp.py format`.

```bash
python tools/dskblank.py blank.dsk               # 720 KB (2DD), the default
python tools/dskblank.py blank.dsk --size 360    # 360 KB (1DD)
python tools/dskblank.py blank.dsk --force       # overwrite an existing file
```

## `dskdd.py`

Copies raw sectors between a `.dsk` image and a USB-connected 3-mode floppy
drive. **Linux only.** It reads and writes the disk with plain block-device
I/O, as `dd` does:

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

## `bas2wav.py`

Converts an ASCII BASIC program (a `.BAS` text file) to a WAV file of an MSX
tape. Play the WAV into a real MSX to load the program, instead of typing it
in. The WAV holds an MSX ASCII tape file at 1200 baud, the same format that
`SAVE"CAS:NAME"` writes.

```bash
python tools/bas2wav.py PROGRAM.BAS program.wav
python tools/bas2wav.py PROGRAM.BAS program.wav --invert   # inverted signal
```

To load the program:

1. Connect the computer's headphone output to the **EAR** plug of the MSX
   cassette cable. Do not connect the MIC and REMOTE plugs.
2. On the MSX, type `LOAD"CAS:"` (or `RUN"CAS:"` to start the program after
   it loads).
3. Play the WAV. The MSX shows `Found:` and the file name when it finds the
   file.

If the MSX does not find the file or shows an error, try a higher volume, then
`--invert`.

| Option | Meaning |
| --- | --- |
| `--name NAME` | Tape file name, up to 6 characters (default: the file name of the source, in upper case) |
| `--invert` | Invert the signal |
| `--volume V` | Peak level, more than 0 and at most 1 (default: 0.8) |
| `--gap S` | Seconds of silence before each 256-byte block, so BASIC has time to process the block (default: 1.0) |

The script changes the line ends to CRLF. The output is 48 kHz, 16-bit mono.
A WAV from this script, converted with [`wav2cas.py`](#wav2caspy), loads in
the emulator. Loading on real hardware has not been tested yet.

## `wav2cas.py`

Converts a WAV file of an MSX tape to a `.cas` file, for the emulator's
`--tape` option. It reads the standard 1200-baud format, the format that
`SAVE"CAS:"` writes and that `bas2wav.py` makes.

```bash
python tools/wav2cas.py tape.wav tape.cas
python . --tape tape.cas    # then type RUN"CAS:" (or LOAD"CAS:") on the MSX
```

The script prints the length and the first bytes of each block that it
finds. It reads 8-bit and 16-bit WAV files. It averages stereo to mono. It
reads the normal and the inverted signal without an option. If the script
finds no block, it writes no file and exits with status `1`.

## `run_z80_exercisers.py`

Runs Z80 instruction exercisers on the bare `Z80` core, with no MSX machine
around it: CP/M `.com` programs (zexdoc, zexall) and the `.tap` images of
[z80test](https://github.com/raxoft/z80test). The test programs are not in
this repository. Pass their paths. The exit status is 1 if any program
reports a failure, else 0.

```bash
venvs/pypy-msx-emulator/bin/python tools/run_z80_exercisers.py \
    path/to/zexdoc.com path/to/zexall.com path/to/z80full.tap
```

Use PyPy: zexdoc and zexall take about 1.5 minutes each on PyPy and much
longer on CPython. See
[Instruction exercisers](../docs/technical-implementation.md#instruction-exercisers)
for how each format runs and the expected result.

## `run_singlestep_tests.py`

Runs the [SingleStepTests/z80](https://github.com/SingleStepTests/z80) cases
on the bare `Z80` core. Each file holds 1000 cases for one opcode: the CPU
state before and after one instruction. The tool runs one instruction per
case and compares every register, MEMPTR (WZ), Q, IFF1/IFF2, IM, the RAM, the
port transactions and the T-state count. It does not compare the bus state of
each cycle. The test files are not in this repository. Pass their paths.

```bash
venvs/pypy-msx-emulator/bin/python tools/run_singlestep_tests.py \
    path/to/SingleStepTests/z80/v1/*.json
```

Use PyPy for the full set (all 1604 files take about 3 minutes). On CPython,
`--limit N` runs only the first N cases of each file. Cases where the core
deliberately gives another result are listed, with their sources, in
`tools/singlestep_known_divergences.json` (`--divergences <file>` uses another
list). They are counted separately and do
not fail the run. An entry that no longer matches any case is reported as
stale. The exit status is 1 on any other failure or on a stale entry, else 0.

The cases come from an emulator core, not from real hardware. A port of the
core to another language can run the same files and the same divergence
list.

---

## `golden_state.py`

Runs fixed cases of the whole core and compares hashes of the result with
`tests/vectors/golden_state.json`. A case gives a machine, an optional slot 1
cartridge, an input script and checkpoint frames. At each checkpoint the file
can record the SHA-256 of each section of the saved state (`cpu`, `memory`,
`vdp`, `psg` and the others), of the audio up to that frame, and of the video
of that frame. A mismatch names the case, the frame and the section.

The file in the repository holds the cases and their checkpoint frames, with
no hashes yet. The save-state layout still changes (cassette, disk and other
devices), so the hashes are recorded before the port of the core starts,
after a review of the core and of the cases. The check reports a case with no
recorded hashes and does not fail on it.

```bash
venvs/py-msx-emulator/bin/python tools/golden_state.py           # check
venvs/py-msx-emulator/bin/python tools/golden_state.py --write   # record
```

To check that a change keeps the behavior, record a copy of the file before
the change and check against it after the change with `--vectors <copy>`.
After the hashes are in the repository, run `--write` after an intended change
of behavior and commit the new file. `tests/test_golden_state.py` runs the
same check in the test suite.

The ROMs are not in this repository. A case whose ROM is absent, or has
another SHA-256, is skipped, with the path in the reason. Put these files in
place:

| Path | Source | SHA-256 |
| --- | --- | --- |
| `roms/cbios/` | [C-BIOS](https://cbios.sourceforge.net/), as for any C-BIOS machine | (not checked) |
| `roms/homebrew/noborunoca.rom` | `noborunoca.rom` of [NOBORUNOCA v1.0.2](https://github.com/h1romas4/noborunoca/releases/tag/v1.0.2) (MIT) | `57a3be2fe5c3190daa3e69cf23cd262cdc8af98c60993ea941d43c815cbb25f6` |
| `roms/homebrew/uno-msx2.rom` | `uno-msx2.rom` of [commodore-uno v1.0.5](https://github.com/jhonnaker1/commodore-uno/releases/tag/v1.0.5) (MIT), or a build of v1.0.5 (below) | `74d74d8720e623a7b8aadce8e0b0c9094f8a2162339a8e7c677c7732f673ddb8` |

The SHA-256 in the table comes from a build of commodore-uno v1.0.5. Another version
can give another ROM, so use v1.0.5. The `uno-msx2.rom` files of the v1.0.5 and
v1.0.10 releases have the same SHA-256 as that build. To build the ROM, use
[SDCC](https://sdcc.sourceforge.net/) and GNU make:

```bash
# 1. Get the source
git clone --branch v1.0.5 https://github.com/jhonnaker1/commodore-uno.git
cd commodore-uno/msx2

# 2. Build: the ROM is build/uno.rom (16 KB)
make
```

Then copy `build/uno.rom` to `roms/homebrew/uno-msx2.rom`, and check that its
SHA-256 is the value in the table.

The hashes record what this core does, not what a real MSX does. They catch a
change in behavior, and a port of the core can compare itself with the
Python core by running the same cases.

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
- tape insert/eject/rewind/seek

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
