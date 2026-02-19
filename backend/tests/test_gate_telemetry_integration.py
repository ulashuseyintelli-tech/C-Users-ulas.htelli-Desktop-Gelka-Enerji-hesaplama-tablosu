"""
PR-12 Task 3: End-to-end checkpoint — telemetry chain integration test.

Kanıtlar:
  1. ReleaseGate.check() metrikleri doğru emit ediyor
  2. MetricStore persist ediyor
  3. Exporter .prom üretiyor
  4. Fail-open metrik yazımı gate davranışını değiştirmiyor
  5. Audit fail-closed davranışı bozulmuyor

Validates: Requirements 1.1, 2.1, 3.1, 4.1, 5.4, 6.1-6.5
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.app.testing.gate_metrics import (
    GateMetricExporter,
    GateMetricStore,
)
from backend.app.testing.perf_budget import TestTier, TierRunResult
from backend.app.testing.policy_engine import AuditLog, OpsGateStatus
from backend.app.testing.release_gate import GateDecision, ReleaseGate, ReleaseOverride
from backend.app.testing.release_policy import (
    BlockReasonCode,
    ReleasePolicy,
    ReleasePolicyInput,
    ReleaseVerdict,
)
from backend.app.testing.rollout_orchestrator import (
    DriftSnapshot,
    PolicyCanaryResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLICY = ReleasePolicy()


def _clean_drift() -> DriftSnapshot:
    return DriftSnapshot(
        window_size=20, total_decisions=100,
        abort_count=0, promote_count=90, hold_count=5,
        degrade_count=0, override_count=5,
        abort_rate=0.0, override_rate=0.05,
        alert=False, alert_reason="",
    )


def _safe_canary() -> PolicyCanaryResult:
    return PolicyCanaryResult(
        old_version="v1", new_version="v2",
        total=100, safe=95, upgrade=5, breaking=0,
        guard_violations=0, recommendation="promote", reason="all safe",
    )


def _ok_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[TierRunResult(
            tier=TestTier.SMOKE, total_seconds=1.0, test_count=5,
            budget_seconds=10.0, passed=True, slowest=[],
        )],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


def _block_ops_gate_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[TierRunResult(
            tier=TestTier.SMOKE, total_seconds=1.0, test_count=5,
            budget_seconds=10.0, passed=True, slowest=[],
        )],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=False),
    )


def _hold_input() -> ReleasePolicyInput:
    return ReleasePolicyInput(
        tier_results=[TierRunResult(
            tier=TestTier.SMOKE, total_seconds=15.0, test_count=5,
            budget_seconds=10.0, passed=False, slowest=[],
        )],
        flake_snapshot=[],
        drift_snapshot=_clean_drift(),
        canary_result=_safe_canary(),
        ops_gate=OpsGateStatus(passed=True),
    )


# ===================================================================
# End-to-end: gate → store → exporter → prometheus
# ===================================================================

class TestEndToEndTelemetryChain:
    """Uçtan uca: check() → store counters → .prom çıktısı."""

    def test_allow_decision_emits_metrics_and_prom(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_ok_input())
        decision = gate.check(result)

        assert decision.allowed is True

        # Store: ALLOW +1
        assert store.decision_counts()["ALLOW"] == 1
        assert store.decision_counts()["DENY"] == 0

        # Prom output reflects the counter
        prom = GateMetricExporter.export_prometheus(store)
        assert 'release_gate_decision_total{decision="ALLOW"} 1' in prom
        assert 'release_gate_decision_total{decision="DENY"} 0' in prom

    def test_deny_decision_emits_metrics_with_reasons(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_block_ops_gate_input())
        decision = gate.check(result)

        assert decision.allowed is False

        # Store: DENY +1, OPS_GATE_FAIL reason +1
        assert store.decision_counts()["DENY"] == 1
        assert store.reason_counts()[BlockReasonCode.OPS_GATE_FAIL.value] >= 1

        # Prom output
        prom = GateMetricExporter.export_prometheus(store)
        assert 'release_gate_decision_total{decision="DENY"} 1' in prom
        assert f'release_gate_reason_total{{reason="{BlockReasonCode.OPS_GATE_FAIL.value}"}}' in prom

    def test_breach_path_emits_breach_counter(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_block_ops_gate_input())
        override = ReleaseOverride(
            ttl_seconds=3600, created_at_ms=0,
            scope="v2.4", reason="test", created_by="admin",
        )
        decision = gate.check(result, override=override, release_scope="v2.4", now_ms=1000)

        assert decision.allowed is False
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision.audit_detail

        # Store: breach +1
        assert store.breach_counts()["NO_OVERRIDE"] == 1

        # Prom output
        prom = GateMetricExporter.export_prometheus(store)
        assert 'release_gate_contract_breach_total{kind="NO_OVERRIDE"} 1' in prom

    def test_multiple_decisions_accumulate(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        # 3 ALLOW
        ok_result = POLICY.evaluate(_ok_input())
        for _ in range(3):
            gate.check(ok_result)

        # 2 DENY
        block_result = POLICY.evaluate(_block_ops_gate_input())
        for _ in range(2):
            gate.check(block_result)

        assert store.decision_counts() == {"ALLOW": 3, "DENY": 2}

        prom = GateMetricExporter.export_prometheus(store)
        assert 'release_gate_decision_total{decision="ALLOW"} 3' in prom
        assert 'release_gate_decision_total{decision="DENY"} 2' in prom

    def test_metric_write_failures_only_on_real_failure(self):
        """metric_write_failures_total yalnızca gerçek write fail'de artar."""
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        # Normal operations — no write failures
        gate.check(POLICY.evaluate(_ok_input()))
        gate.check(POLICY.evaluate(_block_ops_gate_input()))
        assert store.metric_write_failures() == 0

        prom = GateMetricExporter.export_prometheus(store)
        assert "release_gate_metric_write_failures_total 0" in prom


