from msx.vdp.vdp import VDP


def make_vdp() -> VDP:
    return VDP()


def set_write_addr(vdp: VDP, addr: int) -> None:
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, 0x40 | ((addr >> 8) & 0x3F))


def set_read_addr(vdp: VDP, addr: int) -> None:
    vdp.write_port(0x99, addr & 0xFF)
    vdp.write_port(0x99, (addr >> 8) & 0x3F)


# --- Display geometry ---

def test_vblank_start_line_equals_display_height() -> None:
    vdp = make_vdp()
    assert vdp.vblank_start_line == vdp.display_height == 192


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
    vdp.write_port(0x99, 0x80 | 3)    # register select: reg 3 (all 8 bits)
    assert vdp.regs[3] == 0x42


def test_register_write_all_eight() -> None:
    vdp = make_vdp()
    for reg in range(8):
        vdp.write_port(0x99, reg * 10)
        vdp.write_port(0x99, 0x80 | reg)
    masks = (0x03, 0xFB, 0x0F, 0xFF, 0x07, 0x7F, 0x07, 0xFF)  # implemented bits
    for reg in range(8):
        assert vdp.regs[reg] == (reg * 10) & masks[reg]


def test_register_write_ignores_bit6_of_second_byte() -> None:
    # Unlike the V9938, a 0xC0-0xFF second byte is still a register write.
    vdp = make_vdp()
    vdp.write_port(0x99, 0x42)
    vdp.write_port(0x99, 0xC0 | 3)
    assert vdp.regs[3] == 0x42


def test_register_write_also_sets_address_high_bits() -> None:
    # Unlike the V9938, a real MSX1 VDP's register write also perturbs the
    # VRAM address pointer's high 6 bits from the second control byte
    # (openMSX VDP.cc, thanks-to-dvik; confirmed on real MSX1 hardware --
    # see allium/tms9918a.allium's WriteControlPort).
    vdp = make_vdp()
    vdp.write_port(0x99, 0x42)        # data byte (also sets addr's low
                                       # byte to 0x42, see the test below)
    vdp.write_port(0x99, 0x80 | 3)    # register select: reg 3
    assert vdp.regs[3] == 0x42
    assert vdp.addr == 0x0342         # high bits from (0x80|3) & 0x3F = 3



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


def test_status_read_clears_coincidence_flag() -> None:
    vdp = make_vdp()
    vdp.status = 0xFF
    vdp.read_port(0x99)
    assert not (vdp.status & 0x20)  # coincidence flag cleared


def test_status_read_preserves_fifth_sprite_index() -> None:
    vdp = make_vdp()
    vdp.status = 0xFF
    vdp.read_port(0x99)
    assert (vdp.status & 0x1F) == 0x1F  # bits 4:0 (5th-sprite index) untouched


def test_status_read_substitutes_1f_for_stale_fifth_sprite_index_when_5s_clear() -> None:
    # C-BIOS-safety hack (matches msx/vdp/v9938.py's own read_port): bits
    # 4:0 read as 0x1F when 5S (bit 6) is clear, not whatever they actually
    # hold -- some MSX BIOS/cartridge boot code feeds this into a loop
    # counter and 0 there stalls it.
    vdp = make_vdp()
    vdp.status = 0x00  # F/5S/C all clear, bits 4:0 = 0
    result = vdp.read_port(0x99)
    assert (result & 0x1F) == 0x1F, "returned byte substitutes 31 for the stale index"


def test_status_read_does_not_substitute_when_5s_set() -> None:
    vdp = make_vdp()
    vdp.status = 0x40 | 0x03  # 5S set, real index = 3
    result = vdp.read_port(0x99)
    assert (result & 0x1F) == 0x03, "the real index is returned, not substituted, when 5S is set"


# --- Address latch and read-ahead buffer ---

def test_first_control_byte_directly_sets_address_low_byte() -> None:
    # Real MSX1 VDPs use no latch for the address pointer itself: unlike
    # the V9938, the first control byte writes straight into addr's low
    # byte, even before a second byte completes the sequence (openMSX
    # VDP.cc, thanks-to-hap; confirmed on a real TMS9929A -- see
    # allium/tms9918a.allium's WriteControlPort).
    vdp = make_vdp()
    vdp.addr = 0x3F00  # arbitrary high bits already set
    vdp.write_port(0x99, 0xAB)  # single, stranded control byte
    assert vdp.addr == 0x3FAB


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


# --- Data port access resets the control latch ---

