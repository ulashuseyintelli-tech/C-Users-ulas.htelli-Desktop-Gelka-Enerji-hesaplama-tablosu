"""
PR-17 Task 2.2: Save/Load Round-Trip Property Tests (Hypothesis PBT).

MetricStore'un disk persistence katmanını property-based testlerle kilitleriz.

Properties:
    P-RT1: random ops → save → load → canonical equality  (core round-trip)
    P-RT2: save fail injection → atomicity (eski valid JSON veya hiç dosya)
    P-RT3: legacy backfill state → save → load → equality (meta normalize)

**Validates: Requirements 1.2, 1.5**
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings, assume, note, HealthCheck, Phase

from backend.app.testing.preflight_metrics import (
    MetricExporter,
    MetricStore,
    PreflightMetric,
    _OVERRIDE_KINDS,
    _REASON_LABELS,
    _VERDICT_LABELS,
    _atomic_write,
)
from backend.app.testing.release_policy import BlockReasonCode


# ===================================================================
# Strategies — bounded ama geniş state jenerasyonu
# ===================================================================

_VERDICTS = list(_VERDICT_LABELS)  # ["OK", "HOLD", "BLOCK"]
_REASONS = list(_REASON_LABELS)    # BlockReasonCode.value'lar
_KINDS = list(_OVERRIDE_KINDS)     # ["attempt", "applied", "breach"]


def _st_reason_subset() -> st.SearchStrategy[list[str]]:
    """BlockReasonCode'dan rastgele subset (0..len arası)."""
    return st.lists(st.sampled_from(_REASONS), max_size=len(_REASONS), unique=True)


def _st_counter_value() -> st.SearchStrategy[int]:
    """Counter değeri: 0..1_000_000 aralığında int."""
    return st.integers(min_value=0, max_value=1_000_000)


def _st_metric() -> st.SearchStrategy[PreflightMetric]:
    """Rastgele geçerli PreflightMetric üret."""
    return st.builds(
        PreflightMetric,
        timestamp=st.from_regex(
            r"20[2-3][0-9]-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9]Z",
            fullmatch=True,
        ),
        verdict=st.sampled_from(_VERDICTS),
        exit_code=st.sampled_from([0, 1, 2]),
        reasons=_st_reason_subset(),
        override_applied=st.booleans(),
        contract_breach=st.booleans(),
        override_kind=st.sampled_from(_KINDS + ["none"]),
        spec_hash=st.text(
            alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
            min_size=1,
            max_size=20,
        ),
        duration_ms=st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    )


# Ops: ADD metric veya SAVE+RELOAD
class _OpAdd:
    """Store'a metrik ekle."""
    def __init__(self, metric: PreflightMetric):
        self.metric = metric

    def __repr__(self) -> str:
        return f"ADD(verdict={self.metric.verdict}, reasons={self.metric.reasons}, kind={self.metric.override_kind})"


class _OpSaveReload:
    """Store'u diske kaydet ve tekrar yükle."""
    def __repr__(self) -> str:
        return "SAVE_RELOAD"


def _st_op() -> st.SearchStrategy:
    """Rastgele op: ADD veya SAVE_RELOAD."""
    return st.one_of(
        _st_metric().map(_OpAdd),
        st.just(_OpSaveReload()),
    )


def _st_ops_sequence() -> st.SearchStrategy[list]:
    """Rastgele ops dizisi: en az 1 ADD, toplam 1..30 op."""
    return st.lists(_st_op(), min_size=1, max_size=30).filter(
        lambda ops: any(isinstance(op, _OpAdd) for op in ops)
    )


# ===================================================================
# Canonical equality helper
# ===================================================================

def _canonical_store_dict(store: MetricStore) -> dict[str, Any]:
    """
    Store'dan canonical dict üret.
    - Key ordering normalize (sorted)
    - Float timestamp → float (tutarlı tip)
    - Metrics listesi sıralı (timestamp + verdict)
    """
    d = store.to_dict()

    # Counter map'leri sorted key ile
    d["verdict_counts"] = dict(sorted(d["verdict_counts"].items()))
    d["reason_counts"] = dict(sorted(d["reason_counts"].items()))
    d["override_counts"] = dict(sorted(d["override_counts"].items()))

    # store_start_timestamp her zaman float
    d["store_start_timestamp"] = float(d["store_start_timestamp"])

    # Metrics listesi: her metrik dict'ini sorted key ile
    canonical_metrics = []
    for m in d["metrics"]:
        cm = dict(sorted(m.items()))
        # duration_ms her zaman float
        cm["duration_ms"] = float(cm["duration_ms"])
        # reasons listesi sorted (set semantiği, sıra önemsiz)
        cm["reasons"] = sorted(cm["reasons"])
        canonical_metrics.append(cm)

    # Metrics'i deterministik sırala (timestamp, verdict, spec_hash)
    canonical_metrics.sort(key=lambda m: (m["timestamp"], m["verdict"], m["spec_hash"]))
    d["metrics"] = canonical_metrics

    return d


