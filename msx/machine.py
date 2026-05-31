from __future__ import annotations

from dataclasses import dataclass, field
from msx.cpu.z80 import Z80
from msx.debug.logger import DebugLogger
from msx.io import IOBus
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.vdp.renderer import render_frame
from msx.vdp.vdp import VDP

# NTSC: 3.579545 MHz / 60 Hz ≈ 59,659 T-states per frame
CYCLES_PER_FRAME: int = 59_659
HANG_PC_REPEAT_THRESHOLD: int = 1000


@dataclass
class Machine:
    cpu: Z80
    vdp: VDP
    memory: Memory
    io: IOBus
    _logger: DebugLogger | None = field(default=None, repr=False)
    _last_pc: int = field(default=0, init=False, repr=False)
    _pc_repeat: int = field(default=0, init=False, repr=False)

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
            pc = self.cpu.registers.PC
            cycles += self.step()
            if self._logger is not None:
                # PC-loop hang: skip normal HALT (halted + interrupts enabled)
                if not (self.cpu.halted and self.cpu.iff1):
                    if pc == self._last_pc:
                        self._pc_repeat += 1
                        if self._pc_repeat >= HANG_PC_REPEAT_THRESHOLD:
                            self._logger.on_hang_pc_loop(pc)
                    else:
                        self._pc_repeat = 0
                    self._last_pc = pc

        # HALT+DI hang: check once per frame
        if self._logger is not None:
            if self.cpu.halted and not self.cpu.iff1:
                self._logger.on_hang_halt_di(self.cpu.registers.PC)

        result = render_frame(self.vdp)
        self.vdp._frame_count += 1
        return result


def make_machine(
    rom: bytes,
    cartridge: bytes | None = None,
    logger: DebugLogger | None = None,
) -> Machine:
    memory = Memory(
        rom=rom,
        ram=bytearray(16384),
        cartridge=cartridge,
        slot_register=0x00,
        _logger=logger,
    )
    psg = PSG()
    ppi = PPI(memory=memory)
    vdp = VDP(_logger=logger)
    io = IOBus(_logger=logger)
    io.register_read(0x98, 0x99, vdp.read_port)
    io.register_write(0x98, 0x99, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    machine = Machine(cpu=cpu, vdp=vdp, memory=memory, io=io, _logger=logger)
    io._get_pc = lambda: cpu.registers.PC
    return machine
