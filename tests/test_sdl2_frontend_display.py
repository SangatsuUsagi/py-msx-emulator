"""Tests for the SDL2 frontend's display/texture management.

Covers allium/frontend.allium's Part 2 (display) rules: CreateHostDisplay,
ResizeDisplayForFrameWidth, DisplayedSizeCapsWideTextures and
UploadFrameToTexture. Uses a minimal fake `sdl2` module (same technique as
test_sdl2_frontend_hotkeys.py's `_FakeSDL`) so `_resize_texture_if_needed`
and `_upload_to_texture` run against real ctypes plumbing without a real
SDL2 window.
"""
import ctypes

from frontend.sdl2_frontend import (
    _SCREEN_HEIGHT,
    _SCREEN_WIDTH,
    _resize_texture_if_needed,
    _upload_to_texture,
)


# --- allium/frontend.allium: config.output_height / config.base_width -----
# The two constants CreateHostDisplay's rule pins as the fixed starting
# geometry (Part 2's "output_height is already constant across every VDP
# screen mode" design rationale).

def test_screen_dimensions_match_declared_config_defaults() -> None:
    assert _SCREEN_WIDTH == 256   # frontend.allium config.base_width
    assert _SCREEN_HEIGHT == 212  # frontend.allium config.output_height


class _FakeResizeSDL:
    """Just enough of the SDL2 texture/window API for `_resize_texture_if_needed`."""

    SDL_PIXELFORMAT_RGB24 = 1
    SDL_TEXTUREACCESS_STREAMING = 1

    def __init__(self, create_fails: bool = False) -> None:
        self.destroyed: list[object] = []
        self.created_sizes: list[tuple[int, int]] = []
        self.window_sizes: list[tuple[int, int]] = []
        self._next_id = 100
        self._create_fails = create_fails

    def SDL_DestroyTexture(self, texture: object) -> None:
        self.destroyed.append(texture)

    def SDL_CreateTexture(self, _renderer: object, _fmt: int, _access: int, w: int, h: int) -> object:
        self.created_sizes.append((w, h))
        if self._create_fails:
            return None
        self._next_id += 1
        return self._next_id

    def SDL_SetWindowSize(self, _window: object, w: int, h: int) -> None:
        self.window_sizes.append((w, h))

    def SDL_GetError(self) -> bytes:
        return b"fake create-texture failure"


# --- allium/frontend.allium: ResizeDisplayForFrameWidth --------------------

def test_same_width_frame_does_not_recreate_texture() -> None:
    sdl = _FakeResizeSDL()
    index_buf = bytearray(_SCREEN_WIDTH * _SCREEN_HEIGHT)
    texture, tex_w, tex_h, running = _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=_SCREEN_WIDTH, tex_h=_SCREEN_HEIGHT, scale=3,
    )
    assert (tex_w, tex_h) == (_SCREEN_WIDTH, _SCREEN_HEIGHT)
    assert texture == 100          # unchanged: no recreate
    assert running is True
    assert sdl.destroyed == []
    assert sdl.created_sizes == []
    assert sdl.window_sizes == []  # no resize needed


def test_wide_frame_recreates_texture_at_new_width() -> None:
    sdl = _FakeResizeSDL()
    wide_w = 512
    index_buf = bytearray(wide_w * _SCREEN_HEIGHT)
    texture, tex_w, tex_h, running = _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=_SCREEN_WIDTH, tex_h=_SCREEN_HEIGHT, scale=3,
    )
    assert (tex_w, tex_h) == (wide_w, _SCREEN_HEIGHT)  # height never changes
    assert running is True
    assert sdl.destroyed == [100]
    assert sdl.created_sizes == [(wide_w, _SCREEN_HEIGHT)]


def test_narrowing_back_to_base_width_recreates_again() -> None:
    # SCREEN 6/7 -> back to a normal (256-wide) mode: must resize again, not
    # get stuck at the wide texture.
    sdl = _FakeResizeSDL()
    index_buf = bytearray(_SCREEN_WIDTH * _SCREEN_HEIGHT)
    texture, tex_w, tex_h, running = _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=512, tex_h=_SCREEN_HEIGHT, scale=3,
    )
    assert (tex_w, tex_h) == (_SCREEN_WIDTH, _SCREEN_HEIGHT)
    assert sdl.destroyed == [100]
    assert sdl.created_sizes == [(_SCREEN_WIDTH, _SCREEN_HEIGHT)]


def test_texture_create_failure_reports_not_running() -> None:
    sdl = _FakeResizeSDL(create_fails=True)
    index_buf = bytearray(512 * _SCREEN_HEIGHT)
    _texture, _tex_w, _tex_h, running = _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=_SCREEN_WIDTH, tex_h=_SCREEN_HEIGHT, scale=3,
    )
    assert running is False


# --- allium/frontend.allium: DisplayedSizeCapsWideTextures -----------------

