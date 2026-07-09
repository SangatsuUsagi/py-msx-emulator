from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol

if TYPE_CHECKING:
    from msx.mapper_tracer import MapperTracer
    from msx.scc import SCC

_PAGE_8K = 8192
_PAGE_16K = 16384

# R-Type (Irem) bank register masks (openMSX RomRType).
_RTYPE_HI_BIT = 0x10   # when set, only the low 3 bits of the mask apply
_RTYPE_MASK_HI = 0x17
_RTYPE_MASK = 0x1F


class Mapper(Protocol):
    def read(self, addr: int) -> int: ...
    def write(self, addr: int, value: int) -> None: ...
    def snapshot(self) -> dict[str, object]: ...
    def restore(self, state: dict[str, object]) -> None: ...


@dataclass
class _BankTracing:
    """Shared bank-change tracing state for the bank-switching mappers.

    Consolidated into one base so every mapper carries the same four hook
    fields instead of redeclaring them, and so _trace_bank can access them
    with static typing rather than getattr. The loader injects the callbacks
    after construction (all init=False).
    """

    _tracer: "MapperTracer | None" = field(default=None, init=False, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_cycle: Callable[[], int] | None = field(default=None, init=False, repr=False)
    _get_frame: Callable[[], int] | None = field(default=None, init=False, repr=False)


def _trace_bank(mapper: _BankTracing, window: int, old: int, new: int, addr: int) -> None:
    """Notify an injected MapperTracer of a bank change. No-op without a tracer."""
    tracer = mapper._tracer
    if tracer is None or old == new:
        return
    get_pc = mapper._get_pc
    get_cycle = mapper._get_cycle
    get_frame = mapper._get_frame
    tracer.bank_change(
        window, old, new, addr,
        get_pc() if get_pc else 0,
        get_cycle() if get_cycle else 0,
        get_frame() if get_frame else 0,
    )


@dataclass
class FlatMapper:
    """Flat (non-bank-switching) cartridge mapper. Reproduces the original behaviour."""

    cartridge: bytes | None

    def read(self, addr: int) -> int:
        if not self.cartridge:
            return 0xFF
        # Mirror the ROM across the full cartridge region (e.g., 8 KB ROM repeats in 32 KB space).
        offset = (addr - 0x4000) % len(self.cartridge)
        return self.cartridge[offset]

    def write(self, addr: int, value: int) -> None:
        pass

    def snapshot(self) -> dict[str, object]:
        return {}

    def restore(self, state: dict[str, object]) -> None:
        pass


@dataclass
class Ascii8Mapper(_BankTracing):
    """ASCII8 mapper: four 8 KB windows at 0x4000/0x6000/0x8000/0xA000.

    Control registers written to 0x6000–0x7FFF select which ROM page each
    window shows.  The 2 KB sub-ranges (determined by bits 12:11 of the write
    address) map to windows 0–3 respectively.

    Power-on state: all four windows select bank 0 (matches real ASCII8 hardware
    and openMSX, which reset all segment registers to 0). Same rationale as
    Ascii16Mapper: a cartridge INIT may rely on the upper windows mirroring
    bank 0 before it switches banks itself.
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0, 0, 0], repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def read(self, addr: int) -> int:
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            # Bits 12:11 of address select register 0–3
            reg = (addr >> 11) & 0x03
            new = value % self._num_pages()
            old = self._banks[reg]
            self._banks[reg] = new
            _trace_bank(self, reg, old, new, addr)

    def snapshot(self) -> dict[str, object]:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        self._banks[:] = state["banks"]  # type: ignore[call-overload]


@dataclass
class Ascii16Mapper(_BankTracing):
    """ASCII16 mapper: two 16 KB windows at 0x4000 and 0x8000.

    Control registers at 0x6000–0x6FFF (window 0) and 0x7000–0x7FFF (window 1).

    Power-on state: both windows select bank 0 (matches real ASCII16 hardware
    and openMSX, which reset all segment registers to 0). Some games rely on the
    0x8000 window mirroring bank 0 at startup — e.g. their cartridge INIT does
    `JP 8031h` into bank-0 code visible through the second window.
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 0], repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_16K)

    def read(self, addr: int) -> int:
        if addr < 0x8000:
            window, base = 0, 0x4000
        else:
            window, base = 1, 0x8000
        page_offset = self._banks[window] * _PAGE_16K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            # Bit 12 selects window 0 (0x6xxx) or window 1 (0x7xxx)
            window = (addr >> 12) & 0x01
            new = value % self._num_pages()
            old = self._banks[window]
            self._banks[window] = new
            _trace_bank(self, window, old, new, addr)

    def snapshot(self) -> dict[str, object]:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        self._banks[:] = state["banks"]  # type: ignore[call-overload]


