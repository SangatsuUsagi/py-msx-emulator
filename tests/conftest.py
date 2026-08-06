import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from msx.cpu.z80 import Z80
from msx.debugger import prompt as debugger_prompt_module
from msx.mapper import FlatMapper
from msx.memory import Memory


@pytest.fixture(autouse=True)
def _isolated_debugger_history_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the debug REPL's history file so tests never touch the real
    user's ~/.msx_dbg_history. Debugger.enter() reads/writes it via readline
    whenever the real stdlib `readline` module is present (e.g. macOS/Linux),
    which every test that calls Debugger.enter() does."""
    monkeypatch.setattr(debugger_prompt_module, "_HISTORY_FILE", tmp_path / ".msx_dbg_history")
    monkeypatch.setattr(debugger_prompt_module, "_readline_ready", False)


def _short_tmp_base() -> str | None:
    """Shortest writable temp base for AF_UNIX sockets.

    A Unix socket path is capped at ~104 bytes (macOS) / ~108 bytes (Linux), but
    pytest's ``tmp_path`` sits under a long base — on macOS ``$TMPDIR`` alone can
    exceed the limit — so binding a socket there raises "AF_UNIX path too long".
    ``/tmp`` is short and present on both macOS and *nix; fall back to the
    platform default (``mkdtemp(dir=None)``) if it is somehow absent.
    """
    return "/tmp" if Path("/tmp").is_dir() else None


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    """A short-path temp directory for AF_UNIX sockets, removed after the test.

    Use ``sock_dir / "name.sock"`` instead of ``tmp_path / "name.sock"`` so the
    socket path stays under the platform ``sun_path`` limit on macOS and *nix.
    """
    path = tempfile.mkdtemp(prefix="msxrpc-", dir=_short_tmp_base())
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def bare_memory() -> Memory:
    return Memory(rom=bytes(32768), ram=bytearray(32768), _mapper=FlatMapper(None))


@pytest.fixture
def bare_cpu(bare_memory: Memory) -> Z80:
    return Z80(
        read_byte=bare_memory.read,
        write_byte=bare_memory.write,
    )
