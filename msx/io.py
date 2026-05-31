from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class IOBus:
    _read_handlers: list[tuple[int, int, Callable[[int], int]]] = field(
        default_factory=list
    )
    _write_handlers: list[tuple[int, int, Callable[[int, int], None]]] = field(
        default_factory=list
    )

    def register_read(self, start: int, end: int, handler: Callable[[int], int]) -> None:
        self._read_handlers.append((start, end, handler))

    def register_write(
        self, start: int, end: int, handler: Callable[[int, int], None]
    ) -> None:
        self._write_handlers.append((start, end, handler))

    def read_port(self, port: int) -> int:
        for start, end, handler in self._read_handlers:
            if start <= port <= end:
                return handler(port)
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        for start, end, handler in self._write_handlers:
            if start <= port <= end:
                handler(port, value)
                return
