import pytest

from aitrader.ratelimit import RateLimiter, retry


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def test_limiter_spaces_calls():
    t = FakeTime()
    limiter = RateLimiter(2, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        limiter.acquire()
    assert t.sleeps == [0.5, 0.5]


def test_retry_retries_transient_then_succeeds():
    t = FakeTime()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("transient")
        return "ok"

    assert retry(flaky, is_transient=lambda e: isinstance(e, TimeoutError), sleep=t.sleep) == "ok"
    assert len(calls) == 3 and t.sleeps == [0.5, 1.0]


def test_retry_does_not_retry_permanent_errors():
    t = FakeTime()
    with pytest.raises(ValueError):
        retry(lambda: (_ for _ in ()).throw(ValueError("bad")), is_transient=lambda e: False, sleep=t.sleep)
    assert t.sleeps == []


def test_retry_gives_up_after_attempts():
    t = FakeTime()
    with pytest.raises(TimeoutError):
        retry(lambda: (_ for _ in ()).throw(TimeoutError()), attempts=3,
              is_transient=lambda e: True, sleep=t.sleep)
    assert t.sleeps == [0.5, 1.0]
