"""
Tests for StubServer — in-process HTTP stub.

Property-based tests (Hypothesis):
  - Property 3: StubServer Fail Count Behavior

Unit tests:
  - Normal mode returns 200
  - Unlimited fail mode returns 500
  - Server start/stop lifecycle

Feature: fault-injection, Task 2.3
Requirements: 2.1, 2.2, 2.3
"""

import urllib.request
import urllib.error

import pytest
from hypothesis import given, settings, strategies as st

from app.testing.stub_server import StubServer, StubHandler


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def server():
    """Start a fresh StubServer, stop after test."""
    srv = StubServer()
    srv.start()
    # Reset handler state
    StubServer.set_fail_mode(False)
    yield srv
    srv.stop()


def _get_status(url: str) -> int:
    """Make GET request, return HTTP status code."""
    try:
        resp = urllib.request.urlopen(url)
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code


# ═══════════════════════════════════════════════════════════════════════════════
# Property-Based Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty3FailCountBehavior:
    """Feature: fault-injection, Property 3: StubServer Fail Count Behavior"""

    @settings(max_examples=100, deadline=None)
    @given(fail_count=st.integers(min_value=1, max_value=10))
    def test_first_n_fail_then_succeed(self, fail_count):
        srv = StubServer()
        srv.start()
        try:
            StubServer.set_fail_mode(True, fail_count=fail_count)
            # First fail_count requests should return 500
            for i in range(fail_count):
                status = _get_status(srv.url)
                assert status == 500, f"Request {i+1} should be 500, got {status}"
            # Next request should return 200
            status = _get_status(srv.url)
            assert status == 200, f"Request {fail_count+1} should be 200, got {status}"
        finally:
            srv.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalMode:
    """Requirement 2.1: Normal mode returns HTTP 200."""

    def test_returns_200(self, server):
        status = _get_status(server.url)
        assert status == 200

    def test_multiple_requests_200(self, server):
        for _ in range(5):
            assert _get_status(server.url) == 200


class TestFailMode:
    """Requirement 2.2: Fail mode returns HTTP 500."""

    def test_unlimited_fail_mode(self, server):
        StubServer.set_fail_mode(True, fail_count=0)
        for _ in range(10):
            assert _get_status(server.url) == 500

    def test_fail_mode_off_restores_200(self, server):
        StubServer.set_fail_mode(True)
        assert _get_status(server.url) == 500
        StubServer.set_fail_mode(False)
        assert _get_status(server.url) == 200


class TestServerLifecycle:
    """Server start/stop without errors."""

    def test_url_has_port(self, server):
        assert server.url.startswith("http://127.0.0.1:")
        port = int(server.url.split(":")[-1])
        assert port > 0

    def test_stop_is_idempotent(self, server):
        server.stop()
        server.stop()  # should not raise