def _assert_stores_equal(store1: MetricStore, store2: MetricStore, msg: str = "") -> None:
    """İki store'un canonical eşdeğerliğini doğrula."""
    d1 = _canonical_store_dict(store1)
    d2 = _canonical_store_dict(store2)

    # Meta fields
    assert d1["store_start_timestamp"] == d2["store_start_timestamp"], \
        f"store_start_timestamp farkı {msg}: {d1['store_start_timestamp']} != {d2['store_start_timestamp']}"
    assert d1["store_generation"] == d2["store_generation"], \
        f"store_generation farkı {msg}: {d1['store_generation']} != {d2['store_generation']}"

    # Counter maps
    assert d1["verdict_counts"] == d2["verdict_counts"], \
        f"verdict_counts farkı {msg}"
    assert d1["reason_counts"] == d2["reason_counts"], \
        f"reason_counts farkı {msg}"
    assert d1["override_counts"] == d2["override_counts"], \
        f"override_counts farkı {msg}"

    # Write failures
    assert d1["write_failures"] == d2["write_failures"], \
        f"write_failures farkı {msg}"

    # Metrics
    assert len(d1["metrics"]) == len(d2["metrics"]), \
        f"metrics count farkı {msg}: {len(d1['metrics'])} != {len(d2['metrics'])}"
    for i, (m1, m2) in enumerate(zip(d1["metrics"], d2["metrics"])):
        assert m1 == m2, f"metric[{i}] farkı {msg}: {m1} != {m2}"


# ===================================================================
# P-RT1: Core round-trip property
# random ops → save → load → equality
# **Validates: Requirements 1.2, 1.5**
# ===================================================================

class TestCoreRoundTrip:
    """
    Rastgele ops dizisi ile store doldur, diske kaydet, tekrar yükle.
    Save sonrası snapshot ile loaded store canonical eşdeğer olmalı.

    Kritik: Kıyas "save sonrası snapshot" ile yapılır çünkü save
    success'te generation +1 artar.
    """

    @given(ops=_st_ops_sequence())
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_random_ops_save_load_equality(self, ops):
        """
        **Validates: Requirements 1.2, 1.5**

        Random ops ile store'u doldur (ADD + ara SAVE/RELOAD).
        Final: SAVE → LOAD → compare (save sonrası snapshot ile).
        """
        import tempfile, shutil
        tmp_path = Path(tempfile.mkdtemp())
        store = MetricStore()

        # Ops trace for shrink debugging
        trace = []

        for op in ops:
            if isinstance(op, _OpAdd):
                store.add(op.metric)
                trace.append(repr(op))
            elif isinstance(op, _OpSaveReload):
                ok = store.save_to_dir(tmp_path)
                if ok:
                    trace.append("SAVE(ok)")
                    loaded = MetricStore()
                    loaded.load_from_dir(tmp_path)
                    # Ara reload: loaded store'u devam ettir
                    store = loaded
                    trace.append("RELOAD(ok)")
                else:
                    trace.append("SAVE(fail)")

        # Final: SAVE → snapshot → LOAD → compare
        note(f"Ops trace: {trace}")
        save_ok = store.save_to_dir(tmp_path)
        assert save_ok, f"Final save failed. Trace: {trace}"

        # save sonrası snapshot: generation artmış durumda
        # store zaten save sonrası state'i tutuyor (generation +1 oldu)
        expected_generation = store.store_generation()
        expected_timestamp = store.store_start_timestamp()

        loaded = MetricStore()
        load_ok = loaded.load_from_dir(tmp_path)
        assert load_ok, f"Final load failed. Trace: {trace}"

        # Canonical equality
        _assert_stores_equal(store, loaded, msg=f"Trace: {trace}")
        shutil.rmtree(tmp_path, ignore_errors=True)


