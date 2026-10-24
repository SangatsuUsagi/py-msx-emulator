"""MapperKind is the closed, port-friendly identifier persisted in save-state
in place of a mapper's Python class name (see msx/mapper.py's MapperKind
docstring and openspec/changes/mapper-state-tagged-union). This test only
checks the class-level `kind` wiring is consistent -- it does not exercise
save/load behavior (see tests/test_state.py for that).
"""
from msx.fmpac import FmPac
from msx.mapper import (
    Ascii8Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    Ascii16Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    BankTracingMapper,
    FixedPageMapper,
    FlatMapper,
    GameMaster2Mapper,
    HalnoteMapper,
    KoeiSRAM32Mapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    Mapper,
    MapperKind,
    RTypeMapper,
    SCCICart,
    mapper_kind_display_name,
)
from msx.opll import Opll
from msx.ram_mapper import RamMapper
from msx.sram import SramImage

# Every _ALL_MAPPER_CLASSES member that subclasses BankTracingMapper (directly
# or via an intermediate class/mixin). FlatMapper and FixedPageMapper subclass
# _NoStateMapperMixin instead -- a different no-op, not BankTracingMapper's --
# and RamMapper/FmPac are unrelated device classes that merely structurally
# satisfy the Mapper protocol, so none of those three are in scope here.
_BANK_TRACING_MAPPER_SUBCLASSES: tuple[type[Mapper], ...] = (
    Ascii8Mapper,
    Ascii16Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    KoeiSRAM32Mapper,
    GameMaster2Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    RTypeMapper,
    KonamiMapper,
    MajutsushiMapper,
    KonamiSCCMapper,
    SCCICart,
    HalnoteMapper,
)

# Every concrete Mapper(Protocol)-conforming class, including FmPac (which
# structurally satisfies Mapper via machine.memory._mapper2, see
# msx/machine_loader.py, even though it is not one of the ROM-mapper-family
# classes msx/state.py's mapper_kind identity check applies to).
_ALL_MAPPER_CLASSES: tuple[type[Mapper], ...] = (
    FlatMapper,
    FixedPageMapper,
    Ascii8Mapper,
    Ascii16Mapper,
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    KoeiSRAM32Mapper,
    GameMaster2Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    RTypeMapper,
    KonamiMapper,
    MajutsushiMapper,
    KonamiSCCMapper,
    SCCICart,
    HalnoteMapper,
    FmPac,
    RamMapper,
)


def test_every_mapper_class_has_a_kind() -> None:
    for cls in _ALL_MAPPER_CLASSES:
        assert isinstance(cls.kind, MapperKind), f"{cls.__name__} has no MapperKind"


def test_no_two_mapper_classes_share_a_kind() -> None:
    kinds = [cls.kind for cls in _ALL_MAPPER_CLASSES]
    assert len(kinds) == len(set(kinds)), "two or more mapper classes share a MapperKind"


def test_every_mapperkind_member_is_used_by_exactly_one_class() -> None:
    kinds = [cls.kind for cls in _ALL_MAPPER_CLASSES]
    assert set(kinds) == set(MapperKind), (
        "MapperKind has members with no owning class, or a class uses a "
        "MapperKind member not covered by this test's class list"
    )


def test_subclass_inherits_kind_unless_overridden() -> None:
    # Sanity check for the SRAM/DAC subclasses that don't redeclare `kind`
    # at their own class body but DO override their parent's default --
    # confirms the ClassVar reassignment pattern (not dataclass field
    # inheritance) actually took effect for each.
    assert Ascii8Sram2Mapper.kind == MapperKind.ASCII8_SRAM2
    assert Ascii8Sram8Mapper.kind == MapperKind.ASCII8_SRAM8
    assert KoeiSRAM32Mapper.kind == MapperKind.KOEI_SRAM32
    assert Ascii16Sram2Mapper.kind == MapperKind.ASCII16_SRAM2
    assert Ascii16Sram8Mapper.kind == MapperKind.ASCII16_SRAM8
    assert MajutsushiMapper.kind == MapperKind.MAJUTSUSHI


def test_ram_mapper_kind_and_display_name() -> None:
    assert RamMapper.kind == MapperKind.RAM_MAPPER
    assert mapper_kind_display_name(MapperKind.RAM_MAPPER) == "RamMapper"


# Classes whose sram_medium() returns an SRAM image the host persists (see
# cart-sram-mapper's "SRAM image via sram_medium", cart-halnote's and
# cart-fmpac's own SRAM-persistence Requirements). Every other class in
# _ALL_MAPPER_CLASSES returns None.
_SRAM_CARRYING_CLASSES: frozenset[type[Mapper]] = frozenset({
    Ascii8Sram2Mapper,
    Ascii8Sram8Mapper,
    KoeiSRAM32Mapper,
    GameMaster2Mapper,
    Ascii16Sram2Mapper,
    Ascii16Sram8Mapper,
    HalnoteMapper,
    FmPac,
})


