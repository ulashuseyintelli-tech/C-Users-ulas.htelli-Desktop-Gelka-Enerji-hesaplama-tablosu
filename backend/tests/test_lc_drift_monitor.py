"""
PR-8: Drift Monitor tests.

- Empty window → no alert
- Normal distribution → no alert
- High abort rate → alert
- High override rate → alert
- Both thresholds exceeded → alert with both reasons
- PBT: abort_rate always in [0, 1]
- PBT: alert iff threshold exceeded
- PBT: snapshot deterministic
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import PolicyAction
from backend.app.testing.rollout_orchestrator import DriftMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decisions(promote: int = 0, abort: int = 0, hold: int = 0, degrade: int = 0):
    return (
        [PolicyAction.PROMOTE] * promote
        + [PolicyAction.ABORT] * abort
        + [PolicyAction.HOLD] * hold
        + [PolicyAction.DEGRADE] * degrade
    )


MONITOR = DriftMonitor(abort_rate_threshold=0.3, override_rate_threshold=0.2)


# ---------------------------------------------------------------------------
# Empty window
# ---------------------------------------------------------------------------

class TestEmptyWindow:
    def test_empty_no_alert(self):
        snap = MONITOR.snapshot([])
        assert snap.alert is False
        assert snap.total_decisions == 0


# ---------------------------------------------------------------------------
# Normal distribution
# ---------------------------------------------------------------------------

class TestNormalDistribution:
    def test_mostly_promote_no_alert(self):
        snap = MONITOR.snapshot(_decisions(promote=8, abort=1, hold=1))
        assert snap.alert is False
        assert snap.abort_rate == 0.1

    def test_all_promote_no_alert(self):
        snap = MONITOR.snapshot(_decisions(promote=20))
        assert snap.alert is False
        assert snap.promote_count == 20


# ---------------------------------------------------------------------------
# High abort rate
# ---------------------------------------------------------------------------

class TestHighAbortRate:
    def test_abort_rate_above_threshold_alerts(self):
        snap = MONITOR.snapshot(_decisions(promote=5, abort=5))
        assert snap.abort_rate == 0.5
        assert snap.alert is True
        assert "abort_rate" in snap.alert_reason

    def test_abort_rate_at_threshold_no_alert(self):
        snap = MONITOR.snapshot(_decisions(promote=7, abort=3))
        assert snap.abort_rate == 0.3
        assert snap.alert is False


# ---------------------------------------------------------------------------
# High override rate
# ---------------------------------------------------------------------------

class TestHighOverrideRate:
    def test_override_rate_above_threshold_alerts(self):
        decisions = _decisions(promote=10)
        snap = MONITOR.snapshot(decisions, override_count=3)
        assert snap.override_rate == 0.3
        assert snap.alert is True
        assert "override_rate" in snap.alert_reason

    def test_override_rate_at_threshold_no_alert(self):
        decisions = _decisions(promote=10)
        snap = MONITOR.snapshot(decisions, override_count=2)
        assert snap.override_rate == 0.2
        assert snap.alert is False


# ---------------------------------------------------------------------------
# Both thresholds
# ---------------------------------------------------------------------------

class TestBothThresholds:
    def test_both_exceeded_both_reasons(self):
        decisions = _decisions(promote=3, abort=7)
        snap = MONITOR.snapshot(decisions, override_count=5)
        assert snap.alert is True
        assert "abort_rate" in snap.alert_reason
        assert "override_rate" in snap.alert_reason


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

class TestCounts:
    def test_counts_match_input(self):
        decisions = _decisions(promote=5, abort=3, hold=2, degrade=1)
        snap = MONITOR.snapshot(decisions)
        assert snap.promote_count == 5
        assert snap.abort_count == 3
        assert snap.hold_count == 2
        assert snap.degrade_count == 1
        assert snap.total_decisions == 11


# ---------------------------------------------------------------------------
# PBT: abort_rate always in [0, 1]
# ---------------------------------------------------------------------------

class TestPbtAbortRateBounds:
    @given(
        promote=st.integers(min_value=0, max_value=50),
        abort=st.integers(min_value=0, max_value=50),
        hold=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_abort_rate_in_unit_interval(self, promote, abort, hold):
        decisions = _decisions(promote=promote, abort=abort, hold=hold)
        if not decisions:
            return
        snap = MONITOR.snapshot(decisions)
        assert 0.0 <= snap.abort_rate <= 1.0


# ---------------------------------------------------------------------------
# PBT: alert iff threshold exceeded
# ---------------------------------------------------------------------------

class TestPbtAlertIffThreshold:
    @given(
        promote=st.integers(min_value=0, max_value=50),
        abort=st.integers(min_value=0, max_value=50),
        overrides=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_alert_matches_threshold_logic(self, promote, abort, overrides):
        decisions = _decisions(promote=promote, abort=abort)
        if not decisions:
            return
        snap = MONITOR.snapshot(decisions, override_count=overrides)
        total = len(decisions)
        expected_abort_alert = (abort / total) > 0.3
        expected_override_alert = (overrides / total) > 0.2
        assert snap.alert == (expected_abort_alert or expected_override_alert)


# ---------------------------------------------------------------------------
# PBT: snapshot deterministic
# ---------------------------------------------------------------------------

class TestPbtSnapshotDeterministic:
    @given(
        promote=st.integers(min_value=0, max_value=20),
        abort=st.integers(min_value=0, max_value=20),
        hold=st.integers(min_value=0, max_value=20),
        overrides=st.integers(min_value=0, max_value=20),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_same_input_same_snapshot(self, promote, abort, hold, overrides):
        decisions = _decisions(promote=promote, abort=abort, hold=hold)
        s1 = MONITOR.snapshot(decisions, override_count=overrides)
        s2 = MONITOR.snapshot(decisions, override_count=overrides)
        assert s1 == s2
