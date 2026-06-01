from __future__ import annotations

from dataclasses import dataclass, field

from msx.input import InputState
from msx.memory import Memory


@dataclass
class PPI:
    memory: Memory
    _input: InputState | None = field(default=None, repr=False)
    _row: int = field(default=0, repr=False)

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA8:
            self.memory.slot_register = value
        elif port == 0xAA:
            self._row = value & 0x0F

    def read_port(self, port: int) -> int:
        if port == 0xA8:
            return self.memory.slot_register & 0xFF
        if port == 0xA9:
            if self._input is not None and self._row < len(self._input.matrix):
                return self._input.matrix[self._row]
            return 0xFF
        # 0xAA, 0xAB: unused for read
        return 0xFF
