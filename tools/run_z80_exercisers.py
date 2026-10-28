#!/usr/bin/env python3
"""Run Z80 instruction exercisers on the bare msx.cpu.z80.Z80 core.

Two program formats, picked by file extension:

- ``.com``: a CP/M program such as zexall or zexdoc (Frank D. Cringle).
  Loaded at 0x0100 with flat 64 KB RAM; BDOS functions 2 (character out)
  and 9 (``$``-terminated string out) are handled at 0x0005, and a jump to
  0x0000 ends the run. A line containing ``ERROR`` counts as a failure.
- ``.tap``: a ZX Spectrum tape image of Patrik Rak's z80test
  (https://github.com/raxoft/z80test). The CODE block is loaded at its
  address and the few Spectrum ROM services it uses are stubbed:
  CHAN-OPEN (0x1601) returns at once, RST 10h prints the character in A,
  and every port read returns 0xBF (the value the IN tests expect from the
  Spectrum ULA). ``tests failed`` in the output counts as a failure.

The programs are not part of this repository (zexall and zexdoc are GPL);
pass their paths. The expected result for this core: zexall, zexdoc and
every z80test variant pass in full. z80test checks a repeating iteration
only for INIR and INDR (INIR->NOP', INDR->NOP'); the repeat flags and MEMPTR
of OTIR/OTDR are checked by unit tests only.

The exit status is 1 if any program reports a failure, else 0. PyPy runs
zexall in about 90 seconds; CPython takes much longer.

Usage:
    ./venvs/pypy-msx-emulator/bin/python tools/run_z80_exercisers.py \\
        path/to/zexall.com path/to/z80full.tap
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(os.environ.get("PY_MSX_ROOT", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(_PROJECT_ROOT))

from msx.cpu.z80 import Z80  # noqa: E402

_EXIT_ADDRESS = 0x0000  # CP/M warm boot; also the z80test return address
_CPM_ENTRY = 0x0100
_CPM_BDOS = 0x0005
_CPM_TPA_TOP_PTR = 0x0006  # operand of the JP at the BDOS entry
_CPM_STACK = 0xF000
_BDOS_CONOUT = 2
_BDOS_PRINT_STRING = 9
_SPECTRUM_CHAN_OPEN = 0x1601
_SPECTRUM_RST10 = 0x0010
_SPECTRUM_ERR_NR = 0x5C3A
_TAP_STACK = 0x7F00
_ULA_PORT_VALUE = 0xBF  # what z80test's IN tests expect from the Spectrum ULA
_RET = 0xC9


def _ula_read_port(_port: int) -> int:
    return _ULA_PORT_VALUE


def _run_cpm(data: bytes) -> tuple[str, int]:
    mem = bytearray(0x10000)
    mem[_CPM_ENTRY : _CPM_ENTRY + len(data)] = data
    mem[_CPM_BDOS] = _RET
    # Top of the TPA, read by programs that size their stack from it.
    mem[_CPM_TPA_TOP_PTR] = _CPM_STACK & 0xFF
    mem[_CPM_TPA_TOP_PTR + 1] = _CPM_STACK >> 8
    cpu = Z80(read_byte=mem.__getitem__, write_byte=mem.__setitem__)
    r = cpu.registers
    r.PC = _CPM_ENTRY
    r.SP = _CPM_STACK
    out: list[str] = []
    instructions = 0
    step = cpu.step
    while True:
        pc = r.PC
        if pc == _CPM_BDOS:
            if r.C == _BDOS_CONOUT:
                _emit(out, chr(r.E))
            elif r.C == _BDOS_PRINT_STRING:
                addr = r.DE
                while mem[addr] != ord("$"):
                    _emit(out, chr(mem[addr]))
                    addr = (addr + 1) & 0xFFFF
        elif pc == _EXIT_ADDRESS:
            break
        step()
        instructions += 1
    return "".join(out), instructions


def _load_tap_code(data: bytes, mem: bytearray) -> int:
    """Copy the first CODE block of a TAP image into mem; return its address."""
    blocks: list[bytes] = []
    pos = 0
    while pos + 2 <= len(data):
        length = data[pos] | (data[pos + 1] << 8)
        blocks.append(data[pos + 2 : pos + 2 + length])
        pos += 2 + length
    for i in range(len(blocks) - 1):
        header = blocks[i]
        # Header block: flag 0x00, type 3 (CODE), param 1 = load address.
        if len(header) >= 16 and header[0] == 0x00 and header[1] == 3:
            address = header[14] | (header[15] << 8)
            code = blocks[i + 1][1:-1]  # drop the flag and checksum bytes
            mem[address : address + len(code)] = code
            return address
    raise ValueError("no CODE block in the TAP image")


def _run_tap(data: bytes) -> tuple[str, int]:
    mem = bytearray(0x10000)
    entry = _load_tap_code(data, mem)
    mem[_SPECTRUM_CHAN_OPEN] = _RET
    mem[_SPECTRUM_RST10] = _RET
    cpu = Z80(read_byte=mem.__getitem__, write_byte=mem.__setitem__, read_port=_ula_read_port)
    r = cpu.registers
    r.PC = entry
    r.SP = _TAP_STACK  # holds the return address _EXIT_ADDRESS (memory is zeroed)
    r.IY = _SPECTRUM_ERR_NR
    out: list[str] = []
    skip = 0  # operand bytes left of a Spectrum TAB control code
    instructions = 0
    step = cpu.step
    while True:
        pc = r.PC
        if pc == _SPECTRUM_RST10:
            ch = r.A
            if skip > 0:
                skip -= 1
            elif ch == 23:  # TAB n, m: the column goes as two more characters
                skip = 2
                _emit(out, " ")
            elif ch == 13:
                _emit(out, "\n")
            elif ch == 127:
                _emit(out, "(c)")
            elif 32 <= ch < 127:
                _emit(out, chr(ch))
        elif pc == _EXIT_ADDRESS:
            break
        step()
        instructions += 1
    return "".join(out), instructions


def _emit(out: list[str], text: str) -> None:
    out.append(text)
    sys.stdout.write(text)
    sys.stdout.flush()


def _run(path: Path) -> bool:
    """Run one program; return True if it reports no failure."""
    data = path.read_bytes()
    print(f"== {path.name}")
    start = time.perf_counter()
    # main() has already checked the suffix is .com or .tap.
    if path.suffix.lower() == ".com":
        text, instructions = _run_cpm(data)
        passed = "ERROR" not in text
    else:
        text, instructions = _run_tap(data)
        passed = "tests failed" not in text
    elapsed = time.perf_counter() - start
    print(f"\n[{instructions} instructions in {elapsed:.1f} s]\n")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Z80 instruction exercisers on the Z80 core.")
    parser.add_argument("programs", nargs="+", type=Path, help="CP/M .com or z80test .tap files")
    args = parser.parse_args()
    for path in args.programs:
        if path.suffix.lower() not in (".com", ".tap"):
            parser.error(f"{path}: expected a .com or .tap file")
        if not path.is_file():
            parser.error(f"{path}: not found")
    failed: list[str] = []
    for path in args.programs:
        if not _run(path):
            failed.append(path.name)
    if failed:
        print("Failures reported by: " + ", ".join(failed))
        return 1
    print("All programs passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
