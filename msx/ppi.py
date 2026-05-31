from __future__ import annotations

from dataclasses import dataclass

from msx.memory import Memory


@dataclass
class PPI:
    memory: Memory

    def write_port(self, port: int, value: int) -> None:
        value = value & 0xFF
        if port == 0xA8:
            self.memory.slot_register = value

    def read_port(self, port: int) -> int:
        if port == 0xA8:
            return self.memory.slot_register & 0xFF
        if port == 0xA9:
            return 0xFF  # all keys released
        # 0xAA, 0xAB: unused
        return 0xFF
