"""SramImage dirty tracking and the Medium protocol (msx/sram.py)."""
from __future__ import annotations

from msx.fdc.disk_image import DskDiskImage
from msx.sram import Medium, SramImage


def test_write_that_changes_a_byte_sets_dirty() -> None:
    image = SramImage.blank(16)
    image.write(3, 0x42)
    assert image.read(3) == 0x42
    assert image.dirty is True


def test_writing_the_stored_value_stays_clean() -> None:
    image = SramImage.of(16, bytes([0x11]) * 16)
    image.write(3, 0x11)
    assert image.dirty is False


def test_restoring_a_differing_image_sets_dirty() -> None:
    image = SramImage.blank(4)
    image.restore(b"\x01\x02\x03\x04")
    assert (image.data(), image.dirty) == (b"\x01\x02\x03\x04", True)


def test_restoring_an_equal_image_stays_clean() -> None:
    image = SramImage.of(4, b"\x01\x02\x03\x04")
    image.restore(b"\x01\x02\x03\x04")
    assert image.dirty is False


def test_of_a_wrong_size_image_starts_blank() -> None:
    assert SramImage.of(8, b"\xFF" * 7).data() == bytes(8)
    assert SramImage.of(8, None).data() == bytes(8)
    assert len(SramImage.of(8, b"\xFF" * 8)) == 8


def test_of_copies_the_image() -> None:
    source = bytearray(b"\x00" * 4)
    image = SramImage.of(4, source)
    source[0] = 0x55
    assert image.read(0) == 0x00


def test_mark_persisted_clears_dirty() -> None:
    image = SramImage.blank(4)
    image.write(0, 1)
    image.mark_persisted()
    assert image.dirty is False


def test_sram_image_and_disk_image_are_media() -> None:
    media: list[Medium] = [SramImage.blank(4), DskDiskImage(bytes(737280))]
    for medium in media:
        assert medium.dirty is False
        assert isinstance(medium.data(), bytes)
