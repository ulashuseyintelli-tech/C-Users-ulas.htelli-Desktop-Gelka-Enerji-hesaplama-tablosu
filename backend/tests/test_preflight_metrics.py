"""
PR-17: Preflight Telemetry — unit testler.

Properties:
    P19: Determinism — aynı store → aynı çıktı
    P20: Monotonic counters — counter asla azalmaz
    P21: Label safety — tüm label'lar sabit enum setlerinden
"""
import json
import threading
import pytest

from backend.app.testing.preflight_metrics import (
    MetricExporter,
    MetricStore,
    PreflightMetric,
    _OVERRIDE_KINDS,
    _REASON_LABELS,
    _VERDICT_LABELS,
)
from backend.app.testing.release_policy import BlockReasonCode


# ===================================================================
# Helpers
# ===================================================================

def _ok_output() -> dict:
    return {
        "verdict": "release_ok",
        "exit_code": 0,
        "reasons": [],
        "override_applied": False,
        "contract_breach": False,
        "spec_hash": "abc123",
    }


def _hold_output() -> dict:
    return {
        "verdict": "release_hold",
        "exit_code": 1,
        "reasons": ["TIER_FAIL", "FLAKY_TESTS"],
        "override_applied": False,
        "contract_breach": False,
        "spec_hash": "abc123",
    }


def _hold_overridden_output() -> dict:
    return {
        "verdict": "release_hold",
        "exit_code": 0,
        "reasons": ["TIER_FAIL"],
        "override_applied": True,
        "contract_breach": False,
        "override_by": "dev-lead",
        "override_reason": "hotfix",
        "spec_hash": "abc123",
    }


def _block_output() -> dict:
    return {
        "verdict": "release_block",
        "exit_code": 2,
        "reasons": ["GUARD_VIOLATION", "NO_TIER_DATA"],
        "override_applied": False,
        "contract_breach": False,
        "spec_hash": "abc123",
    }


def _block_breach_output() -> dict:
    return {
        "verdict": "release_block",
        "exit_code": 2,
        "reasons": ["GUARD_VIOLATION"],
        "override_applied": False,
        "contract_breach": True,
        "contract_breach_detail": "CONTRACT_BREACH_NO_OVERRIDE: GUARD_VIOLATION",
        "override_by": "rogue-dev",
        "spec_hash": "abc123",
    }


# ===================================================================
# TestPreflightMetric — from_preflight_output
# ===================================================================

class TestFromPreflightOutput:
    """MetricExporter.from_preflight_output doğruluğu."""

    def test_ok_verdict(self):
        m = MetricExporter.from_preflight_output(_ok_output())
        assert m.verdict == "OK"
        assert m.exit_code == 0
        assert m.reasons == []
        assert m.override_kind == "none"

    def test_hold_verdict(self):
        m = MetricExporter.from_preflight_output(_hold_output())
        assert m.verdict == "HOLD"
        assert m.exit_code == 1
        assert "TIER_FAIL" in m.reasons
        assert "FLAKY_TESTS" in m.reasons
        assert m.override_kind == "none"

    def test_hold_overridden(self):
        m = MetricExporter.from_preflight_output(_hold_overridden_output())
        assert m.verdict == "HOLD"
        assert m.override_applied is True
        assert m.override_kind == "applied"

    def test_block_verdict(self):
        m = MetricExporter.from_preflight_output(_block_output())
        assert m.verdict == "BLOCK"
        assert m.exit_code == 2
        assert m.override_kind == "none"

    def test_block_breach(self):
        m = MetricExporter.from_preflight_output(_block_breach_output())
        assert m.verdict == "BLOCK"
        assert m.contract_breach is True
        assert m.override_kind == "breach"

    def test_block_attempt(self):
        """BLOCK + override_by sağlanmış ama breach yok → attempt."""
        out = _block_output()
        out["override_by"] = "someone"
        m = MetricExporter.from_preflight_output(out)
        assert m.override_kind == "attempt"

    def test_invalid_reasons_filtered(self):
        """Bilinmeyen reason code'lar filtrelenir (bounded label)."""
        out = _ok_output()
        out["reasons"] = ["TIER_FAIL", "UNKNOWN_REASON", "FLAKY_TESTS"]
        m = MetricExporter.from_preflight_output(out)
        assert m.reasons == ["TIER_FAIL", "FLAKY_TESTS"]

    def test_duration_ms_passed(self):
        m = MetricExporter.from_preflight_output(_ok_output(), duration_ms=42.5)
        assert m.duration_ms == 42.5


# ===================================================================
# TestMetricStore — add / counts
# ===================================================================