@dataclass
class Ascii8Sram2Mapper(Ascii8Mapper):
    """ASCII8 mapper + 2 KB battery-backed SRAM (generic ASCII8-SRAM).

    Following openMSX RomAscii8_8: a window maps SRAM when its bank register
    value has the SRAM-enable bit set, where the enable bit equals the ROM's
    8 KB page count (rom_size // 8192). SRAM is only selectable for windows in
    _SRAM_PAGES (default 0x8000 and 0xA000; the region bit for window w is
    1 << (w + 2)). The SRAM-page-select bits are masked with
    round_up(sram_size / 8192) - 1. Writes to 0x6000–0x7FFF always update bank
    registers (raw value, never SRAM).

    KOEI and Wizardry variants (different enable bit / SRAM windows) are out of
    scope here and are not covered by this generic mapper.
    """

    _SRAM_SIZE: ClassVar[int] = 2048
    _SRAM_MASK: ClassVar[int] = 0x7FF
    # Region bitmask of windows that may map SRAM: 0x8000 (1<<4) and 0xA000 (1<<5).
    _SRAM_PAGES: ClassVar[int] = 0x30

    # Portability note: `sram` is Optional and every access is guarded / masked
    # with a `# type: ignore`. A Rust/C++ port makes SRAM non-optional (allocate
    # a fixed `[u8; _SRAM_SIZE]` in the constructor/factory) so the read/write
    # paths need no None-check and the type is `&[u8]`, not `Option<&[u8]>`.
    sram: bytearray | None = None
    # Constant SRAM/ROM geometry, cached for the hot read() path — these depend
    # only on len(rom) and the class-constant _SRAM_SIZE, fixed after construction.
    _c_enable_bit: int = field(default=0, init=False, repr=False)
    _c_block_mask: int = field(default=0, init=False, repr=False)
    _c_rom_len: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.sram, bytearray) or len(self.sram) != self._SRAM_SIZE:
            self.sram = bytearray(self._SRAM_SIZE)
        self._c_enable_bit = self._num_pages()        # == _sram_enable_bit()
        self._c_block_mask = self._sram_block_mask()
        self._c_rom_len = len(self.rom)

    def _sram_enable_bit(self) -> int:
        return self._num_pages()

    def _sram_block_mask(self) -> int:
        blocks = max(1, (self._SRAM_SIZE + _PAGE_8K - 1) // _PAGE_8K)
        return blocks - 1

    def _is_sram_bank(self, window: int) -> bool:
        if not (self._SRAM_PAGES & (1 << (window + 2))):
            return False
        return bool(self._banks[window] & self._sram_enable_bit())

    def _sram_offset(self, window: int, addr: int, base: int) -> int:
        block = self._banks[window] & self._sram_block_mask()
        return (block * _PAGE_8K + (addr - base)) & self._SRAM_MASK

    def read(self, addr: int) -> int:
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        # Hot path: SRAM/ROM geometry is cached (see __post_init__); the
        # _is_sram_bank / _sram_offset helpers below hold the un-inlined form
        # used by write() and the tests.
        bank = self._banks[window]
        if (self._SRAM_PAGES & (1 << (window + 2))) and (bank & self._c_enable_bit):
            offset = ((bank & self._c_block_mask) * _PAGE_8K + (addr - base)) & self._SRAM_MASK
            return self.sram[offset]  # type: ignore[index]
        page_offset = bank * _PAGE_8K + (addr - base)
        if 0 <= page_offset < self._c_rom_len:
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            reg = (addr >> 11) & 0x03
            old = self._banks[reg]
            self._banks[reg] = value
            _trace_bank(self, reg, old, value, addr)
            return
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        if self._is_sram_bank(window):
            self.sram[self._sram_offset(window, addr, base)] = value & 0xFF  # type: ignore[index]

    def save_sram(self, path: Path) -> None:
        path.write_bytes(self.sram)  # type: ignore[arg-type]

    def snapshot(self) -> dict[str, object]:
        state = super().snapshot()
        state["sram"] = bytes(self.sram)  # type: ignore[arg-type]
        return state

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        if self.sram is not None:
            self.sram[:] = state["sram"]  # type: ignore[call-overload]


@dataclass
class Ascii8Sram8Mapper(Ascii8Sram2Mapper):
    """ASCII8 mapper + 8 KB battery-backed SRAM."""

    _SRAM_SIZE: ClassVar[int] = 8192
    _SRAM_MASK: ClassVar[int] = 0x1FFF


@dataclass
class Ascii16Sram2Mapper(Ascii16Mapper):
    """ASCII16 mapper + 2 KB battery-backed SRAM (openMSX RomAscii16_2).

    Only window 1 (0x8000–0xBFFF) can be SRAM-mapped. SRAM is selected for
    window 1 when its bank register value equals exactly 0x10 (strict equality;
    any other value selects a ROM page). Writes to 0x6000–0x7FFF always update
    bank registers (raw value).
    """

    _SRAM_SIZE: ClassVar[int] = 2048
    _SRAM_MASK: ClassVar[int] = 0x7FF
    _SRAM_SELECT: ClassVar[int] = 0x10

    sram: bytearray | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sram, bytearray) or len(self.sram) != self._SRAM_SIZE:
            self.sram = bytearray(self._SRAM_SIZE)

    def _is_sram_bank(self, window: int) -> bool:
        return window == 1 and self._banks[window] == self._SRAM_SELECT

    def read(self, addr: int) -> int:
        if addr < 0x8000:
            window, base = 0, 0x4000
        else:
            window, base = 1, 0x8000
        if self._is_sram_bank(window):
            return self.sram[(addr - base) & self._SRAM_MASK]  # type: ignore[index]
        page_offset = self._banks[window] * _PAGE_16K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr <= 0x7FFF:
            window = (addr >> 12) & 0x01
            old = self._banks[window]
            self._banks[window] = value
            _trace_bank(self, window, old, value, addr)
        elif addr >= 0x8000:
            if self._is_sram_bank(1):
                self.sram[(addr - 0x8000) & self._SRAM_MASK] = value & 0xFF  # type: ignore[index]

    def save_sram(self, path: Path) -> None:
        path.write_bytes(self.sram)  # type: ignore[arg-type]

    def snapshot(self) -> dict[str, object]:
        state = super().snapshot()
        state["sram"] = bytes(self.sram)  # type: ignore[arg-type]
        return state

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        if self.sram is not None:
            self.sram[:] = state["sram"]  # type: ignore[call-overload]


