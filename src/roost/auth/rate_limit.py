"""Limit sign-in attempts per kiosk (FR-40), on top of the per-account lockout.

The lockout stops guessing one employee's PIN; this stops one kiosk from trying one PIN
against many employee numbers. In memory: each server process counts its own attempts,
which is enough for one instance (ADR-0013); several instances would share a store.
"""

import time
from collections import deque
from collections.abc import Callable
from threading import Lock

# Ten tries a minute is far more than a person needs, even with a few typos.
DEFAULT_MAX_ATTEMPTS = 10
DEFAULT_WINDOW_SECONDS = 60.0


class AttemptLimiter:
    """A sliding window: at most `max_attempts` per `window` seconds for each key."""

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Count an attempt for `key`, unless the limit is reached (then it is refused
        and not counted)."""
        now = self._clock()
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - self._window:
                attempts.popleft()
            if len(attempts) >= self._max:
                return False
            attempts.append(now)
            return True

    def retry_after(self, key: str) -> float:
        """Seconds until `key` may try again (0 if it may now)."""
        now = self._clock()
        with self._lock:
            attempts = self._attempts.get(key)
            if not attempts or len(attempts) < self._max:
                return 0.0
            return max(0.0, attempts[0] + self._window - now)
