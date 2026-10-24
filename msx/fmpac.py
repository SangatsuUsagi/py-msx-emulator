"""FM-PAC cartridge: banked ROM + YM2413 (OPLL) + 8 KB battery-backed SRAM.

Ground truth: openMSX `MSXFmPac`/`MSXMusicBase`. Window is the primary-slot
0x4000-0x7FFF page pair (bank-switched ROM, magic-unlocked SRAM, and the
memory-mapped OPLL registers all live here); addresses outside the window
read 0xFF and ignore writes.

The SRAM unlock (0x4D at 0x5FFE, 0x69 at 0x5FFF), the 0x4000-0x5FFD SRAM
window, the 0x4D/0x69 readback and re-locking by any other value are also
documented for the Panasonic PAC SRAM interface, which the FM-PAC shares, in
Gigamix Online's PAC SRAM technical memo (saved under references/docs/):
https://gigamix.hatenablog.com/entry/devmsx/pac-sram-access
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, ClassVar, TypedDict, cast

from msx.mapper import MapperKind
from msx.snapshot_schema import SchemaError, check_bytes, check_choice, check_int, check_object
from msx.sram import SramImage

if TYPE_CHECKING:
    from msx.opll import Opll, OpllState

__all__ = ["FmPac", "FmPacState", "SRAM_SIZE"]


class FmPacState(TypedDict):
    """Save-state schema for FmPac.snapshot()/FmPac.restore().

    Nests the carried OPLL's own OpllState, so restoring a FmPacState fully
    reinstates the device's persistent state (SRAM/bank/enable/magic
    registers and the OPLL) in one call, matching the machine-state spec's
    "Mapper snapshot and restore API" requirement.
    """

    sram: bytes
    bank: int
    enable: int
    r1ffe: int
    r1fff: int
    opll: OpllState

_WINDOW_BASE = 0x4000
_WINDOW_SIZE = 0x4000
_BANK_SIZE = 0x4000
SRAM_SIZE = 8192

# Window-relative (address & 0x3FFF) offsets of the fixed registers.
_OFF_MAGIC_LO = 0x1FFE
_OFF_MAGIC_HI = 0x1FFF
_OFF_OPLL_ADDR = 0x3FF4
_OFF_OPLL_DATA = 0x3FF5
_OFF_ENABLE = 0x3FF6
_OFF_BANK = 0x3FF7

_MAGIC_R1FFE = 0x4D
_MAGIC_R1FFF = 0x69

# bit 0 gates the I/O-port (0x7C/0x7D) OPLL access; the FM-PAC's own CALL MUSIC
# sets it only when no other MSX-MUSIC is present (gdx's trace in the MRC
# thread "What is the right way to search for FM-Pak and its RAM").
_ENABLE_IO_OPLL = 0x01
_ENABLE_WRITE_PROTECT = 0x10  # bit 4: freezes the magic registers
_ENABLE_MASK = 0x11
_ENABLE_VALUES = (0x00, 0x01, 0x10, 0x11)  # every value `& _ENABLE_MASK` leaves

# PORT-NOTE: key set read from the TypedDict at import time (see
#   msx/vdp/v9938.py's _STATE_KEYS note: serde `deny_unknown_fields` in Rust,
#   a constexpr key array in C++).
_STATE_KEYS: frozenset[str] = frozenset(FmPacState.__annotations__)


@dataclass
class FmPac:
    """FM-PAC device. Installed as a primary slot's mapper (`read`/`write`)."""

    kind: ClassVar[MapperKind] = MapperKind.FMPAC
    rom: bytes
    opll: "Opll"
    # The initial image only (a loaded save, or omitted for a zero-filled
    # one); __post_init__ copies it into sram_image, blank when the size is wrong.
    sram: InitVar[bytes | bytearray | None] = None
    sram_image: SramImage = field(init=False, repr=False)
    _bank: int = field(default=0, init=False, repr=False)
    _enable: int = field(default=0, init=False, repr=False)
    _r1ffe: int = field(default=0, init=False, repr=False)
    _r1fff: int = field(default=0, init=False, repr=False)
    _sram_enabled: bool = field(default=False, init=False, repr=False)

    def __post_init__(self, sram: bytes | bytearray | None) -> None:
        self.sram_image = SramImage.of(SRAM_SIZE, sram)

    # ------------------------------------------------------------- CPU memory

    def read(self, addr: int) -> int:
        """Memory-mapped read within the 0x4000-0x7FFF window (bank-switched
        ROM, or SRAM/magic registers once unlocked; see the module docstring).
        """
        if not (_WINDOW_BASE <= addr < _WINDOW_BASE + _WINDOW_SIZE):
            return 0xFF
        off = addr - _WINDOW_BASE
        if off == _OFF_ENABLE:
            return self._enable
        if off == _OFF_BANK:
            return self._bank
        if self._sram_enabled:
            if off < _OFF_MAGIC_LO:
                return self.sram_image.read(off)
            if off == _OFF_MAGIC_LO:
                return self._r1ffe
            if off == _OFF_MAGIC_HI:
                return self._r1fff
            # BiFi, quoted in the MRC thread "What is the right way to search
            # for FM-Pak and its RAM" (references/docs/): "When the SRAM is
            # active 6000h - 7FFFh is FFh except for 7FF7h and 7FF6h."
            return 0xFF
        return self.rom[self._bank * _BANK_SIZE + off]

    def write(self, addr: int, value: int) -> None:
        """Memory-mapped write within the 0x4000-0x7FFF window: SRAM magic
        unlock, OPLL register address/data, the enable/bank registers, or
        SRAM data (only while unlocked).
        """
        if not (_WINDOW_BASE <= addr < _WINDOW_BASE + _WINDOW_SIZE):
            return
        off = addr - _WINDOW_BASE
        value &= 0xFF
        if off == _OFF_MAGIC_LO:
            if not (self._enable & _ENABLE_WRITE_PROTECT):
                self._r1ffe = value
                self._check_sram_enable()
            return
        if off == _OFF_MAGIC_HI:
            if not (self._enable & _ENABLE_WRITE_PROTECT):
                self._r1fff = value
                self._check_sram_enable()
            return
        if off == _OFF_OPLL_ADDR:
            self.opll.write_addr(value)
            return
        if off == _OFF_OPLL_DATA:
            self.opll.write_data(value)
            return
        if off == _OFF_ENABLE:
            self._enable = value & _ENABLE_MASK
            if self._enable & _ENABLE_WRITE_PROTECT:
                # Actual value doesn't matter, only that it no longer matches
                # the magic combination (mirrors openMSX MSXFmPac::writeMem).
                self._r1ffe = 0
                self._r1fff = 0
                self._check_sram_enable()
            return
        if off == _OFF_BANK:
            self._bank = value & 0x03
            return
        if self._sram_enabled and off < _OFF_MAGIC_LO:
            self.sram_image.write(off, value)

    def _check_sram_enable(self) -> None:
        self._sram_enabled = self._r1ffe == _MAGIC_R1FFE and self._r1fff == _MAGIC_R1FFF

    # ---------------------------------------------------------------- I/O ports

    def read_port(self, port: int) -> int:
        """I/O-port read; always 0xFF (the OPLL is write-only)."""
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        """I/O-port write, routed to the OPLL's address/data register by
        port parity, but only while the enable register's I/O bit is set.
        """
        if not (self._enable & _ENABLE_IO_OPLL):
            return
        if port & 1:
            self.opll.write_data(value)
        else:
            self.opll.write_addr(value)

    # --------------------------------------------------------------------- reset

    def reset(self) -> None:
        """Power-on reset: resets the carried OPLL and clears bank/enable/
        magic registers (SRAM is left un-paged, per the unlock check).
        """
        self.opll.reset()
        self._bank = 0
        self._enable = 0
        self._r1ffe = 0
        self._r1fff = 0
        self._sram_enabled = False

    # ---------------------------------------------------------------------- SRAM

    def sram_medium(self) -> SramImage | None:
        """The 8 KB battery-backed SRAM (loaded through the `sram=`
        constructor argument)."""
        return self.sram_image

    # ------------------------------------------------------------- save-state
    # PORT-LIBRARY-NOTE: see msx/opll.py's snapshot()/restore() for the
    #   canonical note on this explicit-TypedDict-field save-state boundary
    #   pattern (shared by PSG/SCC/OPLL/FmPac) and its Rust serde /
    #   C++ nlohmann::json crate candidates.

    def snapshot(self) -> FmPacState:
        """Capture device state for save-state (paired with restore),
        including the carried OPLL's own state.
        """
        return {
            "sram": self.sram_image.data(),
            "bank": self._bank,
            "enable": self._enable,
            "r1ffe": self._r1ffe,
            "r1fff": self._r1fff,
            "opll": self.opll.snapshot(),
        }

    def parse_state(self, obj: object, label: str) -> FmPacState:
        """Validate a snapshot() object, including the nested OPLL state,
        and return it as a FmPacState without touching the device. Raises
        SchemaError naming the field path.
        """
        d = check_object(obj, label, _STATE_KEYS)
        bank = check_int(d["bank"], f"{label}.bank", 0, 0x03)
        if (bank + 1) * _BANK_SIZE > len(self.rom):
            raise SchemaError(
                f"{label}.bank {bank} is past the end of the {len(self.rom)}-byte ROM"
            )
        return {
            "sram": bytes(check_bytes(d["sram"], f"{label}.sram", SRAM_SIZE)),
            "bank": bank,
            "enable": check_choice(d["enable"], f"{label}.enable", _ENABLE_VALUES),
            "r1ffe": check_int(d["r1ffe"], f"{label}.r1ffe", 0, 0xFF),
            "r1fff": check_int(d["r1fff"], f"{label}.r1fff", 0, 0xFF),
            "opll": self.opll.parse_state(d["opll"], f"{label}.opll"),
        }

    def restore(self, state: dict[str, object]) -> None:
        """Restore device state produced by snapshot(), including the
        carried OPLL's own state. Parses the whole object first, so a
        rejected state leaves the device and its OPLL unchanged.
        """
        self.apply_state(self.parse_state(state, "state"))

    def apply_state(self, state: Mapping[str, object]) -> None:
        """Assign an already-parsed FmPacState (see parse_state), including
        the carried OPLL's own state. Typed as Mapping to satisfy the Mapper
        Protocol; the value must come from this device's parse_state.
        """
        parsed = cast(FmPacState, state)
        self.sram_image.restore(parsed["sram"])
        self._bank = parsed["bank"]
        self._enable = parsed["enable"]
        self._r1ffe = parsed["r1ffe"]
        self._r1fff = parsed["r1fff"]
        self.opll.apply_state(parsed["opll"])
        self._check_sram_enable()

    def debug_bank_info(self, page: int) -> str | None:
        return None
