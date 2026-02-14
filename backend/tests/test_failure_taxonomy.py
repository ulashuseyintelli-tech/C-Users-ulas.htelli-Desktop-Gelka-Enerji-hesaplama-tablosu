"""
Failure Taxonomy tests — Feature: dependency-wrappers, Task 3.

Property 5: Failure Taxonomy Sınıflandırma Tutarlılığı
- TimeoutError, ConnectionError, ConnectionRefusedError, HTTP 5xx → True
- HTTP 4xx (429 dahil), ValueError, ValidationError → False
"""

import pytest
from hypothesis import given, settings as h_settings, HealthCheck
from hypothesis import strategies as st

from app.guards.failure_taxonomy import (
    is_cb_failure,
    is_cb_failure_status,
    is_retryable,
    CB_FAILURE_EXCEPTIONS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestIsCbFailure:
    """is_cb_failure() unit tests."""

    def test_timeout_error_is_failure(self):
        assert is_cb_failure(TimeoutError("timed out")) is True

    def test_connection_error_is_failure(self):
        assert is_cb_failure(ConnectionError("refused")) is True

    def test_connection_refused_error_is_failure(self):
        assert is_cb_failure(ConnectionRefusedError("refused")) is True

    def test_os_error_is_failure(self):
        assert is_cb_failure(OSError("socket error")) is True

    def test_value_error_not_failure(self):
        assert is_cb_failure(ValueError("bad value")) is False

    def test_runtime_error_not_failure(self):
        assert is_cb_failure(RuntimeError("runtime")) is False

    def test_key_error_not_failure(self):
        assert is_cb_failure(KeyError("key")) is False

    def test_type_error_not_failure(self):
        assert is_cb_failure(TypeError("type")) is False

    def test_http_5xx_is_failure(self):
        """HTTP 5xx response → CB failure."""
        exc = _make_http_exc(500)
        assert is_cb_failure(exc) is True

    def test_http_502_is_failure(self):
        exc = _make_http_exc(502)
        assert is_cb_failure(exc) is True

    def test_http_503_is_failure(self):
        exc = _make_http_exc(503)
        assert is_cb_failure(exc) is True

    def test_http_429_not_failure(self):
        """HTTP 429 → NOT CB failure (rate-limited by upstream)."""
        exc = _make_http_exc(429)
        assert is_cb_failure(exc) is False

    def test_http_400_not_failure(self):
        exc = _make_http_exc(400)
        assert is_cb_failure(exc) is False

    def test_http_401_not_failure(self):
        exc = _make_http_exc(401)
        assert is_cb_failure(exc) is False

    def test_http_404_not_failure(self):
        exc = _make_http_exc(404)
        assert is_cb_failure(exc) is False

    def test_http_422_not_failure(self):
        exc = _make_http_exc(422)
        assert is_cb_failure(exc) is False


class TestIsCbFailureStatus:
    """is_cb_failure_status() unit tests."""

    def test_500_is_failure(self):
        assert is_cb_failure_status(500) is True

    def test_502_is_failure(self):
        assert is_cb_failure_status(502) is True

    def test_503_is_failure(self):
        assert is_cb_failure_status(503) is True

    def test_200_not_failure(self):
        assert is_cb_failure_status(200) is False

    def test_429_not_failure(self):
        assert is_cb_failure_status(429) is False

    def test_400_not_failure(self):
        assert is_cb_failure_status(400) is False

    def test_404_not_failure(self):
        assert is_cb_failure_status(404) is False


class TestIsRetryable:
    """is_retryable() mirrors is_cb_failure()."""

    def test_timeout_retryable(self):
        assert is_retryable(TimeoutError()) is True

    def test_connection_error_retryable(self):
        assert is_retryable(ConnectionError()) is True

    def test_value_error_not_retryable(self):
        assert is_retryable(ValueError()) is False

    def test_http_5xx_retryable(self):
        assert is_retryable(_make_http_exc(500)) is True

    def test_http_429_not_retryable(self):
        assert is_retryable(_make_http_exc(429)) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Property-Based Tests — Feature: dependency-wrappers, Property 5
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailureTaxonomyProperty:
    """Property 5: Failure Taxonomy Sınıflandırma Tutarlılığı."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(status=st.integers(min_value=500, max_value=599))
    def test_5xx_always_cb_failure(self, status):
        """Feature: dependency-wrappers, Property 5: HTTP 5xx → always True."""
        assert is_cb_failure_status(status) is True
        assert is_cb_failure(_make_http_exc(status)) is True

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(status=st.integers(min_value=400, max_value=499))
    def test_4xx_never_cb_failure(self, status):
        """Feature: dependency-wrappers, Property 5: HTTP 4xx (429 dahil) → always False."""
        assert is_cb_failure_status(status) is False
        assert is_cb_failure(_make_http_exc(status)) is False

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(status=st.integers(min_value=200, max_value=399))
    def test_2xx_3xx_never_cb_failure(self, status):
        """Feature: dependency-wrappers, Property 5: 2xx/3xx → always False."""
        assert is_cb_failure_status(status) is False

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        exc_cls=st.sampled_from([TimeoutError, ConnectionError, ConnectionRefusedError, OSError])
    )
    def test_network_exceptions_always_cb_failure(self, exc_cls):
        """Feature: dependency-wrappers, Property 5: network exceptions → always True."""
        assert is_cb_failure(exc_cls("test")) is True
        assert is_retryable(exc_cls("test")) is True

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        exc_cls=st.sampled_from([ValueError, TypeError, KeyError, RuntimeError, AttributeError])
    )
    def test_app_exceptions_never_cb_failure(self, exc_cls):
        """Feature: dependency-wrappers, Property 5: app exceptions → always False."""
        assert is_cb_failure(exc_cls("test")) is False
        assert is_retryable(exc_cls("test")) is False

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(status=st.integers(min_value=100, max_value=599))
    def test_retryable_matches_cb_failure_for_http(self, status):
        """Feature: dependency-wrappers, Property 5: is_retryable == is_cb_failure for HTTP."""
        exc = _make_http_exc(status)
        assert is_retryable(exc) == is_cb_failure(exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


class _FakeResponse:
    """Fake HTTP response for testing."""
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeHTTPError(Exception):
    """Fake HTTP error with response attribute."""
    def __init__(self, status_code: int):
        self.response = _FakeResponse(status_code)
        super().__init__(f"HTTP {status_code}")


def _make_http_exc(status_code: int) -> Exception:
    """Create a fake HTTP error with the given status code."""
    return _FakeHTTPError(status_code)