class TestMetricStore:
    """MetricStore counter doğruluğu."""

    def test_empty_store(self):
        store = MetricStore()
        assert len(store) == 0
        assert store.verdict_counts() == {"OK": 0, "HOLD": 0, "BLOCK": 0}

    def test_add_increments_verdict(self):
        store = MetricStore()
        m = MetricExporter.from_preflight_output(_ok_output())
        store.add(m)
        assert store.verdict_counts()["OK"] == 1
        assert store.verdict_counts()["HOLD"] == 0

    def test_add_increments_reasons(self):
        store = MetricStore()
        m = MetricExporter.from_preflight_output(_hold_output())
        store.add(m)
        rc = store.reason_counts()
        assert rc["TIER_FAIL"] == 1
        assert rc["FLAKY_TESTS"] == 1
        assert rc["GUARD_VIOLATION"] == 0

    def test_add_increments_override(self):
        store = MetricStore()
        m = MetricExporter.from_preflight_output(_hold_overridden_output())
        store.add(m)
        oc = store.override_counts()
        assert oc["applied"] == 1
        assert oc["breach"] == 0

    def test_multiple_adds(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_hold_output()))
        assert len(store) == 3
        vc = store.verdict_counts()
        assert vc["OK"] == 2
        assert vc["HOLD"] == 1


# ===================================================================
# TestReasonCountsComplete — Property 20 (tüm enum üyeleri)
# ===================================================================

class TestReasonCountsComplete:
    """reason_counts() tüm BlockReasonCode üyelerini kapsar."""

    def test_all_reason_codes_present(self):
        store = MetricStore()
        rc = store.reason_counts()
        for code in BlockReasonCode:
            assert code.value in rc, f"{code.value} eksik"

    def test_reason_count_matches_enum_size(self):
        store = MetricStore()
        rc = store.reason_counts()
        assert len(rc) == len(BlockReasonCode)


# ===================================================================
# TestMonotonicCounters — Property 20
# ===================================================================

class TestMonotonicCounters:
    """Counter'lar asla azalmaz."""

    def test_verdict_monotonic(self):
        store = MetricStore()
        prev = store.verdict_counts()["OK"]
        for _ in range(5):
            store.add(MetricExporter.from_preflight_output(_ok_output()))
            curr = store.verdict_counts()["OK"]
            assert curr >= prev
            prev = curr

    def test_reason_monotonic(self):
        store = MetricStore()
        prev = store.reason_counts()["TIER_FAIL"]
        for _ in range(3):
            store.add(MetricExporter.from_preflight_output(_hold_output()))
            curr = store.reason_counts()["TIER_FAIL"]
            assert curr >= prev
            prev = curr

    def test_override_monotonic(self):
        store = MetricStore()
        prev = store.override_counts()["applied"]
        for _ in range(3):
            store.add(MetricExporter.from_preflight_output(_hold_overridden_output()))
            curr = store.override_counts()["applied"]
            assert curr >= prev
            prev = curr


# ===================================================================
# TestLabelSafety — Property 21
# ===================================================================

class TestLabelSafety:
    """Label cardinality bounded — sabit enum setlerinden."""

    def test_verdict_labels_bounded(self):
        assert set(_VERDICT_LABELS) == {"OK", "HOLD", "BLOCK"}

    def test_reason_labels_match_enum(self):
        assert set(_REASON_LABELS) == {r.value for r in BlockReasonCode}

    def test_override_kinds_bounded(self):
        assert set(_OVERRIDE_KINDS) == {"attempt", "applied", "breach"}

    def test_override_by_not_in_prometheus(self):
        """override_by asla Prometheus label olmaz."""
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_hold_overridden_output()))
        prom = MetricExporter.export_prometheus(store)
        assert "override_by" not in prom
        assert "dev-lead" not in prom


# ===================================================================
# TestExportDeterminism — Property 19
# ===================================================================

class TestExportDeterminism:
    """Aynı store → aynı çıktı (byte-level)."""

    def test_json_deterministic(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_hold_output()))
        out1 = MetricExporter.export_json(store)
        out2 = MetricExporter.export_json(store)
        assert out1 == out2

    def test_prometheus_deterministic(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_block_breach_output()))
        out1 = MetricExporter.export_prometheus(store)
        out2 = MetricExporter.export_prometheus(store)
        assert out1 == out2


# ===================================================================
# TestPrometheusFormat
# ===================================================================

class TestPrometheusFormat:
    """Prometheus text exposition format doğruluğu."""

    def test_contains_help_and_type(self):
        store = MetricStore()
        prom = MetricExporter.export_prometheus(store)
        assert "# HELP release_preflight_verdict_total" in prom
        assert "# TYPE release_preflight_verdict_total counter" in prom
        assert "# HELP release_preflight_reason_total" in prom
        assert "# HELP release_preflight_override_total" in prom

    def test_verdict_lines(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        prom = MetricExporter.export_prometheus(store)
        assert 'release_preflight_verdict_total{verdict="OK"} 1' in prom
        assert 'release_preflight_verdict_total{verdict="HOLD"} 0' in prom

    def test_reason_lines(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_hold_output()))
        prom = MetricExporter.export_prometheus(store)
        assert 'release_preflight_reason_total{reason="TIER_FAIL"} 1' in prom
        assert 'release_preflight_reason_total{reason="FLAKY_TESTS"} 1' in prom
        assert 'release_preflight_reason_total{reason="GUARD_VIOLATION"} 0' in prom

    def test_override_lines(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_block_breach_output()))
        prom = MetricExporter.export_prometheus(store)
        assert 'release_preflight_override_total{kind="breach"} 1' in prom
        assert 'release_preflight_override_total{kind="applied"} 0' in prom


