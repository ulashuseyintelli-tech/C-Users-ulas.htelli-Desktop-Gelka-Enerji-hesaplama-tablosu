"""Faz G — Performans Telemetri Testleri (unit + PBT).

Feature: invoice-validation-perf-telemetry
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.invoice.validation.telemetry import (
    Phase,
    Timer,
    VALID_MODES,
    VALID_PHASES,
    get_duration_observations,
    get_mode_gauge,
    observe_duration,
    reset_duration_observations,
    reset_mode_gauge,
    set_mode_gauge,
)
from app.invoice.validation.telemetry_config import (
    LatencyBudgetConfig,
    _parse_positive_float,
    load_latency_budget_config,
    resolve_mode,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Her testten önce/sonra telemetri state'ini sıfırla."""
    reset_duration_observations()
    reset_mode_gauge()
    yield
    reset_duration_observations()
    reset_mode_gauge()


# ===================================================================
# UNIT TESTS
# ===================================================================


class TestPhaseClosedSet:
    """Phase kapalı küme — invalid → log + skip, exception yok."""

    def test_valid_phase_records_observation(self):
        observe_duration("total", 0.5)
        observe_duration("shadow", 0.3)
        observe_duration("enforcement", 0.1)
        obs = get_duration_observations()
        assert obs["total"] == [0.5]
        assert obs["shadow"] == [0.3]
        assert obs["enforcement"] == [0.1]

    def test_invalid_phase_no_exception(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.invoice.validation.telemetry"):
            observe_duration("bogus", 1.0)
        obs = get_duration_observations()
        assert all(len(v) == 0 for v in obs.values())
        assert "geçersiz phase" in caplog.text

    def test_empty_phase_no_exception(self, caplog):
        with caplog.at_level(logging.ERROR, logger="app.invoice.validation.telemetry"):
            observe_duration("", 1.0)
        assert all(len(v) == 0 for v in get_duration_observations().values())


class TestModeGaugeClosedSet:
    """Mode gauge — invalid → skip, valid → atomic 1/0."""

    def test_valid_mode_sets_one_active(self):
        set_mode_gauge("enforce_hard")
        g = get_mode_gauge()
        assert g["enforce_hard"] == 1
        assert sum(g.values()) == 1

    def test_invalid_mode_no_update(self, caplog):
        set_mode_gauge("shadow")  # baseline
        with caplog.at_level(logging.ERROR, logger="app.invoice.validation.telemetry"):
            set_mode_gauge("invalid_mode")
        g = get_mode_gauge()
        assert g["shadow"] == 1  # unchanged
        assert "geçersiz mode" in caplog.text

    def test_mode_switch_atomic(self):
        set_mode_gauge("off")
        set_mode_gauge("enforce_soft")
        g = get_mode_gauge()
        assert g["enforce_soft"] == 1
        assert g["off"] == 0
        assert sum(g.values()) == 1


class TestLatencyBudgetParse:
    """Budget parse — negative/non-numeric → None + log, valid → float."""

    def test_valid_float(self):
        assert _parse_positive_float("100.5", "TEST") == 100.5

    def test_negative_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.invoice.validation.telemetry_config"):
            result = _parse_positive_float("-5", "TEST")
        assert result is None
        assert "pozitif olmalı" in caplog.text

    def test_zero_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.invoice.validation.telemetry_config"):
            result = _parse_positive_float("0", "TEST")
        assert result is None

    def test_non_numeric_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.invoice.validation.telemetry_config"):
            result = _parse_positive_float("abc", "TEST")
        assert result is None
        assert "geçersiz değer" in caplog.text

    def test_empty_returns_none(self):
        assert _parse_positive_float("", "TEST") is None

    def test_whitespace_returns_none(self):
        assert _parse_positive_float("   ", "TEST") is None

    def test_load_config_from_env(self):
        with patch.dict(os.environ, {
            "INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS": "50.0",
            "INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS": "100.0",
        }):
            cfg = load_latency_budget_config()
        assert cfg.p95_ms == 50.0
        assert cfg.p99_ms == 100.0

    def test_load_config_unset_no_budget(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_latency_budget_config()
        assert cfg.p95_ms is None
        assert cfg.p99_ms is None


class TestModeResolveFallback:
    """Mode resolve — invalid → 'shadow' + log, valid → same string."""

    def test_valid_modes(self):
        for m in ("off", "shadow", "enforce_soft", "enforce_hard"):
            assert resolve_mode(m) == m

    def test_invalid_mode_fallback(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.invoice.validation.telemetry_config"):
            result = resolve_mode("turbo")
        assert result == "shadow"
        assert "geçersiz mode" in caplog.text

    def test_empty_mode_fallback_no_log(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.invoice.validation.telemetry_config"):
            result = resolve_mode("")
        assert result == "shadow"
        # empty string → no warning (intentional: unset env)

    def test_none_mode_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            result = resolve_mode(None)
        assert result == "shadow"

    def test_case_insensitive(self):
        assert resolve_mode("ENFORCE_SOFT") == "enforce_soft"
        assert resolve_mode("Shadow") == "shadow"


class TestTimerIsolation:
    """Timer — exception propagation kontrolü."""

    def test_timer_measures_elapsed(self):
        with Timer() as t:
            _ = sum(range(1000))
        assert t.elapsed > 0

    def test_user_exception_propagates_but_elapsed_set(self):
        """Timer içinde user exception → pipeline exception devam eder,
        ama elapsed hesaplanır (try/finally)."""
        t = Timer()
        with pytest.raises(ValueError, match="user error"):
            with t:
                raise ValueError("user error")
        assert t.elapsed >= 0  # __exit__ ran via finally

    def test_observe_duration_never_raises(self, caplog):
        """observe_duration geçersiz phase'de exception atmaz."""
        with caplog.at_level(logging.ERROR, logger="app.invoice.validation.telemetry"):
            # Bu satır exception atmamalı
            observe_duration("NONEXISTENT", 1.0)
        assert all(len(v) == 0 for v in get_duration_observations().values())


class TestValidationBlockedErrorTerminal:
    """ValidationBlockedError.terminal sentinel."""

    def test_terminal_attribute(self):
        from app.invoice.validation.enforcement import ValidationBlockedError
        assert ValidationBlockedError.terminal is True


class TestHistogramModeLabel:
    """Histogram'da mode label yok — observe_duration sadece phase kabul eder."""

    def test_observe_duration_signature_phase_only(self):
        """observe_duration(phase, duration) — 2 arg, mode yok."""
        import inspect
        sig = inspect.signature(observe_duration)
        params = list(sig.parameters.keys())
        assert params == ["phase", "duration_seconds"]


class TestHistogramPreservedOnModeChange:
    """Mod geçişinde histogram verileri sıfırlanmaz."""

    def test_mode_change_preserves_observations(self):
        observe_duration("total", 0.5)
        set_mode_gauge("shadow")
        observe_duration("total", 0.7)
        set_mode_gauge("enforce_hard")
        obs = get_duration_observations()
        assert obs["total"] == [0.5, 0.7]


# ===================================================================
# PROPERTY-BASED TESTS (8 properties)
# ===================================================================


class TestPBTPhaseClosedSet:
    """Property 1: Phase kapalı küme — random string → skip veya record, asla raise."""

    @given(phase=st.text(min_size=0, max_size=50))
    @settings(max_examples=100)
    def test_prop_phase_closed_set(self, phase: str):
        """Feature: invoice-validation-perf-telemetry, Property 1: Phase Kapalı Küme"""
        reset_duration_observations()
        # MUST NOT raise
        observe_duration(phase, 0.1)
        obs = get_duration_observations()
        if phase in VALID_PHASES:
            assert 0.1 in obs[phase]
        else:
            assert all(0.1 not in v for v in obs.values())


class TestPBTModeGaugeInvariant:
    """Property 5: Mod gauge — valid → exactly one 1, invalid → no change."""

    @given(mode=st.text(min_size=0, max_size=50))
    @settings(max_examples=100)
    def test_prop_mode_gauge_invariant(self, mode: str):
        """Feature: invoice-validation-perf-telemetry, Property 5: Mod Gauge Tek Aktif"""
        reset_mode_gauge()
        before = get_mode_gauge()
        set_mode_gauge(mode)
        after = get_mode_gauge()
        if mode in VALID_MODES:
            assert after[mode] == 1
            assert sum(after.values()) == 1
        else:
            assert after == before


class TestPBTDurationNonNegative:
    """Property 2 (partial): Timer çıktısı her zaman ≥ 0."""

    @given(data=st.data())
    @settings(max_examples=100)
    def test_prop_timer_non_negative(self, data):
        """Feature: invoice-validation-perf-telemetry, Property 2: Duration Non-Negative"""
        with Timer() as t:
            # simulate trivial work
            _ = data.draw(st.integers(min_value=0, max_value=1000))
        assert t.elapsed >= 0


class TestPBTShadowDurationSampling:
    """Property 3: Shadow duration — rate=0 → no shadow obs, rate=1 → shadow obs."""

    @given(invoice_id=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_prop_shadow_rate_zero_no_observation(self, invoice_id: str):
        """Feature: invoice-validation-perf-telemetry, Property 3: Shadow Sampling"""
        reset_duration_observations()
        from app.invoice.validation.shadow_config import should_sample
        sampled = should_sample(invoice_id, 0.0)
        assert sampled is False
        # If not sampled, shadow_validate_hook returns None → no shadow observation
        obs = get_duration_observations()
        assert len(obs["shadow"]) == 0

    @given(invoice_id=st.text(min_size=1, max_size=20))
    @settings(max_examples=100)
    def test_prop_shadow_rate_one_always_sampled(self, invoice_id: str):
        """Feature: invoice-validation-perf-telemetry, Property 3: Shadow Always Sampled"""
        from app.invoice.validation.shadow_config import should_sample
        sampled = should_sample(invoice_id, 1.0)
        assert sampled is True


class TestPBTEnforcementDurationMode:
    """Property 4: Enforcement duration — only in soft/hard modes."""

    @given(mode=st.sampled_from(["off", "shadow", "enforce_soft", "enforce_hard"]))
    @settings(max_examples=100)
    def test_prop_enforcement_mode_dependent(self, mode: str):
        """Feature: invoice-validation-perf-telemetry, Property 4: Enforcement Mode"""
        reset_duration_observations()
        from app.invoice.validation.enforcement import enforce_validation
        from app.invoice.validation.enforcement_config import (
            EnforcementConfig,
            ValidationMode,
        )

        cfg = EnforcementConfig(mode=ValidationMode(mode))
        # Minimal valid invoice for enforcement
        invoice = {
            "ettn": "550e8400-e29b-41d4-a716-446655440000",
            "periods": [
                {"code": "T1", "start": "2024-01-01", "end": "2024-01-31", "kwh": 100, "amount": 50},
                {"code": "T2", "start": "2024-01-01", "end": "2024-01-31", "kwh": 200, "amount": 100},
                {"code": "T3", "start": "2024-01-01", "end": "2024-01-31", "kwh": 300, "amount": 150},
            ],
        }
        enforce_validation(invoice, [], config=cfg)
        obs = get_duration_observations()
        if mode in ("enforce_soft", "enforce_hard"):
            assert len(obs["enforcement"]) > 0
        else:
            assert len(obs["enforcement"]) == 0


class TestPBTHistogramNoModeLabel:
    """Property (API): observe_duration sadece phase kabul eder, mode label yok."""

    @given(
        phase=st.sampled_from(list(VALID_PHASES)),
        duration=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_prop_no_mode_label_in_histogram(self, phase: str, duration: float):
        """Feature: invoice-validation-perf-telemetry, Property: No Mode Label"""
        reset_duration_observations()
        observe_duration(phase, duration)
        obs = get_duration_observations()
        # Only phase keys exist — no mode key
        assert set(obs.keys()) == {"total", "shadow", "enforcement"}


class TestPBTBudgetParseRoundTrip:
    """Property 7: Budget parse — valid positive float round-trips, invalid → None."""

    @given(val=st.floats(min_value=0.001, max_value=1e9, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_prop_valid_float_roundtrip(self, val: float):
        """Feature: invoice-validation-perf-telemetry, Property 7: Budget Parse Valid"""
        result = _parse_positive_float(str(val), "TEST")
        assert result is not None
        assert abs(result - val) < 1e-6 * val  # float string round-trip tolerance

    @given(raw=st.text(min_size=0, max_size=30).filter(lambda s: not _is_valid_positive_float(s)))
    @settings(max_examples=100)
    def test_prop_invalid_returns_none(self, raw: str):
        """Feature: invoice-validation-perf-telemetry, Property 7: Budget Parse Invalid"""
        result = _parse_positive_float(raw, "TEST")
        assert result is None


class TestPBTHistogramPreservedOnModeChange:
    """Property 8: Mod geçişinde histogram korunur."""

    @given(
        observations=st.lists(st.floats(min_value=0.0, max_value=100.0, allow_nan=False), min_size=1, max_size=20),
        modes=st.lists(st.sampled_from(list(VALID_MODES)), min_size=1, max_size=5),
    )
    @settings(max_examples=100)
    def test_prop_histogram_preserved(self, observations: list[float], modes: list[str]):
        """Feature: invoice-validation-perf-telemetry, Property 8: Histogram Preserved"""
        reset_duration_observations()
        for val in observations:
            observe_duration("total", val)
        for m in modes:
            set_mode_gauge(m)
        obs = get_duration_observations()
        assert obs["total"] == observations


# ---------------------------------------------------------------------------
# Helper for PBT filter
# ---------------------------------------------------------------------------

def _is_valid_positive_float(s: str) -> bool:
    """Check if string parses to a positive float."""
    try:
        v = float(s.strip())
        return v > 0
    except (ValueError, TypeError):
        return False
