"""Faz H0 — Production Hardening testleri (unit + PBT).

Kapsam:
  - H1: rollout_config parse + fail-closed
  - H2: gate_evaluator (latency, mismatch, safety, unexpected_block, N_min)
  - H3: stage_report yapısı ve içerik doğrulaması
"""

from __future__ import annotations

import json
import logging
import os
from unittest import mock

import pytest

from app.invoice.validation.rollout_config import (
    RolloutConfig,
    _DEFAULT_MISMATCH_GATE_COUNT,
    _DEFAULT_N_MIN,
    load_rollout_config,
)
from app.invoice.validation.gate_evaluator import (
    GateDecision,
    GateResult,
    GateVerdict,
    check_n_min,
    evaluate_all_gates,
    evaluate_latency_gate,
    evaluate_mismatch_gate,
    evaluate_safety_gate,
    evaluate_unexpected_block_gate,
)
from app.invoice.validation.stage_report import (
    EnforcementSnapshot,
    LatencySnapshot,
    MetricsSnapshot,
    MismatchSnapshot,
    generate_stage_report,
    report_to_json,
    validate_report_structure,
)


# ===================================================================
# H1: Rollout Config — Unit Tests
# ===================================================================


class TestRolloutConfigParse:
    """rollout_config.py env var parse testleri."""

    def test_defaults_when_no_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = load_rollout_config()
        assert cfg.latency_gate_delta_ms is None
        assert cfg.mismatch_gate_count == _DEFAULT_MISMATCH_GATE_COUNT
        assert cfg.n_min == _DEFAULT_N_MIN
        assert cfg.rollout_stage is None

    def test_valid_values(self):
        env = {
            "INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS": "15.5",
            "INVOICE_VALIDATION_MISMATCH_GATE_COUNT": "3",
            "INVOICE_VALIDATION_GATE_N_MIN": "25",
            "INVOICE_VALIDATION_ROLLOUT_STAGE": "D1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.latency_gate_delta_ms == 15.5
        assert cfg.mismatch_gate_count == 3
        assert cfg.n_min == 25
        assert cfg.rollout_stage == "D1"

    def test_negative_delta_returns_none(self, caplog):
        env = {"INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS": "-5"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.latency_gate_delta_ms is None
        assert "pozitif olmalı" in caplog.text

    def test_non_numeric_delta_returns_none(self, caplog):
        env = {"INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS": "abc"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.latency_gate_delta_ms is None
        assert "geçersiz değer" in caplog.text

    def test_negative_mismatch_count_returns_default(self, caplog):
        env = {"INVOICE_VALIDATION_MISMATCH_GATE_COUNT": "-1"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.mismatch_gate_count == _DEFAULT_MISMATCH_GATE_COUNT
        assert "negatif olamaz" in caplog.text

    def test_non_numeric_n_min_returns_default(self, caplog):
        env = {"INVOICE_VALIDATION_GATE_N_MIN": "xyz"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.n_min == _DEFAULT_N_MIN
        assert "geçersiz değer" in caplog.text

    def test_zero_n_min_returns_default(self, caplog):
        env = {"INVOICE_VALIDATION_GATE_N_MIN": "0"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.n_min == _DEFAULT_N_MIN

    def test_invalid_stage_returns_none(self, caplog):
        env = {"INVOICE_VALIDATION_ROLLOUT_STAGE": "D9"}
        with mock.patch.dict(os.environ, env, clear=True):
            with caplog.at_level(logging.WARNING, logger="app.invoice.validation.rollout_config"):
                cfg = load_rollout_config()
        assert cfg.rollout_stage is None
        assert "geçersiz stage" in caplog.text

    def test_stage_case_insensitive(self):
        env = {"INVOICE_VALIDATION_ROLLOUT_STAGE": "d2"}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.rollout_stage == "D2"

    def test_frozen_dataclass(self):
        cfg = RolloutConfig()
        with pytest.raises(AttributeError):
            cfg.n_min = 99  # type: ignore[misc]



# ===================================================================
# H2: Gate Evaluator — Unit Tests
# ===================================================================


class TestNMinCheck:
    def test_below_n_min_defers(self):
        r = check_n_min(15, 20)
        assert r.verdict == GateVerdict.DEFER
        assert "observed=15" in r.reasons[0]

    def test_at_n_min_passes(self):
        r = check_n_min(20, 20)
        assert r.verdict == GateVerdict.PASS

    def test_above_n_min_passes(self):
        r = check_n_min(50, 20)
        assert r.verdict == GateVerdict.PASS


class TestLatencyGate:
    def test_disabled_when_delta_none(self):
        r = evaluate_latency_gate(10.0, 20.0, 15.0, 25.0, delta_ms=None)
        assert r.verdict == GateVerdict.PASS
        assert "devre dışı" in r.reasons[0]

    def test_pass_within_budget(self):
        r = evaluate_latency_gate(10.0, 20.0, 15.0, 25.0, delta_ms=10.0)
        assert r.verdict == GateVerdict.PASS

    def test_fail_p95_exceeded(self):
        r = evaluate_latency_gate(10.0, 20.0, 25.0, 25.0, delta_ms=10.0)
        assert r.verdict == GateVerdict.FAIL
        assert any("P95" in reason for reason in r.reasons)

    def test_fail_p99_exceeded(self):
        r = evaluate_latency_gate(10.0, 20.0, 15.0, 35.0, delta_ms=10.0)
        assert r.verdict == GateVerdict.FAIL
        assert any("P99" in reason for reason in r.reasons)

    def test_fail_both_exceeded(self):
        r = evaluate_latency_gate(10.0, 20.0, 25.0, 35.0, delta_ms=10.0)
        assert r.verdict == GateVerdict.FAIL
        assert len(r.reasons) == 2

    def test_exact_boundary_passes(self):
        r = evaluate_latency_gate(10.0, 20.0, 20.0, 30.0, delta_ms=10.0)
        assert r.verdict == GateVerdict.PASS


class TestMismatchGate:
    def test_zero_actionable_passes(self):
        r = evaluate_mismatch_gate(0, threshold=0)
        assert r.verdict == GateVerdict.PASS

    def test_one_actionable_fails_default(self):
        r = evaluate_mismatch_gate(1, threshold=0)
        assert r.verdict == GateVerdict.FAIL

    def test_within_threshold_passes(self):
        r = evaluate_mismatch_gate(2, threshold=5)
        assert r.verdict == GateVerdict.PASS

    def test_disabled_when_threshold_none(self):
        r = evaluate_mismatch_gate(100, threshold=None)
        assert r.verdict == GateVerdict.PASS
        assert "devre dışı" in r.reasons[0]


class TestSafetyGate:
    def test_zero_retry_passes(self):
        r = evaluate_safety_gate(0)
        assert r.verdict == GateVerdict.PASS

    def test_nonzero_retry_fails(self):
        r = evaluate_safety_gate(3)
        assert r.verdict == GateVerdict.FAIL
        assert "retry_loop_count=3" in r.reasons[0]


class TestUnexpectedBlockGate:
    def test_zero_unexpected_passes(self):
        r = evaluate_unexpected_block_gate(0)
        assert r.verdict == GateVerdict.PASS

    def test_one_unexpected_fails_default(self):
        r = evaluate_unexpected_block_gate(1)
        assert r.verdict == GateVerdict.FAIL

    def test_within_threshold_passes(self):
        r = evaluate_unexpected_block_gate(1, threshold=2)
        assert r.verdict == GateVerdict.PASS


class TestEvaluateAllGates:
    _BASE_KWARGS = dict(
        observed_count=25,
        n_min=20,
        baseline_p95=10.0,
        baseline_p99=20.0,
        current_p95=12.0,
        current_p99=22.0,
        delta_ms=15.0,
        actionable_mismatch_count=0,
        mismatch_threshold=0,
        retry_loop_count=0,
        unexpected_block_count=0,
    )

    def test_all_pass(self):
        d = evaluate_all_gates(**self._BASE_KWARGS)
        assert d.overall == GateVerdict.PASS
        assert len(d.results) == 5  # n_min, latency, mismatch, safety, unexpected

    def test_n_min_defer_propagates_all(self):
        kwargs = {**self._BASE_KWARGS, "observed_count": 10}
        d = evaluate_all_gates(**kwargs)
        assert d.overall == GateVerdict.DEFER
        assert len(d.results) == 5
        for r in d.results:
            assert r.verdict == GateVerdict.DEFER

    def test_safety_fail_overrides(self):
        kwargs = {**self._BASE_KWARGS, "retry_loop_count": 1}
        d = evaluate_all_gates(**kwargs)
        assert d.overall == GateVerdict.FAIL

    def test_unexpected_block_fail(self):
        kwargs = {**self._BASE_KWARGS, "unexpected_block_count": 1}
        d = evaluate_all_gates(**kwargs)
        assert d.overall == GateVerdict.FAIL

    def test_mismatch_disabled(self):
        kwargs = {**self._BASE_KWARGS, "actionable_mismatch_count": 99, "mismatch_threshold": None}
        d = evaluate_all_gates(**kwargs)
        mismatch_r = [r for r in d.results if r.gate == "mismatch"][0]
        assert mismatch_r.verdict == GateVerdict.PASS

    def test_gate_decision_overall_priority(self):
        """FAIL > DEFER > PASS."""
        d = GateDecision(results=(
            GateResult(gate="a", verdict=GateVerdict.PASS),
            GateResult(gate="b", verdict=GateVerdict.DEFER),
            GateResult(gate="c", verdict=GateVerdict.FAIL),
        ))
        assert d.overall == GateVerdict.FAIL


# ===================================================================
# H3: Stage Report — Unit Tests
# ===================================================================


class TestStageReport:
    def _make_metrics(self) -> MetricsSnapshot:
        return MetricsSnapshot(
            latency=LatencySnapshot(total_p95_ms=12.0, total_p99_ms=22.0),
            mismatch=MismatchSnapshot(actionable_count=0, whitelisted_count=3),
            enforcement=EnforcementSnapshot(soft_warn_count=1, hard_block_count=0),
        )

    def _make_gate_decision(self) -> GateDecision:
        return GateDecision(results=(
            GateResult(gate="n_min", verdict=GateVerdict.PASS),
            GateResult(gate="latency", verdict=GateVerdict.PASS),
            GateResult(gate="mismatch", verdict=GateVerdict.PASS),
            GateResult(gate="safety", verdict=GateVerdict.PASS),
            GateResult(gate="unexpected_block", verdict=GateVerdict.PASS),
        ))

    def test_report_has_required_fields(self):
        report = generate_stage_report(
            stage="D0",
            observation_days=7,
            total_invoices=35,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        assert validate_report_structure(report)

    def test_report_json_serializable(self):
        report = generate_stage_report(
            stage="D1",
            observation_days=7,
            total_invoices=38,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        json_str = report_to_json(report)
        parsed = json.loads(json_str)
        assert parsed["stage"] == "D1"
        assert parsed["total_invoices"] == 38

    def test_report_contains_gate_results(self):
        report = generate_stage_report(
            stage="D2",
            observation_days=7,
            total_invoices=40,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        assert report["gate_decision"]["overall"] == "pass"
        assert len(report["gate_decision"]["gates"]) == 5

    def test_report_contains_latency_values(self):
        report = generate_stage_report(
            stage="D0",
            observation_days=7,
            total_invoices=35,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        assert report["latency"]["total_p95_ms"] == 12.0
        assert report["latency"]["total_p99_ms"] == 22.0

    def test_report_contains_mismatch_values(self):
        report = generate_stage_report(
            stage="D0",
            observation_days=7,
            total_invoices=35,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        assert report["mismatch"]["actionable_count"] == 0
        assert report["mismatch"]["whitelisted_count"] == 3

    def test_report_no_pii(self):
        """Rapor fatura ID'si veya PII içermemeli."""
        report = generate_stage_report(
            stage="D0",
            observation_days=7,
            total_invoices=35,
            metrics=self._make_metrics(),
            gate_decision=self._make_gate_decision(),
        )
        json_str = report_to_json(report)
        # Raporda invoice_id, name, email gibi PII alanları olmamalı
        for pii_key in ["invoice_id", "customer_name", "email", "phone"]:
            assert pii_key not in json_str


# ===================================================================
# PBT — Property-Based Tests
# ===================================================================

from hypothesis import given, settings, strategies as st


class TestPBTRolloutConfig:
    """PBT: config parse round-trip — rastgele string → never raise, always valid default."""

    @settings(max_examples=100)
    @given(st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=50))
    def test_delta_parse_never_raises(self, raw: str):
        env = {"INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS": raw}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.latency_gate_delta_ms is None or cfg.latency_gate_delta_ms > 0

    @settings(max_examples=100)
    @given(st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=50))
    def test_n_min_parse_never_raises(self, raw: str):
        env = {"INVOICE_VALIDATION_GATE_N_MIN": raw}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.n_min > 0

    @settings(max_examples=100)
    @given(st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=50))
    def test_mismatch_count_parse_never_raises(self, raw: str):
        env = {"INVOICE_VALIDATION_MISMATCH_GATE_COUNT": raw}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.mismatch_gate_count >= 0

    @settings(max_examples=100)
    @given(st.text(alphabet=st.characters(blacklist_characters="\x00"), max_size=50))
    def test_stage_parse_never_raises(self, raw: str):
        env = {"INVOICE_VALIDATION_ROLLOUT_STAGE": raw}
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = load_rollout_config()
        assert cfg.rollout_stage is None or cfg.rollout_stage in {"D0", "D1", "D2"}


class TestPBTGateEvaluator:
    """PBT: gate evaluator properties."""

    @settings(max_examples=100)
    @given(
        delta=st.floats(min_value=0.1, max_value=1000.0),
        extra=st.floats(min_value=0.0, max_value=500.0),
    )
    def test_latency_monotonicity(self, delta: float, extra: float):
        """Delta artarsa pass olasılığı azalmaz."""
        baseline = 10.0
        current = baseline + extra
        r_small = evaluate_latency_gate(baseline, baseline, current, current, delta_ms=delta)
        r_large = evaluate_latency_gate(baseline, baseline, current, current, delta_ms=delta + 100.0)
        if r_large.verdict == GateVerdict.FAIL:
            assert r_small.verdict == GateVerdict.FAIL

    @settings(max_examples=100)
    @given(count=st.integers(min_value=0, max_value=100))
    def test_n_min_guard(self, count: int):
        """count < 20 → always DEFER."""
        r = check_n_min(count, 20)
        if count < 20:
            assert r.verdict == GateVerdict.DEFER
        else:
            assert r.verdict == GateVerdict.PASS

    @settings(max_examples=100)
    @given(
        observed=st.integers(min_value=0, max_value=100),
        actionable=st.integers(min_value=0, max_value=10),
        retry=st.integers(min_value=0, max_value=5),
        unexpected=st.integers(min_value=0, max_value=5),
    )
    def test_evaluate_all_never_raises(self, observed: int, actionable: int, retry: int, unexpected: int):
        """evaluate_all_gates hiçbir girdi kombinasyonunda exception atmaz."""
        d = evaluate_all_gates(
            observed_count=observed,
            n_min=20,
            baseline_p95=10.0,
            baseline_p99=20.0,
            current_p95=15.0,
            current_p99=25.0,
            delta_ms=10.0,
            actionable_mismatch_count=actionable,
            mismatch_threshold=0,
            retry_loop_count=retry,
            unexpected_block_count=unexpected,
        )
        assert d.overall in {GateVerdict.PASS, GateVerdict.FAIL, GateVerdict.DEFER}
        assert len(d.results) == 5


class TestPBTStageReport:
    """PBT: report structure invariant."""

    @settings(max_examples=100)
    @given(
        stage=st.sampled_from(["D0", "D1", "D2"]),
        days=st.integers(min_value=1, max_value=30),
        invoices=st.integers(min_value=0, max_value=500),
    )
    def test_report_always_has_required_fields(self, stage: str, days: int, invoices: int):
        metrics = MetricsSnapshot(
            latency=LatencySnapshot(total_p95_ms=10.0, total_p99_ms=20.0),
            mismatch=MismatchSnapshot(actionable_count=0, whitelisted_count=0),
            enforcement=EnforcementSnapshot(),
        )
        gate = GateDecision(results=(
            GateResult(gate="n_min", verdict=GateVerdict.PASS),
        ))
        report = generate_stage_report(stage, days, invoices, metrics, gate)
        assert validate_report_structure(report)
        json_str = report_to_json(report)
        parsed = json.loads(json_str)
        assert parsed["stage"] == stage
