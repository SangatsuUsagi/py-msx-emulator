from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.diagnostics.logger import DebugLogger


@dataclass
class IOBus:
    """I/O port dispatcher.

    Handlers are registered with an inclusive [start, end] port range and
    dispatched by linear scan: the first registered handler whose range covers
    the (8-bit) port wins for both reads and writes. Reads log after the handler
    runs (the returned value is known); writes log before (the value is fixed at
    call time). Devices decode only the low 8 bits, so the 16-bit port the Z80
    drives is masked at entry.

    PORT-NOTE: handlers are registered as bound methods (e.g. `psg.read_port`)
      against a `Callable[[int], int]` / `Callable[[int, int], None]` type --
      every registrant in msx/machine_loader.py already conforms to an
      implicit `read_port(self, port: int) -> int` /
      `write_port(self, port: int, value: int) -> None` shape, so the Callable
      signature here already captures that interface precisely; there's no
      untyped/Any surface to tighten further.
    Rust equivalent: a `Vec<(u8, u8, Box<dyn FnMut(u8) -> u8>)>` (closures
      over each device), or a `Vec<(u8, u8, Rc<RefCell<dyn IODevice>>)>` if a
      named `trait IODevice { fn read_port(&mut self, port: u8) -> u8; ... }`
      is preferred for readability -- both are direct translations of this
      linear-scan dispatch.
    C++ equivalent: a `std::vector<std::tuple<uint8_t, uint8_t,
      std::function<uint8_t(uint8_t)>>>`, or a small `IODevice` abstract base
      class with virtual `read_port`/`write_port` if named dispatch is
      preferred over `std::function`.
    Kept as-is here because: dispatched on every CPU IN/OUT instruction (a
      moderately hot path); the linear scan over a handful of registered
      device ranges is already close to the cheapest general dispatch shape
      Python offers for a small, rarely-changing handler list.
    """

    _read_handlers: list[tuple[int, int, Callable[[int], int]]] = field(
        default_factory=list
    )
    _write_handlers: list[tuple[int, int, Callable[[int, int], None]]] = field(
        default_factory=list
    )
    _logger: DebugLogger | None = field(default=None, repr=False)
    # PORT-NOTE: _get_pc is a closure assigned at wiring time, capturing the
    #   owning Z80/Machine.
    # Rust equivalent: a trait object or feature-flagged field resolved once
    #   at construction, not a per-call closure capturing the owner.
    # C++ equivalent: same -- a std::function member or virtual interface
    #   pointer, assigned once at construction.
    # Kept as-is here because: only called inside `if self._logger is not
    #   None:`, so it costs nothing on the always-hot IN/OUT dispatch path in
    #   normal operation -- confirmed by investigation, not deferred pending
    #   benchmarking. Full reasoning (including why msx/psg.py's
    #   PSG._get_cycle -- read unconditionally, not logger-gated -- reached a
    #   different conclusion) in openspec/changes/archive/
    #   *-psg-cycle-source-refactor/design.md, Decision 1.
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)

    def register_read(self, start: int, end: int, handler: Callable[[int], int]) -> None:
        self._read_handlers.append((start, end, handler))

    def register_write(
        self, start: int, end: int, handler: Callable[[int, int], None]
    ) -> None:
        self._write_handlers.append((start, end, handler))

    def read_port(self, port: int) -> int:
        # The Z80 drives a 16-bit port address (high byte = B or A), but MSX
        # devices decode only the low 8 bits. Mask here so handlers and range
        # checks see the 8-bit port regardless of the high byte.
        port &= 0xFF
        for start, end, handler in self._read_handlers:
            if start <= port <= end:
                value = handler(port)
                if self._logger is not None:
                    pc = self._get_pc() if self._get_pc is not None else 0
                    self._logger.on_io_read(port, value, pc)
                return value
        if self._logger is not None:
            pc = self._get_pc() if self._get_pc is not None else 0
            self._logger.on_io_read(port, 0xFF, pc)
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        port &= 0xFF
        if self._logger is not None:
            pc = self._get_pc() if self._get_pc is not None else 0
            self._logger.on_io_write(port, value, pc)
        for start, end, handler in self._write_handlers:
            if start <= port <= end:
                handler(port, value)
                return
