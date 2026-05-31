from dataclasses import dataclass, field


@dataclass
class Registers:
    AF: int = 0xFFFF
    BC: int = 0xFFFF
    DE: int = 0xFFFF
    HL: int = 0xFFFF
    IX: int = 0xFFFF
    IY: int = 0xFFFF
    SP: int = 0xFFFF
    PC: int = 0x0000
    I: int = 0x00
    R: int = 0x00
    AF_: int = 0xFFFF
    BC_: int = 0xFFFF
    DE_: int = 0xFFFF
    HL_: int = 0xFFFF

    def reset(self) -> None:
        self.PC = 0x0000
        self.SP = 0xFFFF
        self.AF = 0xFFFF
        self.BC = 0xFFFF
        self.DE = 0xFFFF
        self.HL = 0xFFFF
        self.IX = 0xFFFF
        self.IY = 0xFFFF
        self.I = 0x00
        self.R = 0x00
        self.AF_ = 0xFFFF
        self.BC_ = 0xFFFF
        self.DE_ = 0xFFFF
        self.HL_ = 0xFFFF

    @property
    def A(self) -> int:
        return (self.AF >> 8) & 0xFF

    @A.setter
    def A(self, v: int) -> None:
        self.AF = ((v & 0xFF) << 8) | (self.AF & 0xFF)

    @property
    def F(self) -> int:
        return self.AF & 0xFF

    @F.setter
    def F(self, v: int) -> None:
        self.AF = (self.AF & 0xFF00) | (v & 0xFF)

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
