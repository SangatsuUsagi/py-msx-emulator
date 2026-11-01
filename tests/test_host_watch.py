"""host/watch.py: the r/w/rw spelling of a watch kind."""
from __future__ import annotations

import pytest

from host.watch import watch_kind, watch_kind_exact, watch_text
from msx.boundary import WATCH_READ, WATCH_WRITE


@pytest.mark.parametrize("text, kind", [
    ("r", WATCH_READ), ("w", WATCH_WRITE), ("rw", WATCH_READ | WATCH_WRITE),
    ("wr", WATCH_READ | WATCH_WRITE), ("RW", WATCH_READ | WATCH_WRITE), (" w ", WATCH_WRITE),
])
def test_a_mix_of_r_and_w_spells_a_kind(text: str, kind: int) -> None:
    assert watch_kind(text) == kind


@pytest.mark.parametrize("text", ["", " ", "x", "rx", "read"])
def test_other_text_spells_no_kind(text: str) -> None:
    assert watch_kind(text) is None


def test_each_kind_has_one_spelling() -> None:
    assert watch_text(WATCH_READ) == "r"
    assert watch_text(WATCH_WRITE) == "w"
    assert watch_text(WATCH_READ | WATCH_WRITE) == "rw"


@pytest.mark.parametrize("kind", [0, 4, WATCH_READ | 4])
def test_an_invalid_kind_has_no_spelling(kind: int) -> None:
    with pytest.raises(ValueError):
        watch_text(kind)


@pytest.mark.parametrize(("text", "kind"), [
    ("r", WATCH_READ), ("w", WATCH_WRITE), ("rw", WATCH_READ | WATCH_WRITE),
])
def test_the_exact_parser_takes_r_w_and_rw(text: str, kind: int) -> None:
    assert watch_kind_exact(text) == kind


@pytest.mark.parametrize("text", ["R", "wr", "RW", " r", "", 1, None])
def test_the_exact_parser_rejects_any_other_value(text: object) -> None:
    assert watch_kind_exact(text) is None
