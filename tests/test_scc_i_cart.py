"""Tests for the SCC-I cartridge mapper (msx/mapper.py:SCCICart)."""
from msx.mapper import SCCICart
from msx.scc import SCC


def _cart() -> SCCICart:
    return SCCICart(scc=SCC(is_052539=True))


# ---------------------------------------------------------------------------
# Bank-switched RAM
# ---------------------------------------------------------------------------

def test_ram_blank_at_construction() -> None:
    cart = _cart()
    assert all(cart.read(addr) == 0x00 for addr in range(0x4000, 0xC000, 0x400))


def test_bank_register_value_masked_to_4_bits() -> None:
    cart = _cart()
    cart.write(0x5000, 0xFF)
    assert cart._banks[0] & 0x0F == 0x0F
    assert cart._banks[0] == 0xFF  # raw register value is stored as-is


def test_bank_switch_changes_visible_window_content() -> None:
    cart = _cart()
    # Point window 0 at block 5, temporarily make it a RAM-write region to
    # write data into that block, then point window 1 at the same block and
    # confirm it sees the same content -- proving the bank register (not the
    # window) selects which of the 16 blocks is visible.
    cart.write(0x5000, 5)  # window 0 bank register -> block 5
    cart.write(0xBFFE, 0x01)  # window 0 becomes a RAM-write region
    cart.write(0x4010, 0xAB)  # writes into block 5, offset 0x10
    cart.write(0xBFFE, 0x00)  # back to ordinary bank-register mode
    cart.write(0x7000, 5)  # window 1 bank register -> block 5 too
    assert cart.read(0x6010) == 0xAB


# ---------------------------------------------------------------------------
# Mode register: SCC mode select + RAM-write regions
# ---------------------------------------------------------------------------

def test_mode_register_selects_plus_mode_on_scc_chip() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x20)
    assert cart.scc._plus_mode is True


def test_mode_register_writable_at_either_address() -> None:
    cart = _cart()
    cart.write(0xBFFF, 0x20)
    assert cart.scc._plus_mode is True


def test_compatible_mode_scc_window_at_0x9800() -> None:
    cart = _cart()
    cart.write(0x9000, 0x3F)  # window 2 enable mask
    assert cart._scc_window_base == 0x9800
    cart.write(0x9810, 0x7F)  # waveform byte via the cartridge window
    assert cart.scc.read(0x10) == 0x7F


def test_plus_mode_scc_window_at_0xb800() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x20)  # Plus mode
    cart.write(0xB000, 0x80)  # window 3 enable bit
    assert cart._scc_window_base == 0xB800
    cart.write(0xB810, 0x7F)  # waveform byte via the cartridge window
    assert cart.scc.read(0x10) == 0x7F


def test_compatible_mode_scc_window_read_forwards_to_chip() -> None:
    # allium/scci-cartridge.allium: rule-success.ForwardReadFromScc (Compatible
    # window) -- writing directly to the carried chip must be visible through
    # the cartridge's own read(), not just via scc.read() directly.
    cart = _cart()
    cart.write(0x9000, 0x3F)  # window 2 enable mask
    cart.scc.write(0x10, 0x7F)  # waveform byte, set directly on the chip
    assert cart.read(0x9810) == 0x7F


def test_plus_mode_scc_window_read_forwards_to_chip() -> None:
    # allium/scci-cartridge.allium: rule-success.ForwardReadFromScc (Plus
    # window).
    cart = _cart()
    cart.write(0xBFFE, 0x20)  # Plus mode
    cart.write(0xB000, 0x80)  # window 3 enable bit
    cart.scc.write(0x10, 0x7F)
    assert cart.read(0xB810) == 0x7F


def test_ram_write_region_overrides_bank_register_zone() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x01)  # window 0 is a RAM-write region
    cart.write(0x5000, 0x50)  # would be a bank-register write otherwise
    assert cart._banks[0] == 0  # bank unchanged, no switch occurred
    cart.write(0xBFFE, 0x00)
    assert cart.read(0x5000) == 0x50  # the write landed in RAM instead


def test_ram_write_region_intercepts_writes_to_an_active_scc_window() -> None:
    # allium/scci-cartridge.allium: rule-failure.ForwardWriteToScc via the
    # ram_segment guard -- distinct from the bank-register-zone case covered
    # by test_ram_write_region_overrides_bank_register_zone. Window 3's
    # ram_segment can only become true via the global bit 0x10 (no individual
    # bit exists for it -- see test_window3_never_ram_write_via_its_own_bit),
    # so this is the only way to reach "SCC+ window enable condition is true
    # AND the same window is simultaneously a RAM-write region" -- a real
    # cross-cutting interaction called out in the spec's own @guidance.
    cart = _cart()
    cart.write(0xB000, 0x80)  # window 3 bank register -> SCC+ enable bit set
    cart.write(0xBFFE, 0x30)  # Plus mode (0x20) + all-windows RAM-write (0x10)
    cart.write(0xB810, 0x7F)  # would be a waveform byte if forwarded to the chip
    assert cart.scc.read(0x10) == 0x00  # write did NOT reach the chip
    # cart.read() would itself forward to the still-active SCC+ window (reads
    # are never ram_segment-gated -- see ForwardReadFromScc), so disable the
    # window (mode register back to 0) before reading to inspect the RAM cell
    # the write actually landed in.
    cart.write(0xBFFE, 0x00)
    assert cart.read(0xB810) == 0x7F  # the write landed in RAM instead


