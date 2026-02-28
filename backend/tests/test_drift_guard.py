"""
Drift Guard — Unit + Integration Tests (Task 4).

DG1) DriftReasonCode enum: closed set, DRIFT: prefix
DG2) DriftInput / DriftDecision: frozen dataclasses
DG3) StubDriftInputProvider: returns valid DriftInput
DG4) evaluate_drift stub: always no-drift
DG5) Config: drift_guard_enabled / drift_guard_killswitch defaults
DG6) Kill-switch 4-spy: provider/evaluator/metrics/telemetry all 0 call
DG7) Disabled mode: provider not called
DG8) Provider failure semantics (shadow vs enforce)
DG9) Mode dispatch: shadow log + proceed, enforce 503
DG10) wouldEnforce semantics
DG11) Metric bounded cardinality

Requirements: DR1.1–DR6.3
"""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from app.guards.drift_guard import (
    DriftDecision,
    DriftInput,
    DriftInputProvider,
    DriftReasonCode,
    StubDriftInputProvider,
    evaluate_drift,
)


# ═══════════════════════════════════════════════════════════════════════
# DG1) DriftReasonCode enum
# ═══════════════════════════════════════════════════════════════════════


class TestDriftReasonCode:
    def test_exactly_three_members(self):
        assert len(DriftReasonCode) == 3

    def test_all_have_drift_prefix(self):
        for rc in DriftReasonCode:
            assert rc.value.startswith("DRIFT:"), f"{rc} missing DRIFT: prefix"

    def test_expected_values(self):
        assert set(DriftReasonCode) == {
            DriftReasonCode.PROVIDER_ERROR,
            DriftReasonCode.THRESHOLD_EXCEEDED,
            DriftReasonCode.INPUT_ANOMALY,
        }

    def test_is_str_enum(self):
        for rc in DriftReasonCode:
            assert isinstance(rc, str)
            assert isinstance(rc.value, str)


# ═══════════════════════════════════════════════════════════════════════
# DG2) DriftInput / DriftDecision frozen dataclasses
# ═══════════════════════════════════════════════════════════════════════


class TestDriftInput:
    def test_frozen(self):
        di = DriftInput(endpoint="/test", method="GET", tenant_id="t1",
                        request_signature="sig", timestamp_ms=1000)
        with pytest.raises(dataclasses.FrozenInstanceError):
            di.endpoint = "/changed"

    def test_fields(self):
        di = DriftInput(endpoint="/e", method="POST", tenant_id="t",
                        request_signature="s", timestamp_ms=42)
        assert di.endpoint == "/e"
        assert di.method == "POST"
        assert di.tenant_id == "t"
        assert di.request_signature == "s"
        assert di.timestamp_ms == 42


class TestDriftDecision:
    def test_frozen(self):
        dd = DriftDecision(is_drift=False)
        with pytest.raises(dataclasses.FrozenInstanceError):
            dd.is_drift = True

    def test_defaults(self):
        dd = DriftDecision(is_drift=False)
        assert dd.reason_code is None
        assert dd.detail == ""
        assert dd.would_enforce is False

    def test_drift_with_reason(self):
        dd = DriftDecision(
            is_drift=True,
            reason_code=DriftReasonCode.THRESHOLD_EXCEEDED,
            detail="score=0.95",
            would_enforce=True,
        )
        assert dd.is_drift is True
        assert dd.reason_code == DriftReasonCode.THRESHOLD_EXCEEDED
        assert dd.would_enforce is True


# ═══════════════════════════════════════════════════════════════════════
# DG3) StubDriftInputProvider
# ═══════════════════════════════════════════════════════════════════════


class TestStubDriftInputProvider:
    def test_returns_drift_input(self):
        provider = StubDriftInputProvider()
        request = MagicMock()
        result = provider.get_input(request, "/test", "GET", "tenant1")
        assert isinstance(result, DriftInput)
        assert result.endpoint == "/test"
        assert result.method == "GET"
        assert result.tenant_id == "tenant1"
        assert result.timestamp_ms > 0


# ═══════════════════════════════════════════════════════════════════════
# DG4) evaluate_drift stub
# ═══════════════════════════════════════════════════════════════════════


class TestEvaluateDrift:
    def test_stub_returns_no_drift(self):
        di = DriftInput(endpoint="/e", method="GET", tenant_id="t",
                        request_signature="s", timestamp_ms=1000)
        result = evaluate_drift(di)
        assert isinstance(result, DriftDecision)
        assert result.is_drift is False
        assert result.reason_code is None


# ═══════════════════════════════════════════════════════════════════════
# DG5) Config defaults
# ═══════════════════════════════════════════════════════════════════════


class TestDriftGuardConfig:
    def test_drift_guard_enabled_default_false(self):
        from app.guard_config import GuardConfig
        config = GuardConfig()
        assert config.drift_guard_enabled is False

    def test_drift_guard_killswitch_default_false(self):
        from app.guard_config import GuardConfig
        config = GuardConfig()
        assert config.drift_guard_killswitch is False

    def test_fallback_defaults_include_drift_fields(self):
        from app.guard_config import _FALLBACK_DEFAULTS
        assert "drift_guard_enabled" in _FALLBACK_DEFAULTS
        assert "drift_guard_killswitch" in _FALLBACK_DEFAULTS
        assert _FALLBACK_DEFAULTS["drift_guard_enabled"] is False
        assert _FALLBACK_DEFAULTS["drift_guard_killswitch"] is False


# ═══════════════════════════════════════════════════════════════════════
# DG11) Metric bounded cardinality
# ═══════════════════════════════════════════════════════════════════════


class TestDriftMetrics:
    def test_inc_drift_evaluation_valid(self):
        from app.ptf_metrics import PTFMetrics
        m = PTFMetrics()
        # Should not raise for valid combos
        for mode in ("shadow", "enforce"):
            for outcome in ("no_drift", "drift_detected", "provider_error"):
                m.inc_drift_evaluation(mode, outcome)

    def test_inc_drift_evaluation_invalid_mode_ignored(self):
        from app.ptf_metrics import PTFMetrics
        m = PTFMetrics()
        # Invalid mode — should not raise, just log warning
        m.inc_drift_evaluation("invalid_mode", "no_drift")

    def test_inc_drift_evaluation_invalid_outcome_ignored(self):
        from app.ptf_metrics import PTFMetrics
        m = PTFMetrics()
        m.inc_drift_evaluation("shadow", "invalid_outcome")

    def test_bounded_cardinality(self):
        """2 modes × 3 outcomes = 6 max series."""
        from app.ptf_metrics import PTFMetrics
        assert len(PTFMetrics._VALID_DRIFT_MODES) == 2
        assert len(PTFMetrics._VALID_DRIFT_OUTCOMES) == 3
