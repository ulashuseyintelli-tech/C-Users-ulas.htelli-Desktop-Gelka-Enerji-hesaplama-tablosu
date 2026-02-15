"""
Dependency Wrapper tests — Feature: dependency-wrappers, Task 8.

Property 4: Wrapper CB Entegrasyonu
Property 6: Retry Politikası Doğruluğu
Property 7: Wrapper Metrik Kaydı
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings as h_settings, HealthCheck, assume
from hypothesis import strategies as st
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig
from app.ptf_metrics import PTFMetrics
from app.guards.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    Dependency,
)
from app.guards.dependency_wrapper import (
    CircuitOpenError,
    DependencyWrapper,
    DBClientWrapper,
    ExternalAPIClientWrapper,
    CacheClientWrapper,
    create_wrapper,
)


def _run(coro):
    """Run async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_config(**overrides):
    defaults = dict(
        wrapper_timeout_seconds_default=1.0,
        wrapper_retry_max_attempts_default=2,
        wrapper_retry_backoff_base_ms=10,
        wrapper_retry_backoff_cap_ms=100,
        wrapper_retry_jitter_pct=0.0,
    )
    defaults.update(overrides)
    return GuardConfig(**defaults)


def _make_wrapper(dep=Dependency.DB_PRIMARY, cb=None, config=None, metrics=None):
    config = config or _make_config()
    metrics = metrics or PTFMetrics(registry=CollectorRegistry())
    if cb is None:
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = True
    return DependencyWrapper(dep, cb, config, metrics), cb, config, metrics


# ═══════════════════════════════════════════════════════════════════════════════
# Property 4: Wrapper CB Entegrasyonu
# ═══════════════════════════════════════════════════════════════════════════════


class TestWrapperCBIntegration:
    """Property 4: CB CLOSED → call, success → record_success; failure → record_failure."""

    def test_success_records_success(self):
        w, cb, _, _ = _make_wrapper()
        result = _run(w.call(AsyncMock(return_value="ok")))
        assert result == "ok"
        cb.record_success.assert_called_once()
        cb.record_failure.assert_not_called()

    def test_cb_failure_records_failure(self):
        w, cb, _, _ = _make_wrapper()
        with pytest.raises(ConnectionError):
            _run(w.call(AsyncMock(side_effect=ConnectionError("refused"))))
        cb.record_failure.assert_called()

    def test_cb_open_raises_circuit_open_error(self):
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = False
        w, _, _, _ = _make_wrapper(cb=cb)
        fn = AsyncMock()
        with pytest.raises(CircuitOpenError) as exc_info:
            _run(w.call(fn))
        assert exc_info.value.dependency == "db_primary"
        fn.assert_not_called()

    def test_non_cb_failure_no_record_failure(self):
        """Non-CB failure (ValueError) → no record_failure, no retry."""
        w, cb, _, _ = _make_wrapper()
        with pytest.raises(ValueError):
            _run(w.call(AsyncMock(side_effect=ValueError("bad input"))))
        cb.record_failure.assert_not_called()

    def test_timeout_records_failure(self):
        """Timeout → record_failure + timeout metric."""
        config = _make_config(wrapper_retry_max_attempts_default=0)
        w, cb, _, _ = _make_wrapper(config=config)

        async def slow_fn():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            _run(w.call(slow_fn))
        cb.record_failure.assert_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Property 6: Retry Politikası Doğruluğu
# ═══════════════════════════════════════════════════════════════════════════════


