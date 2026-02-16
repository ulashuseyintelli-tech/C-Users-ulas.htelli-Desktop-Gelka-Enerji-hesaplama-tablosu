"""
Preflight Guard-Rails — unit + property testleri.

Task 1.1 DoD:
    - Eski JSON (meta yok) load edilebiliyor, meta backfill yapılıyor, ilk save'den sonra meta persist
    - save_to_dir() her başarılı çağrıda store_generation +1
    - store_start_timestamp değişmiyor (aynı dir'de arka arkaya save'lerde sabit)
    - Prom dosyasında iki gauge her zaman yazılıyor

Task 1.2 — Property 1: store_generation monoton artış
    Hypothesis ile rastgele operasyon dizileri (SAVE_OK, SAVE_FAIL, RELOAD, EXPORT, ADD).
    Invariants:
        - store_generation >= 0
        - store_generation non-decreasing
        - store_generation == başarılı save sayısı (initial_generation + S)
        - store_start_timestamp reload'lardan sonra sabit
        - export_prometheus() çağrısı state değiştirmez
    Fail injection: _atomic_write monkeypatch ile deterministic fail.
"""
import json
import time
from enum import Enum
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings, note
from hypothesis import strategies as st

from backend.app.testing.preflight_metrics import (
    MetricExporter,
    MetricStore,
    PreflightMetric,
)


def _ok_output() -> dict:
    return {
        "verdict": "release_ok",
        "exit_code": 0,
        "reasons": [],
        "override_applied": False,
        "contract_breach": False,
        "spec_hash": "abc123",
    }


# ===================================================================
# TestStoreGeneration
# ===================================================================

class TestStoreGeneration:
    """store_generation her başarılı save'de +1 artar."""

    def test_initial_generation_zero(self):
        store = MetricStore()
        assert store.store_generation() == 0

    def test_save_increments_generation(self, tmp_path):
        store = MetricStore()
        assert store.store_generation() == 0
        store.save_to_dir(tmp_path)
        assert store.store_generation() == 1
        store.save_to_dir(tmp_path)
        assert store.store_generation() == 2

    def test_two_saves_generation_plus_two(self, tmp_path):
        store = MetricStore()
        store.save_to_dir(tmp_path)
        store.save_to_dir(tmp_path)
        assert store.store_generation() == 2

    def test_generation_persisted_after_save_load(self, tmp_path):
        store = MetricStore()
        store.add(MetricExporter.from_preflight_output(_ok_output()))
        store.save_to_dir(tmp_path)
        assert store.store_generation() == 1

        loaded = MetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.store_generation() == 1

    def test_generation_survives_multiple_save_load_cycles(self, tmp_path):
        store = MetricStore()
        for i in range(5):
            store.save_to_dir(tmp_path)
            assert store.store_generation() == i + 1

        loaded = MetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.store_generation() == 5


# ===================================================================
# TestStoreStartTimestamp
# ===================================================================

class TestStoreStartTimestamp:
    """store_start_timestamp bir kez set edilir, sonra değişmez."""

    def test_initial_timestamp_positive(self):
        store = MetricStore()
        assert store.store_start_timestamp() > 0

    def test_timestamp_stable_across_saves(self, tmp_path):
        store = MetricStore()
        ts1 = store.store_start_timestamp()
        store.save_to_dir(tmp_path)
        ts2 = store.store_start_timestamp()
        store.save_to_dir(tmp_path)
        ts3 = store.store_start_timestamp()
        assert ts1 == ts2 == ts3

    def test_timestamp_persisted_after_save_load(self, tmp_path):
        store = MetricStore()
        original_ts = store.store_start_timestamp()
        store.save_to_dir(tmp_path)

        loaded = MetricStore()
        loaded.load_from_dir(tmp_path)
        assert loaded.store_start_timestamp() == original_ts

    def test_timestamp_stable_across_save_load_cycles(self, tmp_path):
        store = MetricStore()
        original_ts = store.store_start_timestamp()
        for _ in range(3):
            store.save_to_dir(tmp_path)
            store2 = MetricStore()
            store2.load_from_dir(tmp_path)
            assert store2.store_start_timestamp() == original_ts


# ===================================================================
# TestLegacyFormatBackfill — eski JSON (meta yok)
# ===================================================================