# ===================================================================
# P-RT2: Atomicity — save fail injection
# **Validates: Requirements 1.2, 1.5**
# ===================================================================

class TestAtomicitySaveFailInjection:
    """
    SAVE sırasında _atomic_write fail injection.
    Sonuç: ya eski valid JSON kalmalı ya hiç dosya olmamalı.
    Yarım state asla gözlemlenmemeli.
    """

    @given(
        pre_metrics=st.lists(_st_metric(), min_size=0, max_size=10),
        post_metrics=st.lists(_st_metric(), min_size=1, max_size=10),
    )
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_fail_injection_preserves_old_or_empty(
        self, pre_metrics, post_metrics
    ):
        """
        **Validates: Requirements 1.2, 1.5**

        1. (Opsiyonel) İlk store'u kaydet → valid baseline
        2. Yeni metrikler ekle
        3. Save sırasında _atomic_write fail et
        4. Load: ya baseline state ya defaults — yarım state yok
        """
        import tempfile, shutil
        tmp_path = Path(tempfile.mkdtemp())

        # Phase 1: baseline (opsiyonel — pre_metrics boş olabilir)
        baseline_store = MetricStore()
        for m in pre_metrics:
            baseline_store.add(m)

        has_baseline = len(pre_metrics) > 0
        if has_baseline:
            ok = baseline_store.save_to_dir(tmp_path)
            assert ok, "Baseline save failed"
            # Baseline snapshot
            baseline_dict = _canonical_store_dict(baseline_store)

        # Phase 2: yeni metrikler ekle
        modified_store = MetricStore()
        if has_baseline:
            modified_store.load_from_dir(tmp_path)
        for m in post_metrics:
            modified_store.add(m)

        # Phase 3: save sırasında fail injection
        original_atomic_write = _atomic_write

        def _failing_atomic_write(target, content):
            """Her zaman fail eden atomic write — False döner."""
            return False

        with patch(
            "backend.app.testing.preflight_metrics._atomic_write",
            side_effect=_failing_atomic_write,
        ):
            save_result = modified_store.save_to_dir(tmp_path)
            assert save_result is False, "Save should have failed with injection"

        # Phase 4: Load — ya baseline ya defaults
        check_store = MetricStore()
        metrics_file = tmp_path / "preflight_metrics.json"

        if has_baseline:
            # Baseline dosyası hala orada olmalı
            assert metrics_file.exists(), "Baseline file should survive failed save"
            raw = json.loads(metrics_file.read_text(encoding="utf-8"))
            # Dosya valid JSON olmalı
            assert "metrics" in raw, "File should be valid store JSON"

            load_ok = check_store.load_from_dir(tmp_path)
            assert load_ok, "Load should succeed with baseline file"

            # Loaded state baseline ile eşdeğer olmalı
            loaded_dict = _canonical_store_dict(check_store)
            assert loaded_dict["verdict_counts"] == baseline_dict["verdict_counts"]
            assert loaded_dict["reason_counts"] == baseline_dict["reason_counts"]
            assert loaded_dict["override_counts"] == baseline_dict["override_counts"]
            assert len(loaded_dict["metrics"]) == len(baseline_dict["metrics"])
        else:
            # Baseline yoktu → dosya ya yok ya da oluşturulmamış
            if metrics_file.exists():
                # Eğer varsa, valid JSON olmalı (önceki bir save'den kalmış olabilir)
                raw = json.loads(metrics_file.read_text(encoding="utf-8"))
                assert "metrics" in raw
            else:
                # Dosya yok → load defaults döner
                load_ok = check_store.load_from_dir(tmp_path)
                assert load_ok is False
                assert len(check_store) == 0
        shutil.rmtree(tmp_path, ignore_errors=True)

    @given(metrics=st.lists(_st_metric(), min_size=1, max_size=5))
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_corrupted_json_triggers_failopen_defaults(self, metrics):
        """
        **Validates: Requirements 1.2, 1.5**

        Bozuk JSON dosyası → load fail-open defaults döner.
        Store sıfır state ile başlar, yarım state gözlemlenmez.
        """
        import tempfile, shutil
        tmp_path = Path(tempfile.mkdtemp())

        # Geçerli store kaydet
        store = MetricStore()
        for m in metrics:
            store.add(m)
        store.save_to_dir(tmp_path)

        # Dosyayı boz
        metrics_file = tmp_path / "preflight_metrics.json"
        metrics_file.write_text("{{{{not valid json at all!!!!", encoding="utf-8")

        # Load → fail-open defaults
        loaded = MetricStore()
        result = loaded.load_from_dir(tmp_path)
        assert result is False, "Corrupted JSON should fail load"
        assert len(loaded) == 0, "Corrupted load should give empty store"
        assert loaded.verdict_counts() == {"OK": 0, "HOLD": 0, "BLOCK": 0}
        shutil.rmtree(tmp_path, ignore_errors=True)


