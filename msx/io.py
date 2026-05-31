from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from msx.debug.logger import DebugLogger


@dataclass
class IOBus:
    _read_handlers: list[tuple[int, int, Callable[[int], int]]] = field(
        default_factory=list
    )
    _write_handlers: list[tuple[int, int, Callable[[int, int], None]]] = field(
        default_factory=list
    )
    _logger: DebugLogger | None = field(default=None, repr=False)
    _get_pc: Callable[[], int] | None = field(default=None, repr=False)

    def register_read(self, start: int, end: int, handler: Callable[[int], int]) -> None:
        self._read_handlers.append((start, end, handler))

    def register_write(
        self, start: int, end: int, handler: Callable[[int, int], None]
    ) -> None:
        self._write_handlers.append((start, end, handler))

    def read_port(self, port: int) -> int:
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
        if self._logger is not None:
            pc = self._get_pc() if self._get_pc is not None else 0
            self._logger.on_io_write(port, value, pc)
        for start, end, handler in self._write_handlers:
            if start <= port <= end:
                handler(port, value)
                return
