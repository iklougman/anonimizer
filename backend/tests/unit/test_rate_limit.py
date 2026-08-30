import time

import pytest

from app.api.rate_limit import RateLimitExceededError, check_rate_limit, _reset_for_tests


@pytest.fixture(autouse=True)
def reset_limiter():
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_allows_requests_under_the_limit():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)  # must not raise


def test_blocks_the_request_over_the_limit():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    with pytest.raises(RateLimitExceededError):
        check_rate_limit("1.2.3.4", limit_per_hour=5)


def test_limits_are_independent_per_key():
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    check_rate_limit("5.6.7.8", limit_per_hour=5)  # different IP, must not raise


def test_old_entries_outside_the_window_do_not_count(monkeypatch):
    calls = [3600.0]  # fake clock, starts 1 hour in

    def fake_time():
        return calls[0]

    monkeypatch.setattr("app.api.rate_limit.time.monotonic", fake_time)
    for _ in range(5):
        check_rate_limit("1.2.3.4", limit_per_hour=5)
    calls[0] += 3601  # advance past the 1-hour window
    check_rate_limit("1.2.3.4", limit_per_hour=5)  # must not raise -- old entries expired