# ===================================================================
# P-RT3: Legacy backfill state → save → load → equality
# **Validates: Requirements 1.2**
# ===================================================================

class TestLegacyBackfillRoundTrip:
    """
    store_start_timestamp eksik (eski format) state üret.
    Save → load → meta normalize sonrası equality.
    """

    @given(metrics=st.lists(_st_metric(), min_size=1, max_size=15))
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_legacy_format_without_timestamp_roundtrips(self, metrics):
        """
        **Validates: Requirements 1.2**

        Eski format JSON (store_start_timestamp yok) → load → save → load → equality.
        load_from_dir eski formatta mtime'ı backfill olarak kullanır.
        """
        import tempfile, shutil
        tmp_path = Path(tempfile.mkdtemp())

        # Phase 1: Normal store oluştur ve kaydet
        store = MetricStore()
        for m in metrics:
            store.add(m)
        store.save_to_dir(tmp_path)

        # Phase 2: Dosyadan store_start_timestamp'ı sil (legacy format simülasyonu)
        metrics_file = tmp_path / "preflight_metrics.json"
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
        del data["store_start_timestamp"]
        metrics_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Phase 3: Load (backfill olacak — mtime kullanılır)
        loaded = MetricStore()
        load_ok = loaded.load_from_dir(tmp_path)
        assert load_ok, "Legacy format load should succeed"

        # store_start_timestamp backfill edilmiş olmalı (mtime)
        assert loaded.store_start_timestamp() > 0, "Backfilled timestamp should be positive"

        # Phase 4: Save → Load → equality (artık yeni format)
        loaded.save_to_dir(tmp_path)

        reloaded = MetricStore()
        reloaded.load_from_dir(tmp_path)

        # Counter equality (meta normalize sonrası)
        assert reloaded.verdict_counts() == loaded.verdict_counts()
        assert reloaded.reason_counts() == loaded.reason_counts()
        assert reloaded.override_counts() == loaded.override_counts()
        assert len(reloaded) == len(loaded)
        # Generation: loaded save etti (+1), reloaded load etti → aynı
        assert reloaded.store_generation() == loaded.store_generation()
        # Timestamp: artık dosyada var, backfill gerekmez
        assert reloaded.store_start_timestamp() == loaded.store_start_timestamp()
        shutil.rmtree(tmp_path, ignore_errors=True)

    @given(
        metrics=st.lists(_st_metric(), min_size=1, max_size=10),
        extra_fields=st.dictionaries(
            keys=st.text(
                alphabet=st.characters(whitelist_categories=("Ll",)),
                min_size=1,
                max_size=10,
            ),
            values=st.one_of(st.integers(), st.text(max_size=20)),
            max_size=3,
        ),
    )
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    def test_extra_fields_in_json_ignored_gracefully(
        self, metrics, extra_fields
    ):
        """
        **Validates: Requirements 1.2**

        JSON'da beklenmeyen alanlar → load başarılı, extra alanlar görmezden gelinir.
        Save → load → counter equality korunur.
        """
        import tempfile, shutil
        tmp_path = Path(tempfile.mkdtemp())

        store = MetricStore()
        for m in metrics:
            store.add(m)
        store.save_to_dir(tmp_path)

        # Extra alanlar ekle
        metrics_file = tmp_path / "preflight_metrics.json"
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
        for k, v in extra_fields.items():
            # Mevcut key'leri ezme — sadece yeni key'ler
            if k not in data:
                data[k] = v
        metrics_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = MetricStore()
        load_ok = loaded.load_from_dir(tmp_path)
        assert load_ok, "Load with extra fields should succeed"
        assert loaded.verdict_counts() == store.verdict_counts()
        assert loaded.reason_counts() == store.reason_counts()
        assert loaded.override_counts() == store.override_counts()
        assert len(loaded) == len(store)
        shutil.rmtree(tmp_path, ignore_errors=True)
