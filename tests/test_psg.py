from msx.psg import PSG


def test_registers_init_zero() -> None:
    psg = PSG()
    assert psg.regs == [0] * 16


def test_address_latch() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    assert psg.latch == 7


def test_register_write() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    psg.write_port(0xA1, 0x38)
    assert psg.regs[7] == 0x38


def test_register_read() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x07)
    psg.write_port(0xA1, 0x38)
    assert psg.read_port(0xA2) == 0x38


def test_latch_masked_to_4_bits() -> None:
    psg = PSG()
    psg.write_port(0xA0, 0x1F)  # 0x1F & 0x0F = 0x0F = 15
    assert psg.latch == 15


def test_unmapped_read_returns_ff() -> None:
    psg = PSG()
    assert psg.read_port(0xA0) == 0xFF


def test_sequential_register_writes() -> None:
    psg = PSG()
    for i in range(16):
        psg.write_port(0xA0, i)
        psg.write_port(0xA1, i * 2)
    for i in range(16):
        psg.write_port(0xA0, i)
        assert psg.read_port(0xA2) == i * 2
