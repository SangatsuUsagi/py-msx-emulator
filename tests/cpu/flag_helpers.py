"""Test-local flag pack/unpack helpers.

These mirror the flag-byte construction used by the register tests. They were
relocated here (verbatim from the former ``msx/cpu/flags.py`` bodies) so that
production ``msx/cpu/flags.py`` exposes only the constants (``FLAG_*``) and the
``parity`` helper that production code actually uses.
"""

from msx.cpu.flags import FLAG_C, FLAG_H, FLAG_N, FLAG_PV, FLAG_S, FLAG_Z


def pack(s: bool, z: bool, h: bool, pv: bool, n: bool, c: bool) -> int:
    result = 0
    if s:
        result |= FLAG_S
    if z:
        result |= FLAG_Z
    if h:
        result |= FLAG_H
    if pv:
        result |= FLAG_PV
    if n:
        result |= FLAG_N
    if c:
        result |= FLAG_C
    return result


def unpack(f: int) -> tuple[bool, bool, bool, bool, bool, bool]:
    return (
        bool(f & FLAG_S),
        bool(f & FLAG_Z),
        bool(f & FLAG_H),
        bool(f & FLAG_PV),
        bool(f & FLAG_N),
        bool(f & FLAG_C),
    )