class TestWrapperRetryPolicy:
    """Property 6: Retry doğruluğu — DW-1 kuralı."""

    def test_retry_on_cb_failure(self):
        """CB failure → retry up to max_retries."""
        config = _make_config(wrapper_retry_max_attempts_default=2)
        w, cb, _, _ = _make_wrapper(config=config)
        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refused")

        with pytest.raises(ConnectionError):
            _run(w.call(failing_fn))
        assert call_count == 3  # 1 initial + 2 retries

    def test_no_retry_on_write_default(self):
        """DW-1: is_write=True + wrapper_retry_on_write=False → no retry."""
        config = _make_config(
            wrapper_retry_max_attempts_default=2,
            wrapper_retry_on_write=False,
        )
        w, _, _, _ = _make_wrapper(config=config)
        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refused")

        with pytest.raises(ConnectionError):
            _run(w.call(failing_fn, is_write=True))
        assert call_count == 1

    def test_retry_on_write_when_flag_enabled(self):
        """DW-1: is_write=True + wrapper_retry_on_write=True → retry allowed."""
        config = _make_config(
            wrapper_retry_max_attempts_default=1,
            wrapper_retry_on_write=True,
        )
        w, _, _, _ = _make_wrapper(config=config)
        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("refused")

        with pytest.raises(ConnectionError):
            _run(w.call(failing_fn, is_write=True))
        assert call_count == 2  # 1 initial + 1 retry

    def test_no_retry_on_non_cb_failure(self):
        """Non-CB failure (ValueError) → no retry regardless of config."""
        config = _make_config(wrapper_retry_max_attempts_default=2)
        w, _, _, _ = _make_wrapper(config=config)
        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("bad")

        with pytest.raises(ValueError):
            _run(w.call(failing_fn))
        assert call_count == 1

    def test_retry_stops_when_cb_opens(self):
        """CB opens during retry → CircuitOpenError, retry stops."""
        config = _make_config(wrapper_retry_max_attempts_default=3)
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.side_effect = [True, False]
        w, _, _, _ = _make_wrapper(config=config, cb=cb)

        async def failing_fn():
            raise ConnectionError("refused")

        with pytest.raises(CircuitOpenError):
            _run(w.call(failing_fn))

    def test_retry_increments_metric(self):
        """Each retry → ptf_admin_dependency_retry_total++."""
        config = _make_config(wrapper_retry_max_attempts_default=2)
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(config=config, metrics=metrics)

        async def failing_fn():
            raise ConnectionError("refused")

        with pytest.raises(ConnectionError):
            _run(w.call(failing_fn))
        val = metrics._dependency_retry_total.labels(dependency="db_primary")._value.get()
        assert val == 2.0

    def test_retry_succeeds_on_second_attempt(self):
        """First call fails, retry succeeds → success."""
        config = _make_config(wrapper_retry_max_attempts_default=1)
        w, _, _, _ = _make_wrapper(config=config)
        call_count = 0

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("refused")
            return "recovered"

        result = _run(w.call(flaky_fn))
        assert result == "recovered"
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Property 7: Wrapper Metrik Kaydı
# ═══════════════════════════════════════════════════════════════════════════════


class TestWrapperMetrics:
    """Property 7: Correct metric recording for each outcome."""

    def test_success_metrics(self):
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(metrics=metrics)
        _run(w.call(AsyncMock(return_value="ok")))
        val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="success"
        )._value.get()
        assert val == 1.0
        dur = metrics._dependency_call_duration.labels(dependency="db_primary")._sum.get()
        assert dur > 0

    def test_timeout_metrics(self):
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(config=config, metrics=metrics)

        async def slow_fn():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            _run(w.call(slow_fn))
        val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="timeout"
        )._value.get()
        assert val == 1.0

    def test_circuit_open_metrics(self):
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = False
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(cb=cb, metrics=metrics)
        with pytest.raises(CircuitOpenError):
            _run(w.call(AsyncMock()))
        val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="circuit_open"
        )._value.get()
        assert val == 1.0

    def test_failure_metrics(self):
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(config=config, metrics=metrics)
        with pytest.raises(ConnectionError):
            _run(w.call(AsyncMock(side_effect=ConnectionError("refused"))))
        val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="failure"
        )._value.get()
        assert val == 1.0

    def test_client_error_metrics(self):
        """Non-CB failure (ValueError) → outcome=client_error, not failure."""
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(config=config, metrics=metrics)
        with pytest.raises(ValueError):
            _run(w.call(AsyncMock(side_effect=ValueError("bad input"))))
        val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="client_error"
        )._value.get()
        assert val == 1.0
        # CB failure counter should NOT be incremented
        fail_val = metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="failure"
        )._value.get()
        assert fail_val == 0.0

    def test_duration_recorded_on_failure(self):
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        w, _, _, _ = _make_wrapper(config=config, metrics=metrics)
        with pytest.raises(ConnectionError):
            _run(w.call(AsyncMock(side_effect=ConnectionError("refused"))))
        dur = metrics._dependency_call_duration.labels(dependency="db_primary")._sum.get()
        assert dur >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# Factory Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateWrapper:
    """create_wrapper() factory tests."""

    def test_db_primary_returns_db_wrapper(self):
        config = _make_config()
        metrics = PTFMetrics(registry=CollectorRegistry())
        registry = CircuitBreakerRegistry(config, metrics)
        w = create_wrapper(Dependency.DB_PRIMARY, registry, config, metrics)
        assert isinstance(w, DBClientWrapper)

    def test_external_api_returns_api_wrapper(self):
        config = _make_config()
        metrics = PTFMetrics(registry=CollectorRegistry())
        registry = CircuitBreakerRegistry(config, metrics)
        w = create_wrapper(Dependency.EXTERNAL_API, registry, config, metrics)
        assert isinstance(w, ExternalAPIClientWrapper)

    def test_cache_returns_cache_wrapper(self):
        config = _make_config()
        metrics = PTFMetrics(registry=CollectorRegistry())
        registry = CircuitBreakerRegistry(config, metrics)
        w = create_wrapper(Dependency.CACHE, registry, config, metrics)
        assert isinstance(w, CacheClientWrapper)

    def test_all_dependencies_create_successfully(self):
        config = _make_config()
        metrics = PTFMetrics(registry=CollectorRegistry())
        registry = CircuitBreakerRegistry(config, metrics)
        for dep in Dependency:
            w = create_wrapper(dep, registry, config, metrics)
            assert isinstance(w, DependencyWrapper)
            assert w.dependency_name == dep.value