# ===================================================================
# Fail-open: metrik hatası gate kararını değiştirmiyor
# ===================================================================

class TestFailOpenMetricEmission:
    """Metrik store hata fırlatsa bile gate kararı aynı kalır."""

    def test_broken_store_does_not_change_allow_decision(self):
        store = GateMetricStore()
        gate_normal = ReleaseGate(metric_store=store)
        gate_broken = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_ok_input())

        # Normal decision
        decision_normal = gate_normal.check(result)

        # Break the store — record_decision raises
        with patch.object(store, "record_decision", side_effect=RuntimeError("boom")):
            decision_broken = gate_broken.check(result)

        # Same decision
        assert decision_normal.allowed == decision_broken.allowed
        assert decision_normal.verdict == decision_broken.verdict
        assert decision_normal.override_applied == decision_broken.override_applied

    def test_broken_store_does_not_change_deny_decision(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_block_ops_gate_input())

        decision_normal = gate.check(result)

        with patch.object(store, "record_decision", side_effect=RuntimeError("boom")):
            decision_broken = gate.check(result)

        assert decision_normal.allowed == decision_broken.allowed
        assert decision_normal.verdict == decision_broken.verdict

    def test_broken_breach_counter_does_not_change_decision(self):
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)

        result = POLICY.evaluate(_block_ops_gate_input())
        override = ReleaseOverride(
            ttl_seconds=3600, created_at_ms=0,
            scope="v2.4", reason="test", created_by="admin",
        )

        decision_normal = gate.check(result, override=override, release_scope="v2.4", now_ms=1000)

        with patch.object(store, "record_breach", side_effect=RuntimeError("boom")):
            decision_broken = gate.check(result, override=override, release_scope="v2.4", now_ms=1000)

        assert decision_normal.allowed == decision_broken.allowed
        assert "CONTRACT_BREACH_NO_OVERRIDE" in decision_broken.audit_detail

    def test_none_metric_store_works_fine(self):
        """metric_store=None → no metrics, no errors."""
        gate = ReleaseGate(metric_store=None)
        result = POLICY.evaluate(_ok_input())
        decision = gate.check(result)
        assert decision.allowed is True


# ===================================================================
# Audit fail-closed: audit yazım hatası davranışı bozulmuyor
# ===================================================================

class TestAuditFailClosedPreserved:
    """Telemetri eklenmesi audit fail-closed (R3) davranışını bozmaz."""

    def test_audit_failure_emits_counter(self):
        """Audit write fail → audit_write_failures +1."""
        store = GateMetricStore()
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit, metric_store=store)

        # Break audit recording
        with patch.object(audit, "record", side_effect=RuntimeError("disk full")):
            gate.check(POLICY.evaluate(_ok_input()))

        assert store.audit_write_failures() == 1

    def test_audit_success_no_counter(self):
        """Audit write success → audit_write_failures stays 0."""
        store = GateMetricStore()
        gate = ReleaseGate(metric_store=store)
        gate.check(POLICY.evaluate(_ok_input()))
        assert store.audit_write_failures() == 0

    def test_audit_failure_counter_in_prom(self):
        """audit_write_failures visible in prometheus output."""
        store = GateMetricStore()
        audit = AuditLog()
        gate = ReleaseGate(audit_log=audit, metric_store=store)

        with patch.object(audit, "record", side_effect=RuntimeError("disk full")):
            gate.check(POLICY.evaluate(_ok_input()))
            gate.check(POLICY.evaluate(_block_ops_gate_input()))

        prom = GateMetricExporter.export_prometheus(store)
        assert "release_gate_audit_write_failures_total 2" in prom
