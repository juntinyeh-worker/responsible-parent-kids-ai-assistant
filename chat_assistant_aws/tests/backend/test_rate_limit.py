"""Unit + property tests for rate limiter."""
import time
import pytest
from hypothesis import given, settings, strategies as st
from rate_limit import RateLimiter


def test_allows_first_5_requests():
    limiter = RateLimiter(max_requests=5, window=60)
    for i in range(5):
        assert limiter.check("1.2.3.4") is True


def test_blocks_6th_request():
    limiter = RateLimiter(max_requests=5, window=60)
    for _ in range(5):
        limiter.check("1.2.3.4")
    assert limiter.check("1.2.3.4") is False


def test_different_ips_independent():
    limiter = RateLimiter(max_requests=5, window=60)
    for _ in range(5):
        limiter.check("1.1.1.1")
    assert limiter.check("1.1.1.1") is False
    assert limiter.check("2.2.2.2") is True


def test_window_expiry():
    limiter = RateLimiter(max_requests=2, window=0.1)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("1.1.1.1") is False
    time.sleep(0.15)
    assert limiter.check("1.1.1.1") is True


def test_cleanup_removes_expired():
    limiter = RateLimiter(max_requests=5, window=0.1)
    limiter.check("old-ip")
    time.sleep(0.15)
    limiter.cleanup()
    assert "old-ip" not in limiter._requests
