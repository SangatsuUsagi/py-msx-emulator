from msx.vdp.vdp import VDP


def make_vdp() -> VDP:
    return VDP()


def set_write_addr(vdp: VDP, addr: int) -> None:
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, 0x40 | ((addr >> 8) & 0x3F))


def set_read_addr(vdp: VDP, addr: int) -> None:
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, (addr >> 8) & 0x3F)


# --- VRAM ---

def test_vram_initialises_to_zero() -> None:
    vdp = make_vdp()
    assert all(b == 0 for b in vdp.vram)
    assert len(vdp.vram) == 0x4000


def test_vram_write_and_readback() -> None:
    vdp = make_vdp()
    set_write_addr(vdp, 0x1234)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x1234] == 0xAB


def test_sequential_writes_advance_address() -> None:
    vdp = make_vdp()
    set_write_addr(vdp, 0x0000)
    vdp.write_port(0x98, 0x11)
    vdp.write_port(0x98, 0x22)
    vdp.write_port(0x98, 0x33)
    assert vdp.vram[0x0000] == 0x11
    assert vdp.vram[0x0001] == 0x22
    assert vdp.vram[0x0002] == 0x33


def test_write_address_wraps_at_16kb() -> None:
    vdp = make_vdp()
    set_write_addr(vdp, 0x3FFF)
    vdp.write_port(0x98, 0xFF)
    assert vdp.addr == 0x0000


# --- Registers ---

def test_registers_initialise_to_zero() -> None:
    vdp = make_vdp()
    assert vdp.regs == [0] * 8


def test_register_write_via_control_port() -> None:
    vdp = make_vdp()
    vdp.write_port(0x99, 0x42)        # data byte
    vdp.write_port(0x99, 0x80 | 2)    # register select: reg 2
    assert vdp.regs[2] == 0x42


def test_register_write_all_eight() -> None:
    vdp = make_vdp()
    for reg in range(8):
        vdp.write_port(0x99, reg * 10)
        vdp.write_port(0x99, 0x80 | reg)
    for reg in range(8):
        assert vdp.regs[reg] == reg * 10


# --- Status register ---

def test_status_read_returns_current_value() -> None:
    vdp = make_vdp()
    vdp.status = 0xFF
    result = vdp.read_port(0x99)
    assert result == 0xFF


def test_status_read_clears_vblank_and_fifth_sprite_flags() -> None:
    vdp = make_vdp()
    vdp.status = 0xFF
    vdp.read_port(0x99)
    assert not (vdp.status & 0x80)  # VBlank cleared
    assert not (vdp.status & 0x40)  # 5th-sprite flag cleared


# --- Address latch and read-ahead buffer ---

def test_write_address_setup() -> None:
    vdp = make_vdp()
    set_write_addr(vdp, 0x0000)
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x0000] == 0xAB


def test_read_mode_preloads_buffer() -> None:
    vdp = make_vdp()
    vdp.vram[0x0000] = 0x77
    set_read_addr(vdp, 0x0000)
    result = vdp.read_port(0x98)
    assert result == 0x77


def test_sequential_reads_advance_address() -> None:
    vdp = make_vdp()
    vdp.vram[0x0000] = 0x11
    vdp.vram[0x0001] = 0x22
    vdp.vram[0x0002] = 0x33
    set_read_addr(vdp, 0x0000)
    assert vdp.read_port(0x98) == 0x11
    assert vdp.read_port(0x98) == 0x22
    assert vdp.read_port(0x98) == 0x33


def test_read_address_wraps_at_16kb() -> None:
    vdp = make_vdp()
    vdp.vram[0x3FFF] = 0xAA
    vdp.vram[0x0000] = 0xBB
    set_read_addr(vdp, 0x3FFF)
    assert vdp.read_port(0x98) == 0xAA  # reads 0x3FFF
    assert vdp.read_port(0x98) == 0xBB  # wraps to 0x0000