@dataclass
class Ascii16Sram8Mapper(Ascii16Sram2Mapper):
    """ASCII16 mapper + 8 KB battery-backed SRAM."""

    _SRAM_SIZE: ClassVar[int] = 8192
    _SRAM_MASK: ClassVar[int] = 0x1FFF


@dataclass
class RTypeMapper(_BankTracing):
    """R-Type (Irem) mapper: 16 KB fixed at 0x4000 (last page), 16 KB switchable at 0x8000.

    The last 16 KB of ROM is always mapped at 0x4000–0x7FFF.
    The switchable window at 0x8000–0xBFFF starts at page 0.
    Bank register: write anywhere to 0x4000–0x7FFF.
    Bank mask: value & _RTYPE_MASK_HI when bit 4 set, else value & _RTYPE_MASK
    (openMSX RomRType).
    """

    rom: bytes
    _bank: int = field(default=0, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_16K)

    def read(self, addr: int) -> int:
        if 0x4000 <= addr < 0x8000:
            fixed = (self._num_pages() - 1) * _PAGE_16K + (addr - 0x4000)
            if 0 <= fixed < len(self.rom):
                return self.rom[fixed]
            return 0xFF
        if 0x8000 <= addr < 0xC000:
            offset = self._bank * _PAGE_16K + (addr - 0x8000)
            if 0 <= offset < len(self.rom):
                return self.rom[offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x4000 <= addr < 0x8000:
            if value & _RTYPE_HI_BIT:
                value = value & _RTYPE_MASK_HI
            else:
                value = value & _RTYPE_MASK
            old = self._bank
            self._bank = value
            _trace_bank(self, 1, old, self._bank, addr)

    def snapshot(self) -> dict[str, object]:
        return {"bank": self._bank}

    def restore(self, state: dict[str, object]) -> None:
        self._bank = int(state["bank"])  # type: ignore[call-overload]


@dataclass
class KonamiMapper(_BankTracing):
    """Konami (Konami4) mapper: four 8 KB windows.

    Window 0 (0x4000–0x5FFF) is permanently fixed to page 0.
    Windows 1–3 are switched by writing the page index to an address
    within the window itself (0x6000–0x7FFF, 0x8000–0x9FFF, 0xA000–0xBFFF).
    """

    rom: bytes
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)

    def _bank_mask(self) -> int:
        # Konami4 hardware: 5-bit bank register → 32 pages (256 KB) max.
        # Use power-of-2 bitmask capped at 31, matching OpenMSX setBlockMask(31).
        # Avoids modulo aliasing when ROM > 256 KB (upper half is padding/inaccessible).
        pages = min(max(1, len(self.rom) // _PAGE_8K), 32)
        m = 1
        while m < pages:
            m <<= 1
        return m - 1

    def read(self, addr: int) -> int:
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        if 0x6000 <= addr < 0x8000:
            window = 1
        elif 0x8000 <= addr < 0xA000:
            window = 2
        elif 0xA000 <= addr < 0xC000:
            window = 3
        else:
            # Writes to 0x4000–0x5FFF are ignored; window 0 is fixed to page 0.
            return
        new = value & self._bank_mask()
        old = self._banks[window]
        self._banks[window] = new
        _trace_bank(self, window, old, new, addr)

    def snapshot(self) -> dict[str, object]:
        return {"banks": list(self._banks)}

    def restore(self, state: dict[str, object]) -> None:
        self._banks[:] = state["banks"]  # type: ignore[call-overload]


@dataclass
class MajutsushiMapper(KonamiMapper):
    """Konami mapper + DAC for Hai no Majutsushi.

    Writes to 0x5000–0x5FFF are routed to the DAC (8-bit unsigned PCM).
    All other behaviour is identical to KonamiMapper.

    DAC writes are timestamped via _get_cycle callback so generate_samples()
    can reproduce sub-frame timing (same role as openMSX's BlipBuffer delta).
    """

    _last_dac: int = field(default=0x80, init=False, repr=False)
    _dac_events: list[tuple[int, int]] = field(default_factory=list, init=False, repr=False)

    def write(self, addr: int, value: int) -> None:
        if 0x5000 <= addr < 0x6000:
            cycle = self._get_cycle() if self._get_cycle else 0
            self._dac_events.append((cycle, value & 0xFF))
        else:
            super().write(addr, value)

    def generate_samples(self, n: int, frame_start: int = 0, frame_end: int = 0) -> bytearray:
        """Return n signed 16-bit LE mono PCM samples from this frame's DAC events.

        frame_start / frame_end: machine.cycle_count at the frame boundaries.
        DAC events recorded during the frame are mapped to sample positions
        proportionally, matching openMSX DACSound8U delta-at-time behaviour.
        Conversion: (uint8 value - 0x80) * 256 → int16.
        """
        events = self._dac_events
        self._dac_events = []

        cycles = frame_end - frame_start if frame_end > frame_start else 1
        out = bytearray(n * 2)
        value = self._last_dac
        ev_idx = 0

        for i in range(n):
            threshold = frame_start + i * cycles // n
            while ev_idx < len(events) and events[ev_idx][0] <= threshold:
                value = events[ev_idx][1]
                ev_idx += 1
            sample = (value - 0x80) * 256
            out[i * 2] = sample & 0xFF
            out[i * 2 + 1] = (sample >> 8) & 0xFF

        if ev_idx < len(events):
            value = events[-1][1]
        self._last_dac = value
        return out

    def snapshot(self) -> dict[str, object]:
        # _dac_events is transient (consumed each frame), so only last_dac persists.
        state = super().snapshot()
        state["last_dac"] = self._last_dac
        return state

    def restore(self, state: dict[str, object]) -> None:
        super().restore(state)
        self._last_dac = int(state["last_dac"])  # type: ignore[call-overload]


@dataclass
class KonamiSCCMapper(_BankTracing):
    """Konami SCC mapper: same 8 KB bank switching as KonamiMapper, extended
    with SCC mode.

    When the window-2 bank register value has its low 6 bits all set
    ((value & 0x3F) == 0x3F), the address range 0x9800–0x9FFF is redirected to
    SCC registers instead of ROM.

    All four windows are switchable. Each bank register occupies only the
    low 2 KB of its window's register zone:
        bank 0 (0x4000): 0x5000–0x57FF
        bank 1 (0x6000): 0x7000–0x77FF
        bank 2 (0x8000): 0x9000–0x97FF  (0x3F enables SCC)
        bank 3 (0xA000): 0xB000–0xB7FF
    Decoding the whole window would wrongly treat ordinary writes (e.g. a
    BIOS RAM test hitting 0xBF00) as bank switches.
    """

    rom: bytes
    scc: "SCC"
    _banks: list[int] = field(default_factory=lambda: [0, 1, 2, 3], repr=False)
    _scc_mode: bool = field(default=False, init=False, repr=False)

    def _num_pages(self) -> int:
        return max(1, len(self.rom) // _PAGE_8K)

    def read(self, addr: int) -> int:
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            return self.scc.read(addr - 0x9800)
        if addr < 0x6000:
            window, base = 0, 0x4000
        elif addr < 0x8000:
            window, base = 1, 0x6000
        elif addr < 0xA000:
            window, base = 2, 0x8000
        else:
            window, base = 3, 0xA000
        page_offset = self._banks[window] * _PAGE_8K + (addr - base)
        if 0 <= page_offset < len(self.rom):
            return self.rom[page_offset]
        return 0xFF

    def write(self, addr: int, value: int) -> None:
        # SCC register writes take priority over bank-register writes.
        if self._scc_mode and 0x9800 <= addr <= 0x9FFF:
            self.scc.write(addr - 0x9800, value)
            return
        if 0x5000 <= addr < 0x5800:
            new = value % self._num_pages()
            old = self._banks[0]
            self._banks[0] = new
            _trace_bank(self, 0, old, new, addr)
        elif 0x7000 <= addr < 0x7800:
            new = value % self._num_pages()
            old = self._banks[1]
            self._banks[1] = new
            _trace_bank(self, 1, old, new, addr)
        elif 0x9000 <= addr < 0x9800:
            # Window 2 bank register: low 6 bits all set enables SCC mode
            # (upper 2 bits are don't-care); any other value disables it.
            if (value & 0x3F) == 0x3F:
                self._scc_mode = True
            else:
                self._scc_mode = False
                new = value % self._num_pages()
                old = self._banks[2]
                self._banks[2] = new
                _trace_bank(self, 2, old, new, addr)
        elif 0xB000 <= addr < 0xB800:
            new = value % self._num_pages()
            old = self._banks[3]
            self._banks[3] = new
            _trace_bank(self, 3, old, new, addr)
        # Writes outside the four register zones are ignored.

    def snapshot(self) -> dict[str, object]:
        # SCC chip state is snapshotted separately by the state module.
        return {"banks": list(self._banks), "scc_mode": self._scc_mode}

    def restore(self, state: dict[str, object]) -> None:
        self._banks[:] = state["banks"]  # type: ignore[call-overload]
        self._scc_mode = bool(state["scc_mode"])