class TestLegacyFormatBackfill:
    """Eski format JSON (store_generation/store_start_timestamp yok) load edilebilir."""

    def test_legacy_json_loads_with_defaults(self, tmp_path):
        """Meta olmayan store yükle → save → meta var ve doğru."""
        legacy_data = {
            "metrics": [],
            "verdict_counts": {"OK": 5, "HOLD": 2, "BLOCK": 1},
            "reason_counts": {},
            "override_counts": {"attempt": 0, "applied": 0, "breach": 0},
            "write_failures": 0,
        }
        p = tmp_path / "preflight_metrics.json"
        p.write_text(json.dumps(legacy_data), encoding="utf-8")

        store = MetricStore()
        assert store.load_from_dir(tmp_path) is True
        # generation: eski formatta yok → 0
        assert store.store_generation() == 0
        # timestamp: eski formatta yok → dosyanın mtime'ı backfill
        assert store.store_start_timestamp() > 0
        # mtime backfill: dosyanın mtime'ına yakın olmalı
        file_mtime = p.stat().st_mtime
        assert store.store_start_timestamp() == file_mtime

        # Save sonrası meta persist edilmeli
        store.save_to_dir(tmp_path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert "store_generation" in raw
        assert "store_start_timestamp" in raw
        assert raw["store_generation"] == 1

    def test_legacy_json_verdict_counts_preserved(self, tmp_path):
        """Eski format yüklendiğinde mevcut counter'lar korunur."""
        legacy_data = {
            "metrics": [],
            "verdict_counts": {"OK": 10, "HOLD": 3, "BLOCK": 2},
            "reason_counts": {},
            "override_counts": {"attempt": 1, "applied": 2, "breach": 0},
            "write_failures": 3,
        }
        p = tmp_path / "preflight_metrics.json"
        p.write_text(json.dumps(legacy_data), encoding="utf-8")

        store = MetricStore()
        store.load_from_dir(tmp_path)
        assert store.verdict_counts()["OK"] == 10
        assert store.write_failures() == 3


# ===================================================================
# TestCorruptedMeta — bozuk dosya fail-open
# ===================================================================

class TestCorruptedMeta:
    """Bozuk JSON → fail-open: generation=0, timestamp=now."""

    def test_corrupt_json_returns_false(self, tmp_path):
        p = tmp_path / "preflight_metrics.json"
        p.write_text("not valid json {{{", encoding="utf-8")

        store = MetricStore()
        before_ts = store.store_start_timestamp()
        assert store.load_from_dir(tmp_path) is False
        # Store defaults korunur
        assert store.store_generation() == 0
        assert store.store_start_timestamp() == before_ts


# ===================================================================
# TestPrometheusGauges — prom dosyasında iki gauge
# ===================================================================

class TestPrometheusGauges:
    """Prometheus çıktısında store_generation ve store_start_time_seconds gauge'ları."""

    def test_gauges_present_in_prometheus_output(self):
        store = MetricStore()
        prom = MetricExporter.export_prometheus(store)
        assert "# HELP release_preflight_store_generation" in prom
        assert "# TYPE release_preflight_store_generation gauge" in prom
        assert "release_preflight_store_generation 0" in prom

        assert "# HELP release_preflight_store_start_time_seconds" in prom
        assert "# TYPE release_preflight_store_start_time_seconds gauge" in prom

    def test_gauges_reflect_state_after_saves(self, tmp_path):
        store = MetricStore()
        store.save_to_dir(tmp_path)
        store.save_to_dir(tmp_path)
        prom = MetricExporter.export_prometheus(store)
        assert "release_preflight_store_generation 2" in prom

    def test_gauge_names_not_total(self):
        """Gauge'lar _total suffix'i taşımaz — bunlar state, counter değil."""
        store = MetricStore()
        prom = MetricExporter.export_prometheus(store)
        assert "release_preflight_store_generation_total" not in prom
        assert "release_preflight_store_start_time_seconds_total" not in prom


# ===================================================================
# Property 1: store_generation monoton artış — Hypothesis stateful test
# Feature: preflight-guardrails, Property 1: Store generation monoton artış
# Validates: Requirements 1.1
# ===================================================================

class Op(Enum):
    """Operasyon tipleri — Hypothesis tarafından rastgele seçilir."""
    SAVE_OK = "save_ok"
    SAVE_FAIL = "save_fail"
    RELOAD = "reload"
    EXPORT = "export"
    ADD = "add"


# Hypothesis strategy: 1-50 operasyon dizisi
_op_strategy = st.lists(
    st.sampled_from(list(Op)),
    min_size=1,
    max_size=50,
)


def _make_ok_output() -> dict:
    return {
        "verdict": "release_ok",
        "exit_code": 0,
        "reasons": [],
        "override_applied": False,
        "contract_breach": False,
        "spec_hash": "prop-test",
    }


class TestPropertyGenerationMonotonic:
    """
    Property 1: store_generation monoton artış.

    Rastgele operasyon dizileri üzerinde invariants:
        - generation >= 0
        - generation non-decreasing
        - generation == initial_generation + başarılı_save_sayısı
        - start_timestamp sabit (aynı dir'de)
        - export side-effect yok
    """

    @given(ops=_op_strategy)
    @settings(max_examples=200, deadline=None)
    def test_generation_monotonic_with_random_ops(self, ops: list[Op], tmp_path_factory):
        """Rastgele op dizisi boyunca generation asla azalmaz."""
        tmp_dir = tmp_path_factory.mktemp("prop1")
        store = MetricStore()
        initial_ts = store.store_start_timestamp()

        prev_gen = store.store_generation()
        successful_saves = 0
        op_trace: list[str] = []

        for i, op in enumerate(ops):
            if op == Op.SAVE_OK:
                ok = store.save_to_dir(tmp_dir)
                assert ok, f"Step {i}: SAVE_OK failed unexpectedly"
                successful_saves += 1
                op_trace.append(f"SAVE_OK(gen={store.store_generation()})")

            elif op == Op.SAVE_FAIL:
                # Monkeypatch _atomic_write to return False (deterministic fail)
                with patch(
                    "backend.app.testing.preflight_metrics._atomic_write",
                    return_value=False,
                ):
                    ok = store.save_to_dir(tmp_dir)
                assert not ok, f"Step {i}: SAVE_FAIL should have failed"
                op_trace.append(f"SAVE_FAIL(gen={store.store_generation()})")

            elif op == Op.RELOAD:
                new_store = MetricStore()
                loaded = new_store.load_from_dir(tmp_dir)
                if loaded:
                    # Reload sonrası generation ve timestamp korunmalı
                    assert new_store.store_generation() == store.store_generation(), (
                        f"Step {i}: RELOAD generation mismatch: "
                        f"expected {store.store_generation()}, got {new_store.store_generation()}"
                    )
                    store = new_store
                op_trace.append(f"RELOAD(loaded={loaded}, gen={store.store_generation()})")

            elif op == Op.EXPORT:
                gen_before = store.store_generation()
                ts_before = store.store_start_timestamp()
                _ = MetricExporter.export_prometheus(store)
                _ = MetricExporter.export_json(store)
                # Export side-effect yok
                assert store.store_generation() == gen_before, (
                    f"Step {i}: EXPORT changed generation"
                )
                assert store.store_start_timestamp() == ts_before, (
                    f"Step {i}: EXPORT changed timestamp"
                )
                op_trace.append(f"EXPORT(gen={store.store_generation()})")

            elif op == Op.ADD:
                m = MetricExporter.from_preflight_output(_make_ok_output())
                store.add(m)
                op_trace.append(f"ADD(gen={store.store_generation()})")

            # Invariants — her adımda kontrol
            curr_gen = store.store_generation()
            assert curr_gen >= 0, (
                f"Step {i} ({op.value}): generation < 0: {curr_gen}\n"
                f"Trace: {op_trace}"
            )
            assert curr_gen >= prev_gen, (
                f"Step {i} ({op.value}): generation decreased: "
                f"{prev_gen} → {curr_gen}\n"
                f"Trace: {op_trace}"
            )
            prev_gen = curr_gen

        # Final assert: generation == başarılı save sayısı
        note(f"Op trace: {op_trace}")
        note(f"Successful saves: {successful_saves}, final gen: {store.store_generation()}")
        assert store.store_generation() == successful_saves, (
            f"Final generation {store.store_generation()} != "
            f"successful saves {successful_saves}\n"
            f"Trace: {op_trace}"
        )

    @given(ops=_op_strategy)
    @settings(max_examples=200, deadline=None)
    def test_timestamp_stable_across_operations(self, ops: list[Op], tmp_path_factory):
        """Aynı dir'de tüm operasyonlar boyunca start_timestamp sabit kalır."""
        tmp_dir = tmp_path_factory.mktemp("prop1ts")
        store = MetricStore()

        # İlk save yaparak timestamp'ı persist et
        store.save_to_dir(tmp_dir)
        original_ts = store.store_start_timestamp()

        for i, op in enumerate(ops):
            if op == Op.SAVE_OK:
                store.save_to_dir(tmp_dir)
            elif op == Op.SAVE_FAIL:
                with patch(
                    "backend.app.testing.preflight_metrics._atomic_write",
                    return_value=False,
                ):
                    store.save_to_dir(tmp_dir)
            elif op == Op.RELOAD:
                new_store = MetricStore()
                if new_store.load_from_dir(tmp_dir):
                    store = new_store
            elif op == Op.EXPORT:
                MetricExporter.export_prometheus(store)
            elif op == Op.ADD:
                store.add(MetricExporter.from_preflight_output(_make_ok_output()))

            assert store.store_start_timestamp() == original_ts, (
                f"Step {i} ({op.value}): timestamp changed: "
                f"{original_ts} → {store.store_start_timestamp()}"
            )

    @given(
        fail_indices=st.frozensets(st.integers(min_value=0, max_value=19), max_size=10),
    )
    @settings(max_examples=200, deadline=None)
    def test_fail_fail_ok_pattern(self, fail_indices: frozenset, tmp_path_factory):
        """
        20 save denemesi, bazıları fail. Generation == başarılı save sayısı.
        Edge case: fail → fail → ok (generation sadece +1).
        """
        tmp_dir = tmp_path_factory.mktemp("prop1ff")
        store = MetricStore()
        successful = 0

        for i in range(20):
            if i in fail_indices:
                with patch(
                    "backend.app.testing.preflight_metrics._atomic_write",
                    return_value=False,
                ):
                    store.save_to_dir(tmp_dir)
            else:
                store.save_to_dir(tmp_dir)
                successful += 1

        assert store.store_generation() == successful

    @given(n_saves=st.integers(min_value=1, max_value=30))
    @settings(max_examples=200, deadline=None)
    def test_reload_between_saves_preserves_generation(self, n_saves: int, tmp_path_factory):
        """
        ok → reload → ok pattern: her save sonrası reload, generation doğru artar.
        """
        tmp_dir = tmp_path_factory.mktemp("prop1rl")
        store = MetricStore()

        for i in range(n_saves):
            store.save_to_dir(tmp_dir)
            assert store.store_generation() == i + 1

            # Reload
            reloaded = MetricStore()
            assert reloaded.load_from_dir(tmp_dir) is True
            assert reloaded.store_generation() == i + 1
            store = reloaded

        assert store.store_generation() == n_saves

    @given(
        legacy_ok_count=st.integers(min_value=0, max_value=10),
        legacy_hold_count=st.integers(min_value=0, max_value=10),
    )
    @settings(max_examples=100, deadline=None)
    def test_legacy_backfill_then_save(
        self, legacy_ok_count: int, legacy_hold_count: int, tmp_path_factory,
    ):
        """
        Eski format backfill'li load sonrası ok save → generation 1 olur.
        """
        tmp_dir = tmp_path_factory.mktemp("prop1leg")
        legacy_data = {
            "metrics": [],
            "verdict_counts": {"OK": legacy_ok_count, "HOLD": legacy_hold_count, "BLOCK": 0},
            "reason_counts": {},
            "override_counts": {"attempt": 0, "applied": 0, "breach": 0},
            "write_failures": 0,
            # store_generation ve store_start_timestamp yok — eski format
        }
        p = tmp_dir / "preflight_metrics.json"
        p.write_text(json.dumps(legacy_data), encoding="utf-8")

        store = MetricStore()
        assert store.load_from_dir(tmp_dir) is True
        assert store.store_generation() == 0  # eski format → 0

        store.save_to_dir(tmp_dir)
        assert store.store_generation() == 1

        # Reload sonrası generation korunur
        reloaded = MetricStore()
        reloaded.load_from_dir(tmp_dir)
        assert reloaded.store_generation() == 1

# ===================================================================
# Task 3.1: Cardinality Guard — BlockReasonCode enum cap
# ===================================================================

from backend.app.testing.release_policy import BlockReasonCode
from backend.app.testing.preflight_metrics import (
    _REASON_LABELS,
    _VERDICT_LABELS,
    _OVERRIDE_KINDS,
)

MAX_BLOCK_REASON_CARDINALITY = 50


class TestCardinalityGuard:
    """
    BlockReasonCode enum cardinality guard.
    CI'da regression guard olarak çalışır.
    _Requirements: 2.1, 2.2_
    """

    def test_block_reason_code_cardinality_cap(self):
        """BlockReasonCode enum üye sayısı CAP'i aşmamalı."""
        assert len(BlockReasonCode) <= MAX_BLOCK_REASON_CARDINALITY, (
            f"BlockReasonCode enum {len(BlockReasonCode)} üyeye ulaştı. "
            f"Sınır: {MAX_BLOCK_REASON_CARDINALITY}. "
            f"Prometheus cardinality patlaması riski."
        )

    def test_reason_labels_match_enum_exactly(self):
        """_REASON_LABELS sabit tuple'ı enum ile birebir eşleşmeli."""
        assert set(_REASON_LABELS) == {r.value for r in BlockReasonCode}

    def test_verdict_labels_bounded(self):
        """Verdict label seti sabit: OK, HOLD, BLOCK."""
        assert set(_VERDICT_LABELS) == {"OK", "HOLD", "BLOCK"}

    def test_override_kinds_bounded(self):
        """Override kind seti sabit: attempt, applied, breach."""
        assert set(_OVERRIDE_KINDS) == {"attempt", "applied", "breach"}

    def test_reason_label_count_gauge(self):
        """Prometheus çıktısında reason label sayısı gözlemlenebilir."""
        store = MetricStore()
        prom = MetricExporter.export_prometheus(store)
        # Tüm reason label'ları sıfır dahil yazılmalı
        for reason in BlockReasonCode:
            assert f'reason="{reason.value}"' in prom, (
                f"Reason label {reason.value} Prometheus çıktısında eksik"
            )


# ===================================================================
# Task 3.2: Property test — reason label filtreleme (Property 3)
# ===================================================================

class TestPropertyReasonLabelFiltering:
    """
    **Property 3: Reason label filtreleme — bounded cardinality**

    Rastgele string listesi (geçerli + geçersiz karışık) ile
    from_preflight_output çağrıldığında, sonuçta sadece
    BlockReasonCode enum üyeleri kalır.

    **Validates: Requirements 2.3**
    """

    @given(
        valid_reasons=st.lists(
            st.sampled_from([r.value for r in BlockReasonCode]),
            max_size=len(BlockReasonCode),
            unique=True,
        ),
        invalid_reasons=st.lists(
            st.text(min_size=1, max_size=30).filter(
                lambda s: s not in {r.value for r in BlockReasonCode}
            ),
            max_size=10,
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_only_enum_members_survive_filtering(self, valid_reasons, invalid_reasons):
        """
        **Validates: Requirements 2.3**

        Geçerli + geçersiz reason'lar karışık verildiğinde,
        from_preflight_output sonrası sadece enum üyeleri kalır.
        """
        mixed = valid_reasons + invalid_reasons
        output = {
            "verdict": "release_hold",
            "exit_code": 1,
            "reasons": mixed,
            "override_applied": False,
            "contract_breach": False,
            "spec_hash": "test",
        }
        metric = MetricExporter.from_preflight_output(output)

        # Sonuçta sadece geçerli reason'lar olmalı
        valid_set = {r.value for r in BlockReasonCode}
        for r in metric.reasons:
            assert r in valid_set, f"Geçersiz reason filtrelenmemiş: {r}"

        # Tüm geçerli reason'lar korunmalı
        for r in valid_reasons:
            assert r in metric.reasons, f"Geçerli reason kaybolmuş: {r}"

        # Geçersiz reason'lar filtrelenmiş olmalı
        for r in invalid_reasons:
            assert r not in metric.reasons, f"Geçersiz reason filtrelenmemiş: {r}"

        note(f"valid={valid_reasons}, invalid={invalid_reasons}, result={metric.reasons}")


# ===================================================================
# Task 5.1: Property 4 — Write failures in-memory birikim ve persist round-trip
# Feature: preflight-guardrails, Property 4: Write failures in-memory birikim ve persist round-trip
# Validates: Requirements 4.1, 4.2, 4.3
# ===================================================================

import tempfile
import concurrent.futures


class TestPropertyWriteFailuresRoundTrip:
    """
    **Property 4: Write failures in-memory birikim ve persist round-trip**

    N ardışık record_write_failure() → write_failures() == N.
    Ardından save → load → write_failures() == N.

    **Validates: Requirements 4.1, 4.2, 4.3**
    """

    @given(n_failures=st.integers(min_value=1, max_value=20))
    @settings(max_examples=200, deadline=None)
    def test_write_failures_accumulate_and_persist(self, n_failures: int):
        """
        **Validates: Requirements 4.1, 4.2, 4.3**

        N kez record_write_failure() → write_failures() == N.
        save → load → write_failures() == N.
        """
        tmp_dir = tempfile.mkdtemp()
        store = MetricStore()

        for _ in range(n_failures):
            store.record_write_failure()

        assert store.write_failures() == n_failures, (
            f"In-memory birikim hatası: beklenen {n_failures}, "
            f"gerçek {store.write_failures()}"
        )

        ok = store.save_to_dir(tmp_dir)
        assert ok, "save_to_dir başarısız oldu"

        loaded = MetricStore()
        loaded.load_from_dir(tmp_dir)
        assert loaded.write_failures() == n_failures, (
            f"Round-trip sonrası write_failures kayboldu: "
            f"beklenen {n_failures}, gerçek {loaded.write_failures()}"
        )

        note(f"n_failures={n_failures}, persisted={loaded.write_failures()}")

    @given(
        fail_counts=st.lists(
            st.integers(min_value=1, max_value=5),
            min_size=1,
            max_size=5,
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_fail_fail_ok_pattern_accumulates(self, fail_counts: list[int]):
        """
        fail → fail → ... → ok save pattern:
        Her fail grubunda N failure birikir, sonra başarılı save persist eder.

        **Validates: Requirements 4.1, 4.2, 4.3**
        """
        tmp_dir = tempfile.mkdtemp()
        store = MetricStore()
        total_failures = 0

        for count in fail_counts:
            for _ in range(count):
                with patch(
                    "backend.app.testing.preflight_metrics._atomic_write",
                    return_value=False,
                ):
                    ok = store.save_to_dir(tmp_dir)
                    assert not ok
                total_failures += 1

        assert store.write_failures() == total_failures, (
            f"Birikim hatası: beklenen {total_failures}, "
            f"gerçek {store.write_failures()}"
        )

        ok = store.save_to_dir(tmp_dir)
        assert ok, "Final save başarısız"

        loaded = MetricStore()
        loaded.load_from_dir(tmp_dir)
        assert loaded.write_failures() == total_failures, (
            f"Persist sonrası kayıp: beklenen {total_failures}, "
            f"gerçek {loaded.write_failures()}"
        )

        note(f"fail_counts={fail_counts}, total={total_failures}")

    @given(
        n_failures=st.integers(min_value=0, max_value=10),
        n_metrics=st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=200, deadline=None)
    def test_write_failures_independent_of_metrics(self, n_failures: int, n_metrics: int):
        """
        write_failures counter, metrik ekleme/save'den bağımsız çalışır.

        **Validates: Requirements 4.1, 4.2**
        """
        tmp_dir = tempfile.mkdtemp()
        store = MetricStore()

        for _ in range(n_metrics):
            store.add(MetricExporter.from_preflight_output(_ok_output()))

        for _ in range(n_failures):
            store.record_write_failure()

        assert store.write_failures() == n_failures

        if n_failures > 0 or n_metrics > 0:
            ok = store.save_to_dir(tmp_dir)
            assert ok

            loaded = MetricStore()
            loaded.load_from_dir(tmp_dir)
            assert loaded.write_failures() == n_failures
            assert len(loaded) == n_metrics


# ===================================================================
# Task 5.2: Property 5 — Write failures thread-safety
# Feature: preflight-guardrails, Property 5: Write failures thread-safety
# Validates: Requirements 4.4
# ===================================================================


class TestPropertyWriteFailuresThreadSafety:
    """
    **Property 5: Write failures thread-safety**

    K thread × M çağrı → toplam write_failures() == K × M.

    **Validates: Requirements 4.4**
    """

    @given(
        k_threads=st.integers(min_value=2, max_value=8),
        m_calls=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=200, deadline=None)
    def test_concurrent_write_failures_exact_count(self, k_threads: int, m_calls: int):
        """
        **Validates: Requirements 4.4**

        K thread'den her biri M kez record_write_failure() çağırır.
        Toplam == K × M.
        """
        store = MetricStore()
        expected = k_threads * m_calls

        def worker():
            for _ in range(m_calls):
                store.record_write_failure()

        with concurrent.futures.ThreadPoolExecutor(max_workers=k_threads) as executor:
            futures = [executor.submit(worker) for _ in range(k_threads)]
            for f in futures:
                f.result()

        assert store.write_failures() == expected, (
            f"Thread-safety ihlali: beklenen {expected} "
            f"({k_threads} × {m_calls}), gerçek {store.write_failures()}"
        )

        note(f"k={k_threads}, m={m_calls}, total={store.write_failures()}")

    @given(
        k_threads=st.integers(min_value=2, max_value=6),
        m_calls=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=200, deadline=None)
    def test_concurrent_failures_persist_correctly(self, k_threads: int, m_calls: int):
        """
        Concurrent failure birikim sonrası save → load → doğru toplam.

        **Validates: Requirements 4.4, 4.2**
        """
        tmp_dir = tempfile.mkdtemp()
        store = MetricStore()
        expected = k_threads * m_calls

        def worker():
            for _ in range(m_calls):
                store.record_write_failure()

        with concurrent.futures.ThreadPoolExecutor(max_workers=k_threads) as executor:
            futures = [executor.submit(worker) for _ in range(k_threads)]
            for f in futures:
                f.result()

        assert store.write_failures() == expected

        ok = store.save_to_dir(tmp_dir)
        assert ok

        loaded = MetricStore()
        loaded.load_from_dir(tmp_dir)
        assert loaded.write_failures() == expected, (
            f"Persist sonrası kayıp: beklenen {expected}, "
            f"gerçek {loaded.write_failures()}"
        )


# ===================================================================
# Task 6.2: Yapısal test — alert kuralları YAML doğrulaması
# Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.9
# ===================================================================

import yaml


_ALERTS_PATH = (
    Path(__file__).resolve().parents[2]
    / "monitoring" / "prometheus" / "ptf-admin-alerts.yml"
)

# Beklenen alert isimleri ve severity'leri
_EXPECTED_GUARDRAIL_ALERTS = {
    "PreflightContractBreach": "critical",
    "PreflightBlockSpike": "warning",
    "PreflightTelemetryWriteFailure": "warning",
    "PreflightCounterReset": "warning",
}


class TestPreflightGuardrailAlerts:
    """
    ptf-admin-preflight-guardrails alert grubu yapısal doğrulama.
    _Requirements: 5.1, 5.2, 5.3, 5.4, 5.9_
    """

    @pytest.fixture(scope="class")
    def alerts_yaml(self):
        text = _ALERTS_PATH.read_text(encoding="utf-8")
        return yaml.safe_load(text)

    @pytest.fixture(scope="class")
    def guardrail_group(self, alerts_yaml):
        groups = alerts_yaml["spec"]["groups"]
        for g in groups:
            if g["name"] == "ptf-admin-preflight-guardrails":
                return g
        pytest.fail("ptf-admin-preflight-guardrails alert grubu bulunamadı")

    @pytest.fixture(scope="class")
    def guardrail_rules(self, guardrail_group):
        return {r["alert"]: r for r in guardrail_group["rules"]}

    def test_group_exists(self, guardrail_group):
        assert guardrail_group is not None

    def test_four_alert_rules(self, guardrail_rules):
        assert len(guardrail_rules) == 4, (
            f"Beklenen 4 alert, bulunan {len(guardrail_rules)}: "
            f"{list(guardrail_rules.keys())}"
        )

    def test_alert_names_match(self, guardrail_rules):
        assert set(guardrail_rules.keys()) == set(_EXPECTED_GUARDRAIL_ALERTS.keys())

    def test_alert_severities(self, guardrail_rules):
        for name, expected_sev in _EXPECTED_GUARDRAIL_ALERTS.items():
            actual = guardrail_rules[name]["labels"]["severity"]
            assert actual == expected_sev, (
                f"{name}: beklenen severity={expected_sev}, gerçek={actual}"
            )

    def test_all_alerts_have_runbook_url(self, guardrail_rules):
        for name, rule in guardrail_rules.items():
            url = rule["annotations"].get("runbook_url", "")
            assert url, f"{name}: runbook_url eksik"
            # Anchor, alert adıyla eşleşmeli
            assert f"#{name}" in url, (
                f"{name}: runbook_url anchor yanlış: {url}"
            )

    def test_alert_names_unique(self, guardrail_rules):
        """Alert isimleri benzersiz olmalı (dict key'leri zaten benzersiz ama explicit kontrol)."""
        names = [r["alert"] for r in list(guardrail_rules.values())]
        assert len(names) == len(set(names))

    def test_contract_breach_expr(self, guardrail_rules):
        expr = guardrail_rules["PreflightContractBreach"]["expr"]
        assert "release_preflight_override_total" in expr
        assert 'kind="breach"' in expr

    def test_block_spike_dual_threshold(self, guardrail_rules):
        """BLOCK spike: hem mutlak sayı hem oran şartı."""
        expr = guardrail_rules["PreflightBlockSpike"]["expr"]
        assert "release_preflight_verdict_total" in expr
        # Dual threshold: "and" ile birleştirilmiş
        assert "and" in expr.lower() or "AND" in expr

    def test_write_failure_expr(self, guardrail_rules):
        expr = guardrail_rules["PreflightTelemetryWriteFailure"]["expr"]
        assert "release_preflight_telemetry_write_failures_total" in expr

    def test_counter_reset_expr(self, guardrail_rules):
        expr = guardrail_rules["PreflightCounterReset"]["expr"]
        assert "resets(" in expr
        assert "release_preflight_verdict_total" in expr


# ===================================================================
# Task 8.3: Yapısal test — runbook override semantiği ve alert bölümleri
# Validates: Requirements 3.1, 5.8
# ===================================================================

_RUNBOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / "monitoring" / "runbooks" / "ptf-admin-runbook.md"
)


class TestPreflightRunbookStructure:
    """
    Runbook'ta override semantiği ve 4 alert troubleshooting bölümü doğrulama.
    _Requirements: 3.1, 3.2, 3.3, 3.4, 5.8_
    """

    @pytest.fixture(scope="class")
    def runbook_text(self):
        return _RUNBOOK_PATH.read_text(encoding="utf-8")

    def test_override_semantics_section_exists(self, runbook_text):
        assert "# Preflight Override Semantiği" in runbook_text

    def test_override_kinds_defined(self, runbook_text):
        assert "attempt" in runbook_text
        assert "applied" in runbook_text
        assert "breach" in runbook_text

    def test_attempt_means_rejected(self, runbook_text):
        """attempt = override reddedildi açıklaması mevcut."""
        assert "reddedildi" in runbook_text.lower() or "rejected" in runbook_text.lower()

    def test_breach_means_contract_violation(self, runbook_text):
        """breach = sözleşme ihlali açıklaması mevcut."""
        assert "sözleşme ihlali" in runbook_text.lower() or "contract" in runbook_text.lower()

    def test_contract_breach_alert_section(self, runbook_text):
        assert "## PreflightContractBreach" in runbook_text

    def test_block_spike_alert_section(self, runbook_text):
        assert "## PreflightBlockSpike" in runbook_text

    def test_write_failure_alert_section(self, runbook_text):
        assert "## PreflightTelemetryWriteFailure" in runbook_text

    def test_counter_reset_alert_section(self, runbook_text):
        assert "## PreflightCounterReset" in runbook_text

    def test_alert_sections_have_troubleshooting(self, runbook_text):
        """Her alert bölümünde 'Olası Nedenler' ve 'Müdahale Adımları' var."""
        for alert in ["PreflightContractBreach", "PreflightBlockSpike",
                       "PreflightTelemetryWriteFailure", "PreflightCounterReset"]:
            # Alert bölümünü bul
            idx = runbook_text.index(f"## {alert}")
            section = runbook_text[idx:idx + 2000]
            assert "Olası Nedenler" in section, f"{alert}: 'Olası Nedenler' eksik"
            assert "Müdahale Adımları" in section, f"{alert}: 'Müdahale Adımları' eksik"

    def test_runbook_has_promql_example(self, runbook_text):
        """En az bir PromQL örneği mevcut."""
        assert "```promql" in runbook_text

    def test_runbook_anchors_match_alert_names(self, runbook_text):
        """Alert anchor'ları runbook'ta mevcut (runbook_url doğrulaması)."""
        for alert in ["PreflightContractBreach", "PreflightBlockSpike",
                       "PreflightTelemetryWriteFailure", "PreflightCounterReset"]:
            assert f"## {alert}" in runbook_text, (
                f"Runbook'ta #{alert} anchor'ı eksik"
            )