_NO_SRAM_DEFAULTS = (BankTracingMapper.sram_medium, FlatMapper.sram_medium,
                     RamMapper.sram_medium)


def test_sram_medium_is_overridden_exactly_by_the_sram_carrying_set() -> None:
    # cart-sram-mapper "has_sram is True for every SRAM-carrying mapper class"
    # (class level): an SRAM class must not fall back to a None default.
    for cls in _ALL_MAPPER_CLASSES:
        overridden = cls.sram_medium not in _NO_SRAM_DEFAULTS
        assert overridden is (cls in _SRAM_CARRYING_CLASSES), cls.__name__


def test_sram_carrying_instances_return_an_image_of_their_size() -> None:
    # cart-sram-mapper "has_sram is True for every SRAM-carrying mapper
    # class", cart-fmpac / cart-halnote "has_sram is True".
    rom = bytes(range(256)) * 128
    cases: list[tuple[Mapper, int]] = [
        (Ascii8Sram2Mapper(rom=rom), 2048),
        (Ascii8Sram8Mapper(rom=rom), 8192),
        (KoeiSRAM32Mapper(rom=rom), 32768),
        (GameMaster2Mapper(rom=rom), 8192),
        (Ascii16Sram2Mapper(rom=rom), 2048),
        (Ascii16Sram8Mapper(rom=rom), 8192),
        (HalnoteMapper(rom=bytes(1048576)), 16384),
        (FmPac(rom=bytes(65536), opll=Opll()), 8192),
    ]
    for mapper, size in cases:
        image = mapper.sram_medium()
        assert isinstance(image, SramImage), type(mapper).__name__
        assert len(image) == size, type(mapper).__name__
        assert mapper.sram_medium() is image  # the same image for its lifetime


def test_reset_does_not_raise_on_a_mapper_with_no_bank_state() -> None:
    # FlatMapper has no bank registers at all, so the Mapper protocol's
    # reset() default (the shared no-op) is genuinely correct for it --
    # confirm it is callable and inert.
    flat = FlatMapper(cartridge=None)
    flat.reset()


def test_reset_restores_ascii8_bank_registers_to_power_on_values() -> None:
    # Every bank-switching mapper now restores its bank registers on
    # reset() -- an explicit emulation-policy choice (this project treats
    # the slot reset signal as reaching every cartridge mapper's own reset
    # input; see openspec/changes/wire-cartridge-mapper-and-ppi-reset and
    # cart-rom-mapper's "Bank-switching mapper reset restores power-on
    # state"), reversing this project's earlier no-op position.
    ascii8 = Ascii8Mapper(rom=bytes(range(256)) * 128)
    ascii8.write(0x6000, 3)
    assert ascii8._banks != [0, 0, 0, 0]
    ascii8.reset()
    assert ascii8._banks == [0, 0, 0, 0]


def test_bank_tracing_mapper_subclass_list_covers_every_other_mapper_class() -> None:
    # Keeps _BANK_TRACING_MAPPER_SUBCLASSES honest as new mapper classes are
    # added: every _ALL_MAPPER_CLASSES member is either in that list, or is
    # one of the three known exceptions (see its own comment above).
    other = {FlatMapper, FixedPageMapper, RamMapper, FmPac}
    assert set(_ALL_MAPPER_CLASSES) == set(_BANK_TRACING_MAPPER_SUBCLASSES) | other


def test_every_banktracingmapper_subclass_overrides_reset() -> None:
    # BankTracingMapper.reset() is a no-op fallback for a subclass with
    # genuinely no reset-affected state -- but every current subclass does
    # carry bank-register (or other power-on) state, so none may silently
    # fall back to it. A class that forgets to override reset() when adding
    # bank-register state would otherwise fail *silently* (reset() simply
    # does nothing): this closes that gap at test time, standing in for the
    # compile-time check a non-defaulted trait method would give in a future
    # Rust/C++ port.
    for cls in _BANK_TRACING_MAPPER_SUBCLASSES:
        assert cls.reset is not BankTracingMapper.reset, (
            f"{cls.__name__} inherits BankTracingMapper's no-op reset() unchanged"
        )


def test_a_mapper_without_sram_has_no_medium() -> None:
    # cart-rom-mapper "A mapper with no SRAM has has_sram False and a no-op
    # sram_data", memory-ram-mapper "RamMapper declares no SRAM capability".
    for mapper in (FlatMapper(cartridge=None), Ascii8Mapper(rom=bytes(range(256)) * 128),
                   RamMapper()):
        assert mapper.sram_medium() is None


def test_every_mapper_class_exposes_sram_medium() -> None:
    # cart-rom-mapper "Every concrete Mapper implementer satisfies the
    # widened protocol".
    for cls in _ALL_MAPPER_CLASSES:
        assert callable(getattr(cls, "sram_medium", None)), cls.__name__
