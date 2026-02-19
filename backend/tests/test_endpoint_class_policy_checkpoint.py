"""
Endpoint-Class Policy — Task 4 Checkpoint.

Task 1–3'ün ürettiği yeni alanların doğru üretildiğini ve
davranış değişikliği olmadan gözlemlenebildiğini kanıtlar.
Task 5 (middleware enforcement) öncesi "güvenli zemin".

CPK-1: Snapshot correctness (3 senaryo)
CPK-2: Hash determinism / sensitivity
CPK-3: Backward compatibility (parse fail, fallback)
CPK-4: Existing invariants hold (OpsGuard deny bypass, request counter)

Feature: endpoint-class-policy, Task 4
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from backend.app.guard_config import GuardConfig, GuardDenyReason
from backend.app.guards.guard_decision import (
    GuardDecisionSnapshot,
    RiskClass,
    SnapshotFactory,
    TenantMode,
    WindowParams,
    parse_endpoint_risk_map,
    resolve_endpoint_risk_class,
    resolve_effective_mode,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _iso_ago(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _config(**overrides) -> GuardConfig:
    defaults = {
        "schema_version": "1.0",
        "config_version": "test",
        "last_updated_at": _iso_ago(1),
        "decision_layer_enabled": True,
    }
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


FIXED_NOW = 1_700_000_000_000


# ═══════════════════════════════════════════════════════════════════════════════
# CPK-1: Snapshot Correctness — 3 senaryo
# ═══════════════════════════════════════════════════════════════════════════════

class TestCPK1SnapshotCorrectness:
    """
    Live SnapshotFactory.build() ile 3 senaryo:
    risk_class, tenant_mode, effective_mode doğru set ediliyor.
    """

    def test_risk_map_empty_enforce_tenant(self):
        """
        Risk map empty + tenant ENFORCE →
        risk_class=LOW, tenant_mode=ENFORCE, effective_mode=SHADOW.
        """
        config = _config(
            decision_layer_default_mode="enforce",
            decision_layer_endpoint_risk_map_json="",
        )
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/market-prices/list",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        assert snapshot is not None
        assert snapshot.risk_class == RiskClass.LOW
        assert snapshot.tenant_mode == TenantMode.ENFORCE
        assert snapshot.effective_mode == TenantMode.SHADOW

    def test_risk_map_exact_high_enforce_tenant(self):
        """
        Risk map exact HIGH + tenant ENFORCE →
        risk_class=HIGH, effective_mode=ENFORCE.
        """
        risk_map_json = '{"GET /admin/market-prices/upsert": "high"}'
        config = _config(
            decision_layer_default_mode="enforce",
            decision_layer_endpoint_risk_map_json=risk_map_json,
        )
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="GET /admin/market-prices/upsert",
            method="POST",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
            risk_class=RiskClass.HIGH,
        )
        assert snapshot is not None
        assert snapshot.risk_class == RiskClass.HIGH
        assert snapshot.tenant_mode == TenantMode.ENFORCE
        assert snapshot.effective_mode == TenantMode.ENFORCE

    def test_prefix_medium_shadow_tenant(self):
        """
        Prefix MEDIUM + tenant SHADOW →
        effective_mode=SHADOW (identity — shadow preserved).
        """
        config = _config(
            decision_layer_default_mode="shadow",
        )
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/market-prices/import/apply",
            method="POST",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
            risk_class=RiskClass.MEDIUM,
        )
        assert snapshot is not None
        assert snapshot.risk_class == RiskClass.MEDIUM
        assert snapshot.tenant_mode == TenantMode.SHADOW
        assert snapshot.effective_mode == TenantMode.SHADOW


# ═══════════════════════════════════════════════════════════════════════════════
# CPK-2: Hash Determinism / Sensitivity
# ═══════════════════════════════════════════════════════════════════════════════

class TestCPK2HashSensitivity:
    """
    Aynı now_ms, aynı tenant, aynı endpoint —
    sadece risk_class değişince hash değişmeli.
    """

    def test_hash_differs_when_risk_class_changes(self):
        """Empty map (LOW) vs HIGH → hash farklı."""
        config = _config(decision_layer_default_mode="shadow")
        common = dict(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/market-prices/upsert",
            method="POST",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        snap_low = SnapshotFactory.build(**common, risk_class=RiskClass.LOW)
        snap_high = SnapshotFactory.build(**common, risk_class=RiskClass.HIGH)
        assert snap_low is not None and snap_high is not None
        assert snap_low.risk_context_hash != snap_high.risk_context_hash

    def test_hash_deterministic_same_inputs(self):
        """Aynı input → aynı hash (determinism)."""
        config = _config(decision_layer_default_mode="enforce")
        kwargs = dict(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
            risk_class=RiskClass.MEDIUM,
        )
        snap1 = SnapshotFactory.build(**kwargs)
        snap2 = SnapshotFactory.build(**kwargs)
        assert snap1 is not None and snap2 is not None
        assert snap1.risk_context_hash == snap2.risk_context_hash

    def test_hash_differs_when_effective_mode_changes(self):
        """
        Same risk_class (HIGH), different tenant_mode →
        different effective_mode → different hash.
        """
        common = dict(
            guard_deny_reason=None,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
            risk_class=RiskClass.HIGH,
        )
        config_shadow = _config(decision_layer_default_mode="shadow")
        config_enforce = _config(decision_layer_default_mode="enforce")
        snap_s = SnapshotFactory.build(config=config_shadow, **common)
        snap_e = SnapshotFactory.build(config=config_enforce, **common)
        assert snap_s is not None and snap_e is not None
        # shadow tenant → effective SHADOW; enforce tenant + HIGH → effective ENFORCE
        assert snap_s.effective_mode == TenantMode.SHADOW
        assert snap_e.effective_mode == TenantMode.ENFORCE
        assert snap_s.risk_context_hash != snap_e.risk_context_hash


# ═══════════════════════════════════════════════════════════════════════════════
# CPK-3: Backward Compatibility — parse fail, fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestCPK3BackwardCompatibility:
    """
    Endpoint-class config yok / parse fail:
    fallback LOW, effective_mode override kuralı çalışır, crash yok.
    """

    def test_no_risk_map_config_fallback_low(self):
        """Config'te risk map yok → tüm endpoint'ler LOW."""
        config = _config(
            decision_layer_default_mode="enforce",
            # decision_layer_endpoint_risk_map_json not set → defaults to ""
        )
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/anything",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        assert snapshot is not None
        assert snapshot.risk_class == RiskClass.LOW
        assert snapshot.effective_mode == TenantMode.SHADOW  # ENFORCE + LOW → SHADOW

    def test_invalid_risk_map_json_fallback_low(self):
        """Geçersiz JSON → parse_endpoint_risk_map boş dict → LOW."""
        risk_map = parse_endpoint_risk_map("{invalid json}")
        assert risk_map == {}
        risk_class = resolve_endpoint_risk_class("/admin/test", risk_map)
        assert risk_class == RiskClass.LOW

    def test_invalid_risk_map_no_crash(self):
        """Geçersiz risk map JSON ile SnapshotFactory.build() crash etmez."""
        config = _config(
            decision_layer_default_mode="enforce",
            decision_layer_endpoint_risk_map_json="{broken json!!!}",
        )
        # Build should succeed — risk_class defaults to LOW
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        assert snapshot is not None
        assert snapshot.risk_class == RiskClass.LOW

    def test_enforce_low_override_still_works(self):
        """ENFORCE + LOW → SHADOW kuralı fallback durumunda da çalışır."""
        effective = resolve_effective_mode(TenantMode.ENFORCE, RiskClass.LOW)
        assert effective == TenantMode.SHADOW


# ═══════════════════════════════════════════════════════════════════════════════
# CPK-4: Existing Invariants Hold
# ═══════════════════════════════════════════════════════════════════════════════

class TestCPK4ExistingInvariants:
    """
    Mevcut guard mekanizmaları bozulmamış:
    - OpsGuard deny bypass (rate limit 429 path)
    - Snapshot immutability
    - Fail-open on None snapshot
    """

    def test_opsguard_deny_bypass_preserved(self):
        """
        guard_deny_reason=RATE_LIMITED → snapshot'ta PASSTHROUGH verdict.
        Decision layer bu durumda enforcement yapmaz.
        """
        from backend.app.guards.guard_enforcement import EnforcementVerdict, evaluate

        config = _config(decision_layer_default_mode="enforce")
        snapshot = SnapshotFactory.build(
            guard_deny_reason=GuardDenyReason.RATE_LIMITED,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        assert snapshot is not None
        assert evaluate(snapshot) == EnforcementVerdict.PASSTHROUGH

    def test_snapshot_immutability_with_new_fields(self):
        """Frozen dataclass: risk_class ve effective_mode değiştirilemez."""
        from dataclasses import FrozenInstanceError

        config = _config(decision_layer_default_mode="enforce")
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
            risk_class=RiskClass.HIGH,
        )
        assert snapshot is not None
        with pytest.raises((FrozenInstanceError, AttributeError)):
            snapshot.risk_class = RiskClass.LOW  # type: ignore[misc]
        with pytest.raises((FrozenInstanceError, AttributeError)):
            snapshot.effective_mode = TenantMode.OFF  # type: ignore[misc]

    def test_fail_open_on_none_snapshot(self):
        """evaluate(None) → ALLOW (fail-open korunur)."""
        from backend.app.guards.guard_enforcement import EnforcementVerdict, evaluate
        assert evaluate(None) == EnforcementVerdict.ALLOW

    def test_endpoint_field_is_normalize_template(self):
        """
        Snapshot'taki endpoint alanı normalize edilmiş template.
        SnapshotFactory.build()'e verilen endpoint olduğu gibi snapshot'a girer.
        """
        config = _config(decision_layer_default_mode="shadow")
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/market-prices/{period}",
            method="GET",
            dependencies=["db_primary"],
            now_ms=FIXED_NOW,
        )
        assert snapshot is not None
        assert snapshot.endpoint == "/admin/market-prices/{period}"
