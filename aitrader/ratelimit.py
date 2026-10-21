from __future__ import annotations

import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Allows at most `per_second` acquisitions per second, spacing them evenly."""

    def __init__(self, per_second: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next_free = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            start = now if self._next_free is None else max(now, self._next_free)
            if start > now:
                self._sleep(start - now)
            self._next_free = start + self._interval


def retry(fn: Callable[[], T], *, attempts: int = 5, base_delay: float = 0.5,
          is_transient: Callable[[BaseException], bool] = lambda exc: False,
          sleep: Callable[[float], None] = time.sleep) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - classification is delegated to is_transient
            if attempt == attempts or not is_transient(exc):
                raise
            sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")
