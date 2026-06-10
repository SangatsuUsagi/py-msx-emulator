from __future__ import annotations

import sys
from dataclasses import dataclass, field
from msx.cpu.z80 import Z80
from msx.debug.logger import DebugLogger
from msx.input import InputState
from msx.io import IOBus
from msx.mapper import Ascii8Mapper, Ascii16Mapper, FlatMapper, KonamiMapper, KonamiSCCMapper, Mapper
from msx.memory import Memory
from msx.ppi import PPI
from msx.psg import PSG
from msx.ram_mapper import RamMapper
import msx.romdb as romdb
from msx.rtc import RTC
from msx.scc import SCC
from msx.vdp.renderer import render_frame
from msx.vdp.v9938 import V9938
from msx.vdp.v9938_renderer import render_frame as render_frame_v9938
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
    psg: PSG
    scc: SCC | None = field(default=None)
    input: InputState = field(default_factory=InputState)
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

    def run_frame(self, skip_render: bool = False) -> bytearray:
        cycles = 0
        cpu_step = self.cpu.step  # bind Z80.step directly — skip Machine.step wrapper
        if self._logger is None:
            while cycles < CYCLES_PER_FRAME:
                cycles += cpu_step()
        else:
            while cycles < CYCLES_PER_FRAME:
                pc = self.cpu.registers.PC
                cycles += cpu_step()
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
            if self.cpu.halted and not self.cpu.iff1:
                self._logger.on_hang_halt_di(self.cpu.registers.PC)

        if isinstance(self.vdp, V9938):
            result = render_frame_v9938(self.vdp, skip_render=skip_render)
            # V9938 render_frame already increments _frame_count
        else:
            result = render_frame(self.vdp, skip_render=skip_render)
            self.vdp._frame_count += 1
        return result


_SUPPORTED_MAPPERS = frozenset({"Mirrored", "Normal", "ASCII8", "ASCII16", "Konami", "KonamiSCC"})


def _resolve_mapper_type(mapper: str, cartridge: bytes | None) -> str:
    """Resolve 'auto' to a concrete mapper type string via the ROM database."""
    if mapper != "auto":
        return mapper
    if cartridge is None:
        return "Mirrored"
    found = romdb.lookup(cartridge)
    if found is None:
        print("warning: cartridge not found in ROM database, using Mirrored mapper",
              file=sys.stderr)
        return "Mirrored"
    if found not in _SUPPORTED_MAPPERS:
        print(f"warning: unsupported mapper type {found!r} from ROM database, "
              "using Mirrored mapper", file=sys.stderr)
        return "Mirrored"
    return found


def _make_mapper(mapper_type: str, cartridge: bytes | None, scc: SCC | None = None) -> Mapper:
    if mapper_type in ("Mirrored", "Normal"):
        return FlatMapper(cartridge)
    rom_bytes = cartridge if cartridge is not None else b""
    if mapper_type == "ASCII8":
        return Ascii8Mapper(rom_bytes)
    if mapper_type == "ASCII16":
        return Ascii16Mapper(rom_bytes)
    if mapper_type == "Konami":
        return KonamiMapper(rom_bytes)
    if mapper_type == "KonamiSCC":
        if scc is None:
            raise ValueError("KonamiSCC mapper requires an SCC instance")
        return KonamiSCCMapper(rom_bytes, scc=scc)
    raise ValueError(f"unknown mapper type: {mapper_type!r}")


def make_machine(
    rom: bytes,
    cartridge: bytes | None = None,
    logger: DebugLogger | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logrom: bytes | None = None,
) -> Machine:
    resolved = _resolve_mapper_type(mapper, cartridge)
    scc: SCC | None = SCC() if resolved == "KonamiSCC" else None

    resolved2 = _resolve_mapper_type(mapper2, cartridge2)
    if resolved2 == "KonamiSCC":
        print("warning: KonamiSCC is not supported for slot 2, using Konami mapper",
              file=sys.stderr)
        resolved2 = "Konami"
    mapper2_instance = _make_mapper(resolved2, cartridge2)

    memory = Memory(
        rom=rom,
        ram=bytearray(32768),
        _mapper=_make_mapper(resolved, cartridge, scc=scc),
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logrom,
    )
    input_state = InputState()
    psg = PSG(_input=input_state)
    ppi = PPI(memory=memory, _input=input_state)
    vdp = VDP(_logger=logger)
    io = IOBus(_logger=logger)
    io.register_read(0x98, 0x99, vdp.read_port)
    io.register_write(0x98, 0x99, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    machine = Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc,
        input=input_state, _logger=logger,
    )
    io._get_pc = lambda: cpu.registers.PC
    return machine


def make_machine_msx2(
    rom: bytes,
    extrom: bytes,
    *,
    logrom: bytes | None = None,
    cartridge: bytes | None = None,
    mapper: str = "auto",
    cartridge2: bytes | None = None,
    mapper2: str = "auto",
    logger: DebugLogger | None = None,
) -> Machine:
    resolved = _resolve_mapper_type(mapper, cartridge)
    scc: SCC | None = SCC() if resolved == "KonamiSCC" else None

    resolved2 = _resolve_mapper_type(mapper2, cartridge2)
    if resolved2 == "KonamiSCC":
        print("warning: KonamiSCC is not supported for slot 2, using Konami mapper",
              file=sys.stderr)
        resolved2 = "Konami"
    mapper2_instance = _make_mapper(resolved2, cartridge2)

    ram_mapper = RamMapper()
    memory = Memory(
        rom=rom,
        ram=bytearray(32768),
        _mapper=_make_mapper(resolved, cartridge, scc=scc),
        _mapper2=mapper2_instance,
        slot_register=0x00,
        _logger=logger,
        extrom=logrom,          # slot 0 / page 2: logo ROM
        sub0_rom=extrom,        # slot 3 / sub-slot 0: cbios_sub.rom (extension BIOS)
        sub_slot_enabled=True,
        ram_mapper=ram_mapper,
    )
    input_state = InputState()
    psg = PSG(_input=input_state)
    ppi = PPI(memory=memory, _input=input_state)
    vdp = V9938()
    rtc = RTC()
    io = IOBus(_logger=logger)
    io.register_read(0x98, 0x9C, vdp.read_port)
    io.register_write(0x98, 0x9C, vdp.write_port)
    io.register_read(0xA0, 0xA2, psg.read_port)
    io.register_write(0xA0, 0xA2, psg.write_port)
    io.register_read(0xA8, 0xAB, ppi.read_port)
    io.register_write(0xA8, 0xAB, ppi.write_port)
    io.register_read(0xB4, 0xB5, rtc.read_port)
    io.register_write(0xB4, 0xB5, rtc.write_port)
    io.register_read(0xFC, 0xFF, ram_mapper.read_port)
    io.register_write(0xFC, 0xFF, ram_mapper.write_port)
    cpu = Z80(read_byte=memory.read, write_byte=memory.write, _logger=logger)
    machine = Machine(
        cpu=cpu, vdp=vdp, memory=memory, io=io, psg=psg, scc=scc,
        input=input_state, _logger=logger,
    )
    io._get_pc = lambda: cpu.registers.PC
    return machine
