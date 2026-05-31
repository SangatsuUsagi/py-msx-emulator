FLAG_S = 0x80
FLAG_Z = 0x40
FLAG_H = 0x10
FLAG_PV = 0x04
FLAG_N = 0x02
FLAG_C = 0x01


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


def parity(value: int) -> bool:
    v = value & 0xFF
    v ^= v >> 4
    v ^= v >> 2
    v ^= v >> 1
    return not (v & 1)
