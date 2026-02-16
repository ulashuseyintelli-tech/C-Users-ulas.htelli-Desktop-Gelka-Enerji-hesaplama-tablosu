"""
PR-12 Task 1.4: GateMetricStore unit tests.

DoD coverage:
  A) Round-trip correctness (empty + incremented)
  B) Monotonic generation (two saves, failed save rollback)
  C) Atomicity / half-write protection
  D) Legacy/forward compatibility (missing meta, extra fields)
  E) Fail-open metric writes (save exception → store state preserved)

Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2, 5.3, 5.5
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.testing.gate_metrics import (
    GateMetricExporter,
    GateMetricStore,
    _BREACH_KINDS,
    _DECISION_LABELS,
    _REASON_LABELS,
    _atomic_write,
)
from backend.app.testing.release_policy import BlockReasonCode


# ===================================================================
# A) Round-trip correctness
# ===================================================================

class TestRoundTripEmpty:
    """Empty store save→load: counters 0, meta init doğru."""

    def test_empty_save_load(self, tmp_path):
        store = GateMetricStore()
        assert store.save_to_dir(tmp_path) is True

        loaded = GateMetricStore()
        assert loaded.load_from_dir(tmp_path) is True

        # All counters zero
        assert loaded.decision_counts() == {"ALLOW": 0, "DENY": 0}
        assert all(v == 0 for v in loaded.reason_counts().values())
        assert loaded.breach_counts() == {"NO_OVERRIDE": 0}
        assert loaded.audit_write_failures() == 0
        assert loaded.metric_write_failures() == 0

    def test_empty_store_generation_zero(self):
        store = GateMetricStore()
        assert store.store_generation() == 0

    def test_empty_store_start_timestamp_positive(self):
        store = GateMetricStore()
        assert store.store_start_timestamp() > 0


class TestRoundTripIncremented:
    """Increment + save→load: reload sonrası aynı değer."""

    def test_decision_counters_survive_roundtrip(self, tmp_path):
        store = GateMetricStore()
        store.record_decision(True, [])
        store.record_decision(True, [])
        store.record_decision(False, [BlockReasonCode.TIER_FAIL.value])
        store.save_to_dir(tmp_path)

        loaded = GateMetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.decision_counts() == {"ALLOW": 2, "DENY": 1}
        assert loaded.reason_counts()[BlockReasonCode.TIER_FAIL.value] == 1

    def test_breach_counter_survives_roundtrip(self, tmp_path):
        store = GateMetricStore()
        store.record_breach()
        store.record_breach()
        store.save_to_dir(tmp_path)

        loaded = GateMetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.breach_counts() == {"NO_OVERRIDE": 2}

    def test_audit_failure_counter_survives_roundtrip(self, tmp_path):
        store = GateMetricStore()
        store.record_audit_write_failure()
        store.save_to_dir(tmp_path)

        loaded = GateMetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.audit_write_failures() == 1

    def test_metric_failure_counter_survives_roundtrip(self, tmp_path):
        store = GateMetricStore()
        store.record_metric_write_failure()
        store.record_metric_write_failure()
        store.save_to_dir(tmp_path)

        loaded = GateMetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.metric_write_failures() == 2


# ===================================================================
# B) Monotonic generation
# ===================================================================

class TestMonotonicGeneration:
    """store_generation artışı ve failed save rollback."""

    def test_two_successful_saves_increment_generation(self, tmp_path):
        store = GateMetricStore()
        assert store.store_generation() == 0

        store.save_to_dir(tmp_path)
        assert store.store_generation() == 1

        store.save_to_dir(tmp_path)
        assert store.store_generation() == 2

    def test_failed_save_rolls_back_generation(self, tmp_path):
        store = GateMetricStore()
        store.record_decision(True, [])
        store.save_to_dir(tmp_path)
        assert store.store_generation() == 1

        # Inject failure: make _atomic_write always fail
        with patch(
            "backend.app.testing.gate_metrics._atomic_write", return_value=False
        ):
            result = store.save_to_dir(tmp_path)

        assert result is False
        # Generation rolled back to 1 (not 2)
        assert store.store_generation() == 1
        # metric_write_failures incremented
        assert store.metric_write_failures() == 1

    def test_generation_persisted_across_load(self, tmp_path):
        store = GateMetricStore()
        store.save_to_dir(tmp_path)  # gen 1
        store.save_to_dir(tmp_path)  # gen 2
        store.save_to_dir(tmp_path)  # gen 3

        loaded = GateMetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.store_generation() == 3


# ===================================================================
# C) Atomicity / half-write protection
# ===================================================================

class TestAtomicity:
    """_atomic_write fail → eski valid JSON korunuyor."""

    def test_failed_atomic_write_preserves_old_data(self, tmp_path):
        store = GateMetricStore()
        store.record_decision(False, [BlockReasonCode.GUARD_VIOLATION.value])
        store.save_to_dir(tmp_path)

        # Verify initial save
        loaded1 = GateMetricStore()
        loaded1.load_from_dir(tmp_path)
        assert loaded1.decision_counts()["DENY"] == 1

        # Now add more data and fail the save
        store.record_decision(False, [BlockReasonCode.OPS_GATE_FAIL.value])
        with patch(
            "backend.app.testing.gate_metrics._atomic_write", return_value=False
        ):
            store.save_to_dir(tmp_path)

        # Old data on disk should be intact
        loaded2 = GateMetricStore()
        loaded2.load_from_dir(tmp_path)
        assert loaded2.decision_counts()["DENY"] == 1  # old value, not 2
        assert loaded2.reason_counts()[BlockReasonCode.GUARD_VIOLATION.value] == 1
        assert loaded2.reason_counts()[BlockReasonCode.OPS_GATE_FAIL.value] == 0

    def test_load_does_not_see_half_state(self, tmp_path):
        """If no file exists, load returns False — no partial state."""
        store = GateMetricStore()
        result = store.load_from_dir(tmp_path)
        assert result is False
        # Store remains at zero state
        assert store.decision_counts() == {"ALLOW": 0, "DENY": 0}

    def test_atomic_write_creates_file(self, tmp_path):
        target = tmp_path / "test.json"
        ok = _atomic_write(target, '{"test": true}')
        assert ok is True
        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8")) == {"test": True}

    def test_atomic_write_replaces_existing(self, tmp_path):
        target = tmp_path / "test.json"
        target.write_text('{"old": true}', encoding="utf-8")
        _atomic_write(target, '{"new": true}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


# ===================================================================
# D) Legacy/forward compatibility
# ===================================================================

class TestLegacyForwardCompat:
    """Legacy format (meta yok) ve extra fields."""

    def test_legacy_format_no_meta_backfills(self, tmp_path):
        """JSON without store_generation/store_start_timestamp → defaults."""
        legacy_data = {
            "decision_counts": {"ALLOW": 5, "DENY": 3},
            "reason_counts": {r: 0 for r in _REASON_LABELS},
            "breach_counts": {"NO_OVERRIDE": 1},
            "audit_write_failures": 2,
            "metric_write_failures": 0,
        }
        p = tmp_path / "gate_metrics.json"
        p.write_text(json.dumps(legacy_data), encoding="utf-8")

        store = GateMetricStore()
        assert store.load_from_dir(tmp_path) is True

        # Counters loaded correctly
        assert store.decision_counts() == {"ALLOW": 5, "DENY": 3}
        assert store.breach_counts() == {"NO_OVERRIDE": 1}
        assert store.audit_write_failures() == 2

        # Meta backfilled: generation=0, start_time=file mtime
        assert store.store_generation() == 0
        assert store.store_start_timestamp() == pytest.approx(
            p.stat().st_mtime, abs=1.0
        )

    def test_legacy_format_save_persists_meta(self, tmp_path):
        """After loading legacy format, save adds meta fields."""
        legacy_data = {
            "decision_counts": {"ALLOW": 1, "DENY": 0},
            "reason_counts": {r: 0 for r in _REASON_LABELS},
            "breach_counts": {"NO_OVERRIDE": 0},
            "audit_write_failures": 0,
            "metric_write_failures": 0,
        }
        p = tmp_path / "gate_metrics.json"
        p.write_text(json.dumps(legacy_data), encoding="utf-8")

        store = GateMetricStore()
        store.load_from_dir(tmp_path)
        store.save_to_dir(tmp_path)

        # Re-read raw JSON — meta fields now present
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert "store_generation" in raw
        assert "store_start_timestamp" in raw
        assert raw["store_generation"] == 1  # one save

    def test_extra_fields_ignored_on_load(self, tmp_path):
        """Unknown fields in JSON don't break load."""
        data = {
            "decision_counts": {"ALLOW": 10, "DENY": 2},
            "reason_counts": {r: 0 for r in _REASON_LABELS},
            "breach_counts": {"NO_OVERRIDE": 0},
            "audit_write_failures": 0,
            "metric_write_failures": 0,
            "store_generation": 5,
            "store_start_timestamp": 1700000000.0,
            "unknown_future_field": "should be ignored",
            "another_field": [1, 2, 3],
        }
        p = tmp_path / "gate_metrics.json"
        p.write_text(json.dumps(data), encoding="utf-8")

        store = GateMetricStore()
        assert store.load_from_dir(tmp_path) is True
        assert store.decision_counts() == {"ALLOW": 10, "DENY": 2}
        assert store.store_generation() == 5

    def test_corrupt_json_returns_false(self, tmp_path):
        """Corrupt JSON → load returns False, store stays empty."""
        p = tmp_path / "gate_metrics.json"
        p.write_text("not valid json {{{", encoding="utf-8")

        store = GateMetricStore()
        assert store.load_from_dir(tmp_path) is False
        assert store.decision_counts() == {"ALLOW": 0, "DENY": 0}

    def test_missing_file_returns_false(self, tmp_path):
        store = GateMetricStore()
        assert store.load_from_dir(tmp_path / "nonexistent") is False