def test_window1_ram_write_bit_independent_of_window0_and_2() -> None:
    # allium/scci-cartridge.allium: config-default.ram_bit_1, previously only
    # exercised jointly with other bits (all_windows) or not at all in
    # isolation -- window 0's bit (ram_bit_0) has its own dedicated test
    # (test_ram_write_region_overrides_bank_register_zone); this is window 1's.
    cart = _cart()
    cart.write(0xBFFE, 0x02)  # window 1 RAM-write bit only
    assert cart._is_ram_segment == [False, True, False, False]
    cart.write(0x7000, 0x50)  # would be a bank-register write otherwise
    assert cart._banks[1] == 1  # bank unchanged, no switch occurred
    cart.write(0xBFFE, 0x00)
    assert cart.read(0x7000) == 0x50  # the write landed in RAM instead


def test_window2_ram_write_bit_requires_plus_mode() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x04)  # window-2 RAM bit set, Plus-mode bit clear
    assert cart._is_ram_segment[2] is False
    cart.write(0x9000, 0x3F)  # bank-register zone write still switches banks
    assert cart._banks[2] & 0x3F == 0x3F


def test_window2_ram_write_bit_with_plus_mode() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x24)  # window-2 RAM bit + Plus mode
    assert cart._is_ram_segment[2] is True


def test_all_windows_ram_write_mode() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x10)
    assert cart._is_ram_segment == [True, True, True, True]


def test_window3_never_ram_write_via_its_own_bit() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0xFF & ~0x10)  # every bit except the all-windows bit
    assert cart._is_ram_segment[3] is False


# ---------------------------------------------------------------------------
# SCC register window: enable/forwarding vs. fallback to RAM
# ---------------------------------------------------------------------------

def test_scc_window_inactive_falls_back_to_ram() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x01)  # window 0 RAM-write, unrelated to window 2
    cart.write(0x9800, 0x11)  # window 2's bank-register zone doesn't cover 0x9800
    # Window 2's bank register never satisfied the enable mask -> SCC window
    # inactive -> 0x9800 is ordinary window-2 RAM (but not a RAM-write region
    # here, so the write is ignored and the read is whatever block 2 holds).
    assert cart.read(0x9800) == 0x00


def test_bank_register_write_does_not_trigger_scc_enable_from_non_zone_addr() -> None:
    cart = _cart()
    # 0x9800 is inside window 2 but outside its bank-register zone
    # (0x9000-0x97FF), so it is not treated as a bank switch either.
    cart.write(0x9800, 0x3F)
    assert cart._banks[2] == 2  # unchanged from the power-on default


# ---------------------------------------------------------------------------
# Power-on state
# ---------------------------------------------------------------------------

def test_power_on_default_bank_assignment() -> None:
    cart = _cart()
    assert cart._banks == [0, 1, 2, 3]
    assert cart._mode_register == 0x00
    assert cart._scc_window_base is None


# ---------------------------------------------------------------------------
# Out-of-range addresses
# ---------------------------------------------------------------------------

def test_read_outside_window_returns_0xff() -> None:
    cart = _cart()
    assert cart.read(0x0000) == 0xFF
    assert cart.read(0xC000) == 0xFF


def test_write_outside_window_is_ignored() -> None:
    cart = _cart()
    cart.write(0x0000, 0x42)  # must not raise
    cart.write(0xC000, 0x42)


# ---------------------------------------------------------------------------
# Save-state
# ---------------------------------------------------------------------------

def test_snapshot_restore_round_trip() -> None:
    cart = _cart()
    cart.write(0xBFFE, 0x01)  # window 0 RAM-write
    cart.write(0x4000, 0x99)
    cart.write(0x7000, 2)  # window 1 -> block 2
    cart.write(0xBFFE, 0x20)  # Plus mode, no RAM-write regions
    cart.write(0xB000, 0x80)  # window 3 enable bit -> SCC+ window active

    snap = cart.snapshot()

    fresh = SCCICart(scc=SCC(is_052539=True))
    fresh.restore(snap)

    assert bytes(fresh.ram) == bytes(cart.ram)
    assert fresh._banks == cart._banks
    assert fresh._mode_register == cart._mode_register
    assert fresh._scc_window_base == cart._scc_window_base == 0xB800
    assert fresh.scc._plus_mode is True


def test_snapshot_on_blank_cart_round_trips() -> None:
    cart = _cart()
    snap = cart.snapshot()
    fresh = SCCICart(scc=SCC(is_052539=True))
    fresh.restore(snap)
    assert bytes(fresh.ram) == bytes(cart.ram)
    assert fresh._banks == cart._banks
    assert fresh._scc_window_base is None