# ═══════════════════════════════════════════════════════════════════════════════
# Property-Based Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestWrapperCBIntegrationProperty:
    """Property 4: Wrapper CB Entegrasyonu — PBT."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(list(Dependency)),
        call_succeeds=st.booleans(),
        cb_is_open=st.booleans(),
    )
    def test_cb_integration_property(self, dep, call_succeeds, cb_is_open):
        """Feature: dependency-wrappers, Property 4: Wrapper CB Entegrasyonu."""
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = not cb_is_open
        w = DependencyWrapper(dep, cb, config, metrics)

        if cb_is_open:
            with pytest.raises(CircuitOpenError):
                _run(w.call(AsyncMock()))
            cb.record_success.assert_not_called()
        elif call_succeeds:
            result = _run(w.call(AsyncMock(return_value="ok")))
            assert result == "ok"
            cb.record_success.assert_called_once()
        else:
            with pytest.raises(ConnectionError):
                _run(w.call(AsyncMock(side_effect=ConnectionError("fail"))))
            cb.record_failure.assert_called()


class TestWrapperRetryPolicyProperty:
    """Property 6: Retry Politikası Doğruluğu — PBT."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        max_retries=st.integers(min_value=0, max_value=3),
        is_write=st.booleans(),
        retry_on_write=st.booleans(),
    )
    def test_retry_policy_property(self, max_retries, is_write, retry_on_write):
        """Feature: dependency-wrappers, Property 6: Retry Politikası Doğruluğu."""
        config = _make_config(
            wrapper_retry_max_attempts_default=max_retries,
            wrapper_retry_on_write=retry_on_write,
            wrapper_retry_backoff_base_ms=1,
            wrapper_retry_backoff_cap_ms=10,
        )
        metrics = PTFMetrics(registry=CollectorRegistry())
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = True
        w = DependencyWrapper(Dependency.DB_PRIMARY, cb, config, metrics)

        call_count = 0

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            _run(w.call(failing_fn, is_write=is_write))

        can_retry = not is_write or retry_on_write
        expected_calls = 1 + (max_retries if can_retry else 0)
        assert call_count == expected_calls


class TestWrapperMetricsProperty:
    """Property 7: Wrapper Metrik Kaydı — PBT."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(list(Dependency)),
        n_calls=st.integers(min_value=1, max_value=5),
    )
    def test_success_metric_monotonic(self, dep, n_calls):
        """Feature: dependency-wrappers, Property 7: success counter monotonic."""
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = True
        w = DependencyWrapper(dep, cb, config, metrics)

        for _ in range(n_calls):
            _run(w.call(AsyncMock(return_value="ok")))

        val = metrics._dependency_call_total.labels(
            dependency=dep.value, outcome="success"
        )._value.get()
        assert val == n_calls

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(list(Dependency)),
        exc_is_cb=st.booleans(),
    )
    def test_failure_vs_client_error_outcome(self, dep, exc_is_cb):
        """Feature: dependency-wrappers, Property 7: CB failure → outcome=failure, non-CB → outcome=client_error."""
        config = _make_config(wrapper_retry_max_attempts_default=0)
        metrics = PTFMetrics(registry=CollectorRegistry())
        cb = MagicMock(spec=CircuitBreaker)
        cb.allow_request.return_value = True
        w = DependencyWrapper(dep, cb, config, metrics)

        if exc_is_cb:
            with pytest.raises(ConnectionError):
                _run(w.call(AsyncMock(side_effect=ConnectionError("fail"))))
            val = metrics._dependency_call_total.labels(
                dependency=dep.value, outcome="failure"
            )._value.get()
            assert val == 1.0
            client_val = metrics._dependency_call_total.labels(
                dependency=dep.value, outcome="client_error"
            )._value.get()
            assert client_val == 0.0
        else:
            with pytest.raises(ValueError):
                _run(w.call(AsyncMock(side_effect=ValueError("bad"))))
            val = metrics._dependency_call_total.labels(
                dependency=dep.value, outcome="client_error"
            )._value.get()
            assert val == 1.0
            fail_val = metrics._dependency_call_total.labels(
                dependency=dep.value, outcome="failure"
            )._value.get()
            assert fail_val == 0.0