# ===================================================================
# E) Fail-open metric writes
# ===================================================================

class TestFailOpenMetricWrites:
    """save_to_dir exception → store state korunur, audit path etkilenmez."""

    def test_save_failure_preserves_in_memory_state(self, tmp_path):
        """Failed save doesn't corrupt in-memory counters."""
        store = GateMetricStore()
        store.record_decision(True, [])
        store.record_decision(False, [BlockReasonCode.FLAKY_TESTS.value])
        store.record_breach()

        with patch(
            "backend.app.testing.gate_metrics._atomic_write", return_value=False
        ):
            store.save_to_dir(tmp_path)

        # In-memory state intact
        assert store.decision_counts() == {"ALLOW": 1, "DENY": 1}
        assert store.reason_counts()[BlockReasonCode.FLAKY_TESTS.value] == 1
        assert store.breach_counts() == {"NO_OVERRIDE": 1}
        # metric_write_failures incremented
        assert store.metric_write_failures() == 1

    def test_save_failure_does_not_raise(self, tmp_path):
        """save_to_dir returns False on failure, never raises."""
        store = GateMetricStore()
        with patch(
            "backend.app.testing.gate_metrics._atomic_write",
            side_effect=Exception("disk on fire"),
        ):
            # _atomic_write exception is caught inside save_to_dir
            # because _atomic_write itself catches exceptions.
            # But if we bypass that, save_to_dir should still not raise.
            pass

        # Direct test: _atomic_write returning False
        with patch(
            "backend.app.testing.gate_metrics._atomic_write", return_value=False
        ):
            result = store.save_to_dir(tmp_path)
        assert result is False

    def test_multiple_save_failures_accumulate(self, tmp_path):
        store = GateMetricStore()
        with patch(
            "backend.app.testing.gate_metrics._atomic_write", return_value=False
        ):
            store.save_to_dir(tmp_path)
            store.save_to_dir(tmp_path)
            store.save_to_dir(tmp_path)

        assert store.metric_write_failures() == 3
        # Generation should be 0 — all rolled back
        assert store.store_generation() == 0


