"""Tests for the per-kiosk sign-in rate limit."""

from roost.auth.rate_limit import AttemptLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_attempts_beyond_the_limit_are_refused_until_the_window_moves() -> None:
    clock = Clock()
    limiter = AttemptLimiter(max_attempts=3, window_seconds=60, clock=clock)

    assert [limiter.allow("kiosk-1") for _ in range(4)] == [True, True, True, False]
    assert limiter.retry_after("kiosk-1") == 60

    clock.now += 30
    assert not limiter.allow("kiosk-1")  # a refused attempt does not extend the wait
    assert limiter.retry_after("kiosk-1") == 30

    clock.now += 30
    assert limiter.allow("kiosk-1")


def test_each_kiosk_has_its_own_count() -> None:
    limiter = AttemptLimiter(max_attempts=1, window_seconds=60, clock=Clock())

    assert limiter.allow("kiosk-1")
    assert limiter.allow("kiosk-2")
    assert not limiter.allow("kiosk-1")
    assert limiter.retry_after("kiosk-3") == 0
