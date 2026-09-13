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
from msx.ram_mapper import RamMapper

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


# Classes that persist SRAM to a standalone .sram file via save_sram() (see
# cart-sram-mapper's "SRAM persistence via save_sram", cart-halnote's and
# cart-fmpac's own SRAM-persistence Requirements). Every other class in
# _ALL_MAPPER_CLASSES has has_sram = False.
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


def test_has_sram_matches_the_known_sram_carrying_set() -> None:
    for cls in _ALL_MAPPER_CLASSES:
        expected = cls in _SRAM_CARRYING_CLASSES
        assert cls.has_sram is expected, (
            f"{cls.__name__}.has_sram is {cls.has_sram}, expected {expected}"
        )


def test_reset_does_not_raise_on_a_mapper_with_no_reset_affected_state() -> None:
    # Machine.reset() does not touch bank-switching ROM mapper registers
    # today (see machine-core's "Machine reset restores power-on state"),
    # so the Mapper protocol's reset() default is a deliberate no-op for
    # these -- confirm it is callable and genuinely inert.
    flat = FlatMapper(cartridge=None)
    flat.reset()

    ascii8 = Ascii8Mapper(rom=bytes(range(256)) * 128)
    ascii8.write(0x6000, 3)
    banks_before = list(ascii8._banks)
    ascii8.reset()
    assert ascii8._banks == banks_before


def test_save_sram_is_a_no_op_when_has_sram_is_false(tmp_path) -> None:
    flat = FlatMapper(cartridge=None)
    assert flat.has_sram is False
    target = tmp_path / "should-not-exist.sram"
    flat.save_sram(target)
    assert not target.exists()