def test_data_port_write_discards_stray_control_byte() -> None:
    # A stray, unpaired first control byte still perturbs the address's low
    # byte directly (see test_first_control_byte_directly_sets_address_low_
    # byte) -- what "discards" here is the pending control PAIR: the latch
    # clears on the data-port access, so the stray byte never completes as
    # a register write or an address-setup high byte later on.
    vdp = make_vdp()
    set_write_addr(vdp, 0x0100)
    vdp.write_port(0x99, 0x55)  # stray first byte -- addr low byte -> 0x55
    vdp.write_port(0x98, 0xAB)
    assert vdp.vram[0x0155] == 0xAB  # high byte (0x01) kept, low byte -> 0x55
    vdp.write_port(0x99, 0x0E)
    vdp.write_port(0x99, 0x80 | 2)
    assert vdp.regs[2] == 0x0E


def test_data_port_read_discards_stray_control_byte() -> None:
    vdp = make_vdp()
    vdp.vram[0x0200] = 0x5A
    set_read_addr(vdp, 0x0200)
    vdp.write_port(0x99, 0x55)  # stray first byte
    assert vdp.read_port(0x98) == 0x5A
    vdp.write_port(0x99, 0x0E)
    vdp.write_port(0x99, 0x80 | 2)
    assert vdp.regs[2] == 0x0E


def test_interrupted_address_setup_resyncs_at_next_data_access() -> None:
    # An ISR landing between the two OUT (99h) of an address setup reads S#0
    # (resetting the latch) and writes R#1; the stranded high byte must not
    # shift later control pairs once the next data-port access happens.
    vdp = make_vdp()
    vdp.regs = [0x02, 0xE2, 0x0E, 0xFF, 0x03, 0x76, 0x03, 0x00]
    before = list(vdp.regs)
    vdp.write_port(0x99, 0xA8)        # address low byte
    vdp.read_port(0x99)               # ISR: status read
    vdp.write_port(0x99, 0xE2)        # ISR: R#1 := E2
    vdp.write_port(0x99, 0x80 | 1)
    vdp.write_port(0x99, 0x3A)        # stranded address high byte
    vdp.read_port(0x98)               # next data-port access
    vdp.write_port(0x99, 0x00)        # fresh R#7 := 00
    vdp.write_port(0x99, 0x80 | 7)
    set_write_addr(vdp, 0x3800)
    vdp.write_port(0x98, 0xAB)
    assert vdp.regs == before
    assert vdp.vram[0x3800] == 0xAB


def test_data_port_read_returns_buffered_byte_not_live_vram() -> None:
    # Read-ahead: the byte returned is the one buffered by the previous
    # access, so a VRAM change after the buffer filled is not seen yet.
    vdp = make_vdp()
    vdp.vram[0x0300] = 0x11
    vdp.vram[0x0301] = 0x22
    set_read_addr(vdp, 0x0300)
    vdp.vram[0x0300] = 0x99
    assert vdp.read_port(0x98) == 0x11
    assert vdp.read_buf == 0x22
    assert vdp.addr == 0x0302


def test_data_port_read_leaves_status_flags_alone() -> None:
    vdp = make_vdp()
    vdp.status = 0xE5
    set_read_addr(vdp, 0x0000)
    vdp.read_port(0x98)
    assert vdp.status == 0xE5


def test_status_read_resets_control_latch() -> None:
    vdp = make_vdp()
    vdp.write_port(0x99, 0x55)  # stray first byte
    vdp.read_port(0x99)
    assert vdp.latch is None
    vdp.write_port(0x99, 0x0E)
    vdp.write_port(0x99, 0x80 | 2)
    assert vdp.regs[2] == 0x0E


# --- Reset ---

def test_reset_restores_registers_status_and_address_state() -> None:
    vdp = make_vdp()
    vdp.regs = [0xFF] * 8
    vdp.status = 0xFF
    vdp.addr = 0x1234
    vdp.latch = 0x55
    vdp.read_buf = 0x99

    vdp.reset()

    assert vdp.regs == [0] * 8
    assert vdp.status == 0
    assert vdp.addr == 0
    assert vdp.latch is None
    assert vdp.read_buf == 0


def test_reset_retains_vram() -> None:
    vdp = make_vdp()
    vdp.vram[0x1000] = 0xAB

    vdp.reset()

    assert vdp.vram[0x1000] == 0xAB


# --- Register value masks (TI data manual §2.2.1: undefined bits don't exist) ---

def _write_reg(vdp: VDP, reg: int, value: int) -> None:
    vdp.write_port(0x99, value)
    vdp.write_port(0x99, 0x80 | reg)


def test_r0_bit2_is_not_stored() -> None:
    vdp = make_vdp()
    _write_reg(vdp, 0, 0x06)
    assert vdp.regs[0] == 0x02


def test_each_register_keeps_only_implemented_bits() -> None:
    vdp = make_vdp()
    for reg in range(8):
        _write_reg(vdp, reg, 0xFF)
    assert vdp.regs == [0x03, 0xFB, 0x0F, 0xFF, 0x07, 0x7F, 0x07, 0xFF]
