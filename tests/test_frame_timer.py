import time

from msx.frame_timer import FrameTimer


def test_tick_waits_approximately_one_frame_interval() -> None:
    timer = FrameTimer(fps=60.0, speed=1.0)
    t0 = time.perf_counter()
    timer.tick()
    elapsed = time.perf_counter() - t0
    assert 0.010 < elapsed < 0.030, f"expected ~16ms, got {elapsed*1000:.1f}ms"


def test_tick_returns_elapsed_seconds() -> None:
    timer = FrameTimer(fps=60.0, speed=1.0)
    result = timer.tick()
    assert 0.010 < result < 0.030


def test_slowdown_absorption_no_catchup() -> None:
    timer = FrameTimer(fps=60.0, speed=1.0)
    # Simulate a slow frame by advancing the last tick time artificially
    timer._last_tick_time -= 0.050  # pretend 50ms already passed
    timer._next_deadline = time.perf_counter() - 0.050  # deadline already past
    timer.tick()
    # Next tick should still wait ~16ms, not 0ms (catch-up)
    t1 = time.perf_counter()
    timer.tick()
    next_elapsed = time.perf_counter() - t1
    assert next_elapsed > 0.010, f"expected ~16ms wait, got {next_elapsed*1000:.1f}ms"


def test_fps_measured_starts_at_zero() -> None:
    timer = FrameTimer(fps=60.0, speed=1.0)
    assert timer.fps_measured == 0.0


def test_fps_measured_updates_after_one_second() -> None:
    # Use high fps to make ticks fast; simulate 1+ second by manipulating internal state
    timer = FrameTimer(fps=200.0, speed=1.0)
    timer._fps_last_time -= 1.1  # pretend 1.1s have elapsed
    timer._fps_frame_count = 200
    # Call tick once to trigger measurement
    timer.tick()
    # fps_measured should now be approximately 200 / 1.1 ≈ 181
    assert timer.fps_measured > 0.0


def test_speed_2x_halves_frame_interval() -> None:
    timer = FrameTimer(fps=60.0, speed=2.0)
    assert abs(timer._frame_interval - 1.0 / 120.0) < 1e-9


def test_speed_half_doubles_frame_interval() -> None:
    timer = FrameTimer(fps=60.0, speed=0.5)
    assert abs(timer._frame_interval - 1.0 / 30.0) < 1e-9


def test_tick_at_speed_2x_is_faster() -> None:
    timer = FrameTimer(fps=60.0, speed=2.0)
    t0 = time.perf_counter()
    timer.tick()
    elapsed = time.perf_counter() - t0
    # Should wait ~8.3ms, not 16.7ms
    assert elapsed < 0.015, f"expected <15ms at 2x speed, got {elapsed*1000:.1f}ms"
