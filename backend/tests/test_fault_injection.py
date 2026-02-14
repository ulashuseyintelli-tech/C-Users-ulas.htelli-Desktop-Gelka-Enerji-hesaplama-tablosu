"""
Tests for FaultInjector core infrastructure.

Property-based tests (Hypothesis):
  - Property 1: Enable/Disable Round-Trip
  - Property 2: TTL Auto-Expiry

Unit tests:
  - Singleton pattern
  - InjectionPoint enum members
  - disable_all behavior
  - Params storage and retrieval

Feature: fault-injection, Task 1.2
Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
"""

import time
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st

from app.testing.fault_injection import FaultInjector, InjectionPoint, InjectionState


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_injector():
    """Reset singleton before and after each test."""
    FaultInjector.reset_instance()
    yield
    FaultInjector.reset_instance()


# ═══════════════════════════════════════════════════════════════════════════════
# Property-Based Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty1EnableDisableRoundTrip:
    """Feature: fault-injection, Property 1: FaultInjector Enable/Disable Round-Trip"""

    @settings(max_examples=100)
    @given(
        point=st.sampled_from(list(InjectionPoint)),
        ttl=st.floats(min_value=10.0, max_value=300.0),
    )
    def test_enable_then_query_returns_true(self, point, ttl):
        injector = FaultInjector()
        injector.enable(point, ttl_seconds=ttl)
        assert injector.is_enabled(point) is True

    @settings(max_examples=100)
    @given(
        point=st.sampled_from(list(InjectionPoint)),
        ttl=st.floats(min_value=10.0, max_value=300.0),
    )
    def test_enable_then_disable_returns_false(self, point, ttl):
        injector = FaultInjector()
        injector.enable(point, ttl_seconds=ttl)
        injector.disable(point)
        assert injector.is_enabled(point) is False

    @settings(max_examples=100)
    @given(
        point=st.sampled_from(list(InjectionPoint)),
        params=st.fixed_dictionaries({
            "delay_seconds": st.floats(min_value=0.0, max_value=5.0),
        }),
    )
    def test_params_preserved_after_enable(self, point, params):
        injector = FaultInjector()
        injector.enable(point, params=params, ttl_seconds=60.0)
        assert injector.get_params(point) == params


class TestProperty2TTLAutoExpiry:
    """Feature: fault-injection, Property 2: FaultInjector TTL Auto-Expiry"""

    @settings(max_examples=100)
    @given(
        point=st.sampled_from(list(InjectionPoint)),
        ttl=st.floats(min_value=0.001, max_value=1.0),
    )
    def test_ttl_expires_after_duration(self, point, ttl):
        injector = FaultInjector()
        fake_start = 1000.0
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return fake_start  # enable() call
            elif call_count == 2:
                return fake_start + ttl * 0.5  # first is_enabled — within TTL
            else:
                return fake_start + ttl + 0.001  # second is_enabled — past TTL

        with patch("app.testing.fault_injection.time.monotonic", side_effect=fake_monotonic):
            injector.enable(point, ttl_seconds=ttl)
            assert injector.is_enabled(point) is True   # within TTL
            assert injector.is_enabled(point) is False   # past TTL

    @settings(max_examples=100)
    @given(
        point=st.sampled_from(list(InjectionPoint)),
        ttl=st.floats(min_value=0.001, max_value=1.0),
    )
    def test_ttl_not_expired_within_duration(self, point, ttl):
        injector = FaultInjector()
        fake_start = 1000.0
        call_count = 0

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return fake_start  # enable() call
            return fake_start + ttl * 0.5  # is_enabled — within TTL

        with patch("app.testing.fault_injection.time.monotonic", side_effect=fake_monotonic):
            injector.enable(point, ttl_seconds=ttl)
            assert injector.is_enabled(point) is True


# ═══════════════════════════════════════════════════════════════════════════════
# Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSingleton:
    """Requirement 1.6: Singleton pattern."""

    def test_get_instance_returns_same_object(self):
        a = FaultInjector.get_instance()
        b = FaultInjector.get_instance()
        assert a is b

    def test_reset_instance_creates_new_object(self):
        a = FaultInjector.get_instance()
        FaultInjector.reset_instance()
        b = FaultInjector.get_instance()
        assert a is not b


class TestInjectionPointEnum:
    """Requirement 1.1: Five injection points."""

    def test_has_five_members(self):
        assert len(InjectionPoint) == 5

    def test_member_names(self):
        expected = {
            "DB_TIMEOUT",
            "EXTERNAL_5XX_BURST",
            "KILLSWITCH_TOGGLE",
            "RATE_LIMIT_SPIKE",
            "GUARD_INTERNAL_ERROR",
        }
        assert {p.name for p in InjectionPoint} == expected

    def test_string_values(self):
        for p in InjectionPoint:
            assert isinstance(p.value, str)
            assert p.value == p.name


class TestDisableAll:
    """disable_all should deactivate every injection point."""

    def test_disable_all_clears_all_points(self):
        injector = FaultInjector()
        for p in InjectionPoint:
            injector.enable(p, ttl_seconds=60.0)
        injector.disable_all()
        for p in InjectionPoint:
            assert injector.is_enabled(p) is False

    def test_disable_all_clears_params(self):
        injector = FaultInjector()
        injector.enable(
            InjectionPoint.DB_TIMEOUT,
            params={"delay_seconds": 1.0},
            ttl_seconds=60.0,
        )
        injector.disable_all()
        assert injector.get_params(InjectionPoint.DB_TIMEOUT) == {}


class TestInitialState:
    """All points should be disabled initially."""

    def test_all_disabled_on_init(self):
        injector = FaultInjector()
        for p in InjectionPoint:
            assert injector.is_enabled(p) is False

    def test_params_empty_on_init(self):
        injector = FaultInjector()
        for p in InjectionPoint:
            assert injector.get_params(p) == {}


class TestZeroTTL:
    """TTL=0 means no auto-expiry."""

    def test_zero_ttl_never_expires(self):
        injector = FaultInjector()
        fake_start = 1000.0
        with patch("app.testing.fault_injection.time.monotonic", return_value=fake_start):
            injector.enable(InjectionPoint.DB_TIMEOUT, ttl_seconds=0.0)

        # Even far in the future, should still be enabled
        with patch(
            "app.testing.fault_injection.time.monotonic",
            return_value=fake_start + 999999.0,
        ):
            assert injector.is_enabled(InjectionPoint.DB_TIMEOUT) is True