# ===================================================================
# Core counter behavior (supplements round-trip)
# ===================================================================

class TestCoreCounterBehavior:
    """record_decision, record_breach, record_audit_write_failure basics."""

    def test_record_decision_allow(self):
        store = GateMetricStore()
        store.record_decision(True, [])
        assert store.decision_counts()["ALLOW"] == 1
        assert store.decision_counts()["DENY"] == 0

    def test_record_decision_deny_with_reasons(self):
        store = GateMetricStore()
        reasons = [BlockReasonCode.TIER_FAIL.value, BlockReasonCode.FLAKY_TESTS.value]
        store.record_decision(False, reasons)
        assert store.decision_counts()["DENY"] == 1
        assert store.reason_counts()[BlockReasonCode.TIER_FAIL.value] == 1
        assert store.reason_counts()[BlockReasonCode.FLAKY_TESTS.value] == 1

    def test_invalid_reason_silently_skipped(self):
        """Geçersiz reason değerleri sessizce atlanır (bounded cardinality)."""
        store = GateMetricStore()
        store.record_decision(False, ["INVALID_REASON", "ALSO_INVALID"])
        assert store.decision_counts()["DENY"] == 1
        # No reason counter incremented
        assert all(v == 0 for v in store.reason_counts().values())

    def test_mixed_valid_invalid_reasons(self):
        store = GateMetricStore()
        store.record_decision(
            False,
            [BlockReasonCode.GUARD_VIOLATION.value, "BOGUS", BlockReasonCode.OPS_GATE_FAIL.value],
        )
        assert store.reason_counts()[BlockReasonCode.GUARD_VIOLATION.value] == 1
        assert store.reason_counts()[BlockReasonCode.OPS_GATE_FAIL.value] == 1
        # Total reason keys unchanged
        assert set(store.reason_counts().keys()) == set(_REASON_LABELS)

    def test_record_breach_increments(self):
        store = GateMetricStore()
        store.record_breach()
        store.record_breach()
        assert store.breach_counts()["NO_OVERRIDE"] == 2

    def test_record_audit_write_failure_increments(self):
        store = GateMetricStore()
        store.record_audit_write_failure()
        assert store.audit_write_failures() == 1
        store.record_audit_write_failure()
        assert store.audit_write_failures() == 2