def test_wide_texture_window_size_capped_at_base_width_times_scale() -> None:
    # A 512-wide SCREEN 6/7 texture must display at 256*scale, not 512*scale
    # (HostDisplay.displayed_width caps at config.base_width).
    sdl = _FakeResizeSDL()
    index_buf = bytearray(512 * _SCREEN_HEIGHT)
    scale = 3
    _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=_SCREEN_WIDTH, tex_h=_SCREEN_HEIGHT, scale=scale,
    )
    assert sdl.window_sizes == [(_SCREEN_WIDTH * scale, _SCREEN_HEIGHT * scale)]


def test_normal_width_texture_window_size_uses_its_own_width_times_scale() -> None:
    # A base-width (256) texture is not additionally capped -- displayed_width
    # equals texture_width when texture_width <= base_width.
    sdl = _FakeResizeSDL()
    index_buf = bytearray(_SCREEN_WIDTH * _SCREEN_HEIGHT)
    scale = 2
    _resize_texture_if_needed(
        sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=512, tex_h=_SCREEN_HEIGHT, scale=scale,
    )
    assert sdl.window_sizes == [(_SCREEN_WIDTH * scale, _SCREEN_HEIGHT * scale)]


# --- allium/frontend.allium: UploadFrameToTexture ---------------------------

class _FakeUploadSDL:
    """Fake SDL_LockTexture/SDL_UnlockTexture writing into a real ctypes buffer.

    `ctypes.byref(x)` yields a CArgObject whose `._obj` is the original
    ctypes instance -- writable from pure Python without a real C boundary.
    """

    def __init__(self, pitch: int, dest_buffer: ctypes.Array) -> None:
        self._pitch = pitch
        self._dest = dest_buffer
        self.unlocked = False

    def SDL_LockTexture(
        self, _texture: object, _rect: object, pixels_ptr_ref: object, pitch_ref: object
    ) -> int:
        pixels_ptr_ref._obj.value = ctypes.addressof(self._dest)  # type: ignore[attr-defined]
        pitch_ref._obj.value = self._pitch  # type: ignore[attr-defined]
        return 0

    def SDL_UnlockTexture(self, _texture: object) -> None:
        self.unlocked = True


def test_upload_tight_pitch_uses_single_contiguous_copy() -> None:
    tex_w, tex_h = 4, 3
    row_bytes = tex_w * 3
    dest = ctypes.create_string_buffer(row_bytes * tex_h)
    sdl = _FakeUploadSDL(pitch=row_bytes, dest_buffer=dest)
    rgb = bytes(range(row_bytes * tex_h))

    _upload_to_texture(sdl, texture=1, rgb_buf=rgb, tex_w=tex_w, tex_h=tex_h)

    assert dest.raw[: len(rgb)] == rgb
    assert sdl.unlocked is True


def test_upload_padded_pitch_copies_row_by_row() -> None:
    # Driver row padding: destination pitch wider than tex_w * 3.
    tex_w, tex_h = 4, 3
    row_bytes = tex_w * 3
    padded_pitch = row_bytes + 8
    dest = ctypes.create_string_buffer(padded_pitch * tex_h)
    sdl = _FakeUploadSDL(pitch=padded_pitch, dest_buffer=dest)
    rgb = bytes(range(row_bytes * tex_h))

    _upload_to_texture(sdl, texture=1, rgb_buf=rgb, tex_w=tex_w, tex_h=tex_h)

    for row in range(tex_h):
        got = dest.raw[row * padded_pitch: row * padded_pitch + row_bytes]
        want = rgb[row * row_bytes: (row + 1) * row_bytes]
        assert got == want
    # Padding bytes themselves must be untouched (still zero-initialised).
    pad_start = row_bytes
    assert dest.raw[pad_start:pad_start + 8] == b"\x00" * 8
    assert sdl.unlocked is True


def test_resize_then_upload_chain_matches_run_loop_order() -> None:
    # allium/frontend.allium's UploadFrameToTexture requires rgb_width =
    # display.texture_width -- exactly the postcondition
    # ResizeDisplayForFrameWidth establishes. Chain the two calls the same
    # way frontend/sdl2_frontend.py's run() loop does.
    resize_sdl = _FakeResizeSDL()
    wide_w = 512
    index_buf = bytearray(wide_w * _SCREEN_HEIGHT)
    _texture, tex_w, tex_h, running = _resize_texture_if_needed(
        resize_sdl, renderer=1, window=1, texture=100,
        index_buf=index_buf, tex_w=_SCREEN_WIDTH, tex_h=_SCREEN_HEIGHT, scale=3,
    )
    assert running is True
    assert (tex_w, tex_h) == (wide_w, _SCREEN_HEIGHT)

    row_bytes = tex_w * 3
    dest = ctypes.create_string_buffer(row_bytes * tex_h)
    upload_sdl = _FakeUploadSDL(pitch=row_bytes, dest_buffer=dest)
    rgb = bytes((i % 256) for i in range(row_bytes * tex_h))
    _upload_to_texture(upload_sdl, texture=1, rgb_buf=rgb, tex_w=tex_w, tex_h=tex_h)
    assert dest.raw[: len(rgb)] == rgb
