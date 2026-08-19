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
    KoeiSRAM32Mapper,
    KonamiMapper,
    KonamiSCCMapper,
    MajutsushiMapper,
    Mapper,
    MapperKind,
    RTypeMapper,
    SCCICart,
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
    FmPac,
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