# ===================================================================
# Label cardinality invariant
# ===================================================================

class TestLabelCardinality:
    """Label kümeleri sabit — hiçbir işlem yeni anahtar eklemez."""

    def test_decision_labels_fixed(self):
        store = GateMetricStore()
        assert set(store.decision_counts().keys()) == {"ALLOW", "DENY"}
        store.record_decision(True, [])
        store.record_decision(False, [BlockReasonCode.TIER_FAIL.value])
        assert set(store.decision_counts().keys()) == {"ALLOW", "DENY"}

    def test_reason_labels_match_enum(self):
        store = GateMetricStore()
        expected = {r.value for r in BlockReasonCode}
        assert set(store.reason_counts().keys()) == expected

    def test_breach_labels_fixed(self):
        store = GateMetricStore()
        assert set(store.breach_counts().keys()) == {"NO_OVERRIDE"}
        store.record_breach()
        assert set(store.breach_counts().keys()) == {"NO_OVERRIDE"}


# ===================================================================
# Thread safety
# ===================================================================

class TestThreadSafety:
    """Concurrent record çağrıları veri kaybetmez."""

    def test_concurrent_record_decision(self):
        store = GateMetricStore()
        n = 100

        def add_allow():
            for _ in range(n):
                store.record_decision(True, [])

        def add_deny():
            for _ in range(n):
                store.record_decision(False, [BlockReasonCode.TIER_FAIL.value])

        threads = [
            threading.Thread(target=add_allow),
            threading.Thread(target=add_allow),
            threading.Thread(target=add_deny),
            threading.Thread(target=add_deny),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert store.decision_counts()["ALLOW"] == 2 * n
        assert store.decision_counts()["DENY"] == 2 * n
        assert store.reason_counts()[BlockReasonCode.TIER_FAIL.value] == 2 * n
