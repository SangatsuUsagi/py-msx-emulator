FLAG_S = 0x80
FLAG_Z = 0x40
FLAG_Y = 0x20  # undocumented: bit 5 of the per-family source value
FLAG_H = 0x10
FLAG_X = 0x08  # undocumented: bit 3 of the per-family source value
FLAG_PV = 0x04
FLAG_N = 0x02
FLAG_C = 0x01


def parity(value: int) -> bool:
    v = value & 0xFF
    v ^= v >> 4
    v ^= v >> 2
    v ^= v >> 1
    return not (v & 1)
