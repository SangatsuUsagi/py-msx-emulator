from __future__ import annotations

from dataclasses import dataclass

from msx.cpu.z80 import Z80
from msx.io import IOBus
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP

# NTSC: 3.579545 MHz / 60 Hz ≈ 59,659 T-states per frame
CYCLES_PER_FRAME: int = 59_659


@dataclass
class Machine:
    cpu: Z80
    vdp: VDP
    memory: Memory
    io: IOBus

    def __post_init__(self) -> None:
        self.cpu.read_byte = self.memory.read
        self.cpu.write_byte = self.memory.write
        self.cpu.read_port = self.io.read_port
        self.cpu.write_port = self.io.write_port
        self.vdp.on_interrupt = self._vblank_interrupt

    def _vblank_interrupt(self) -> None:
        self.cpu.int_pending = True

    def reset(self) -> None:
        self.cpu.reset()
        self.vdp.status = 0

    def step(self) -> int:
        return self.cpu.step()

    def run_frame(self) -> bytearray:
        cycles = 0
        while cycles < CYCLES_PER_FRAME:
            cycles += self.step()
        return render_frame(self.vdp)


def make_machine(rom: bytes, cartridge: bytes | None = None) -> Machine:
    memory = Memory(
        rom=rom,
        ram=bytearray(16384),
        cartridge=cartridge,
        slot_register=0x00,
    )
    psg = PSG()
    ppi = PPI(memory=memory)
    vdp = VDP()
    io = IOBus()
    io.register_read(0x98, 0x99, vdp.read_port)
    io.register_write(0x98, 0x99, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write)
    return Machine(cpu=cpu, vdp=vdp, memory=memory, io=io)