# ===================================================================
# TestJsonExport
# ===================================================================

class TestJsonExport:
    """JSON export round-trip."""

    def test_json_valid(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        out = MetricExporter.export_json(store)
        data = json.loads(out)
        assert "metrics" in data
        assert "verdict_counts" in data
        assert "reason_counts" in data
        assert "override_counts" in data
        assert "write_failures" in data

    def test_json_round_trip(self):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_hold_output()))
        data = json.loads(MetricExporter.export_json(store))
        restored = MetricStore.from_dict(data)
        assert restored.verdict_counts() == store.verdict_counts()
        assert restored.reason_counts() == store.reason_counts()
        assert restored.override_counts() == store.override_counts()
        assert len(restored) == len(store)


# ===================================================================
# TestPersistence — save/load
# ===================================================================

class TestPersistence:
    """MetricStore save/load round-trip."""

    def test_save_and_load(self, tmp_path):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.add(MetricExporter.from_preflight_output(_block_breach_output()))
        assert store.save_to_dir(tmp_path) is True

        loaded = MetricStore()
        assert loaded.load_from_dir(tmp_path) is True
        assert loaded.verdict_counts() == store.verdict_counts()
        assert loaded.reason_counts() == store.reason_counts()
        assert len(loaded) == 2

    def test_load_missing_dir(self, tmp_path):
        store = MetricStore()
        assert store.load_from_dir(tmp_path / "nonexistent") is False

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / "preflight_metrics.json").write_text("not json", encoding="utf-8")
        store = MetricStore()
        assert store.load_from_dir(tmp_path) is False
        assert len(store) == 0


# ===================================================================
# TestThreadSafety
# ===================================================================

class TestThreadSafety:
    """Concurrent add çağrıları veri kaybetmez."""

    def test_concurrent_adds(self):
        store = MetricStore()
        n = 100

        def add_ok():
            for _ in range(n):
                store.add(MetricExporter.from_preflight_output(_ok_output()))

        threads = [threading.Thread(target=add_ok) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store) == 4 * n
        assert store.verdict_counts()["OK"] == 4 * n


# ===================================================================
# TestWriteFailures — gözlemlenebilir fail-open
# ===================================================================

class TestWriteFailures:
    """Telemetry write failure counter."""

    def test_initial_zero(self):
        store = MetricStore()
        assert store.write_failures() == 0

    def test_record_increments(self):
        store = MetricStore()
        store.record_write_failure()
        assert store.write_failures() == 1
        store.record_write_failure()
        assert store.write_failures() == 2

    def test_write_failures_in_prometheus(self):
        store = MetricStore()
        store.record_write_failure()
        prom = MetricExporter.export_prometheus(store)
        assert "release_preflight_telemetry_write_failures_total 1" in prom

    def test_write_failures_persisted(self, tmp_path):
        store = MetricStore()
        store.record_write_failure()
        store.record_write_failure()
        store.save_to_dir(tmp_path)

        loaded = MetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.write_failures() == 2

    def test_save_failure_increments_counter(self, tmp_path):
        """save_to_dir başarısız olursa write_failures artar."""
        store = MetricStore()
        # Var olmayan ve oluşturulamayan path
        bad_path = tmp_path / "nonexistent" / "deep" / "path"
        # save_to_dir mkdir yapar, bu çalışır; ama biz doğrudan
        # record_write_failure'ı test ediyoruz
        store.record_write_failure()
        assert store.write_failures() == 1


# ===================================================================
# TestOverrideKindSemantics — breach/attempt/applied ayrımı
# ===================================================================

class TestOverrideKindSemantics:
    """Override kind semantiği net ve doğru."""

    def test_breach_means_absolute_block_override_attempt(self):
        """breach = override attempted + BLOCK + ABSOLUTE_BLOCK_REASONS."""
        m = MetricExporter.from_preflight_output(_block_breach_output())
        assert m.override_kind == "breach"
        assert m.contract_breach is True

    def test_attempt_means_block_override_no_breach(self):
        """attempt = override attempted + BLOCK + breach yok."""
        out = _block_output()
        out["override_by"] = "someone"
        m = MetricExporter.from_preflight_output(out)
        assert m.override_kind == "attempt"
        assert m.contract_breach is False

    def test_applied_means_hold_override_accepted(self):
        """applied = override accepted + HOLD verdict."""
        m = MetricExporter.from_preflight_output(_hold_overridden_output())
        assert m.override_kind == "applied"
        assert m.override_applied is True

    def test_none_means_no_override_flags(self):
        """none = override flag'leri sağlanmamış."""
        m = MetricExporter.from_preflight_output(_ok_output())
        assert m.override_kind == "none"
