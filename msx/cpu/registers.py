from dataclasses import dataclass


@dataclass(slots=True)
class Registers:
    """Z80 register file.

    Width contract (load-bearing for a Rust/C++ port): every field is a bare
    Python ``int`` here, but each has a fixed hardware width and is expected to
    be kept already-masked to that width by whoever writes it —
      - 8-bit (u8): A, F, I, R, and the shadow A_/F_.
      - 16-bit (u16): BC, DE, HL, IX, IY, SP, PC, and the shadow BC_/DE_/HL_.
    Callers mask on write (``& 0xFF`` / ``& 0xFFFF``); readers assume the value
    is already in range. A port picks u8/u16 per field, at which point the
    width is enforced by the type (and arithmetic must use wrapping semantics).
    """

    # 8-bit primary fields — direct access, no property overhead
    A: int = 0xFF
    F: int = 0xFF
    BC: int = 0xFFFF
    DE: int = 0xFFFF
    HL: int = 0xFFFF
    IX: int = 0xFFFF
    IY: int = 0xFFFF
    SP: int = 0xFFFF
    PC: int = 0x0000
    I: int = 0x00  # noqa: E741 - Z80 interrupt vector register (canonical name)
    R: int = 0x00
    # Shadow registers
    A_: int = 0xFF
    F_: int = 0xFF
    BC_: int = 0xFFFF
    DE_: int = 0xFFFF
    HL_: int = 0xFFFF

    def reset(self) -> None:
        self.PC = 0x0000
        self.SP = 0xFFFF
        self.A = 0xFF
        self.F = 0xFF
        self.BC = 0xFFFF
        self.DE = 0xFFFF
        self.HL = 0xFFFF
        self.IX = 0xFFFF
        self.IY = 0xFFFF
        self.I = 0x00
        self.R = 0x00
        self.A_ = 0xFF
        self.F_ = 0xFF
        self.BC_ = 0xFFFF
        self.DE_ = 0xFFFF
        self.HL_ = 0xFFFF

    # AF as a computed 16-bit pair (used by PUSH AF, POP AF, EX AF,AF')
    @property
    def AF(self) -> int:
        return (self.A << 8) | self.F

    @AF.setter
    def AF(self, v: int) -> None:
        self.A = (v >> 8) & 0xFF
        self.F = v & 0xFF

    @property
    def AF_(self) -> int:
        return (self.A_ << 8) | self.F_

    @AF_.setter
    def AF_(self, v: int) -> None:
        self.A_ = (v >> 8) & 0xFF
        self.F_ = v & 0xFF

    # 8-bit halves of BC/DE/HL — properties for register-indexed instructions.
    # Portability note: these @property getter/setter pairs have no direct
    # analogue in Rust/C++ and add descriptor-call overhead. A port stores the
    # 8-bit halves as plain u8 fields (or derives them with inline getter/setter
    # methods) rather than as computed properties over the 16-bit pair.
    @property
    def B(self) -> int:
        return (self.BC >> 8) & 0xFF

    @B.setter
    def B(self, v: int) -> None:
        self.BC = ((v & 0xFF) << 8) | (self.BC & 0xFF)

    @property
    def C(self) -> int:
        return self.BC & 0xFF

    @C.setter
    def C(self, v: int) -> None:
        self.BC = (self.BC & 0xFF00) | (v & 0xFF)

    @property
    def D(self) -> int:
        return (self.DE >> 8) & 0xFF

    @D.setter
    def D(self, v: int) -> None:
        self.DE = ((v & 0xFF) << 8) | (self.DE & 0xFF)

    @property
    def E(self) -> int:
        return self.DE & 0xFF

    @E.setter
    def E(self, v: int) -> None:
        self.DE = (self.DE & 0xFF00) | (v & 0xFF)

    @property
    def H(self) -> int:
        return (self.HL >> 8) & 0xFF

    @H.setter
    def H(self, v: int) -> None:
        self.HL = ((v & 0xFF) << 8) | (self.HL & 0xFF)

    @property
    def L(self) -> int:
        return self.HL & 0xFF

    @L.setter
    def L(self, v: int) -> None:
        self.HL = (self.HL & 0xFF00) | (v & 0xFF)

    @property
    def IXH(self) -> int:
        return (self.IX >> 8) & 0xFF

    @IXH.setter
    def IXH(self, v: int) -> None:
        self.IX = ((v & 0xFF) << 8) | (self.IX & 0xFF)

    @property
    def IXL(self) -> int:
        return self.IX & 0xFF

    @IXL.setter
    def IXL(self, v: int) -> None:
        self.IX = (self.IX & 0xFF00) | (v & 0xFF)

    @property
    def IYH(self) -> int:
        return (self.IY >> 8) & 0xFF

    @IYH.setter
    def IYH(self, v: int) -> None:
        self.IY = ((v & 0xFF) << 8) | (self.IY & 0xFF)

    @property
    def IYL(self) -> int:
        return self.IY & 0xFF

    @IYL.setter
    def IYL(self, v: int) -> None:
        self.IY = (self.IY & 0xFF00) | (v & 0xFF)
