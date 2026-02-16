"""
Runtime Guard Decision Layer — unit tests.

17 tests covering:
  - Signal derivation (3): stale, insufficient, all OK
  - Config freshness producer (4): empty, parse error, stale, fresh
  - CB mapping producer (2): miss, present
  - Hash (2): windowParams sensitivity, determinism
  - Snapshot immutability (1): frozen attribute assignment
  - Enforcement (5): passthrough, block_insufficient, block_stale, allow, fail-open

Feature: runtime-guard-decision, Task 8
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, timedelta

import pytest

from backend.app.guard_config import GuardConfig, GuardDenyReason
from backend.app.guards.guard_decision import (
    GuardDecisionSnapshot,
    GuardSignal,
    SignalName,
    SignalReasonCode,
    SignalStatus,
    SnapshotFactory,
    TenantMode,
    WindowParams,
    check_cb_mapping,
    check_config_freshness,
    compute_risk_context_hash,
    derive_signal_flags,
    parse_tenant_allowlist,
    parse_tenant_modes,
    resolve_tenant_mode,
    sanitize_metric_tenant,
    sanitize_tenant_id,
)
from backend.app.guards.guard_enforcement import (
    EnforcementVerdict,
    evaluate,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _signal(status: SignalStatus, name: SignalName = SignalName.CONFIG_FRESHNESS) -> GuardSignal:
    return GuardSignal(
        name=name,
        status=status,
        reason_code=SignalReasonCode.OK,
        observed_at_ms=1_000_000,
    )


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _iso_ago(hours: int) -> str:
    """ISO timestamp `hours` ago from now."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _config(**overrides) -> GuardConfig:
    """Build GuardConfig with test defaults. Uses model_construct to skip env."""
    defaults = {
        "schema_version": "1.0",
        "config_version": "test",
        "last_updated_at": _iso_ago(1),  # 1 hour ago — fresh
    }
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Signal Derivation (3 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeriveSignalFlags:
    """derive_signal_flags derives from signals only — no caller flags."""

    def test_stale_signal_sets_has_stale(self):
        signals = (
            _signal(SignalStatus.OK),
            _signal(SignalStatus.STALE, SignalName.CB_MAPPING),
        )
        has_stale, has_insufficient = derive_signal_flags(signals)
        assert has_stale is True
        assert has_insufficient is False

    def test_insufficient_signal_sets_has_insufficient(self):
        signals = (
            _signal(SignalStatus.INSUFFICIENT),
            _signal(SignalStatus.OK, SignalName.CB_MAPPING),
        )
        has_stale, has_insufficient = derive_signal_flags(signals)
        assert has_stale is False
        assert has_insufficient is True

    def test_all_ok_both_false(self):
        signals = (
            _signal(SignalStatus.OK),
            _signal(SignalStatus.OK, SignalName.CB_MAPPING),
        )
        has_stale, has_insufficient = derive_signal_flags(signals)
        assert has_stale is False
        assert has_insufficient is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Config Freshness Signal Producer (4 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckConfigFreshness:
    """Config freshness signal: empty, parse error, stale, fresh."""

    def test_empty_last_updated_at_insufficient(self):
        config = _config(last_updated_at="")
        sig = check_config_freshness(config, _now_ms(), WindowParams())
        assert sig.status == SignalStatus.INSUFFICIENT
        assert sig.reason_code == SignalReasonCode.CONFIG_TIMESTAMP_MISSING

    def test_unparseable_last_updated_at_insufficient(self):
        config = _config(last_updated_at="not-a-date-at-all")
        sig = check_config_freshness(config, _now_ms(), WindowParams())
        assert sig.status == SignalStatus.INSUFFICIENT
        assert sig.reason_code == SignalReasonCode.CONFIG_TIMESTAMP_PARSE_ERROR

    def test_stale_config(self):
        """last_updated_at 48h ago, max_config_age_ms=24h → STALE."""
        config = _config(last_updated_at=_iso_ago(48))
        wp = WindowParams(max_config_age_ms=86_400_000, clock_skew_allowance_ms=5_000)
        sig = check_config_freshness(config, _now_ms(), wp)
        assert sig.status == SignalStatus.STALE
        assert sig.reason_code == SignalReasonCode.CONFIG_STALE

    def test_fresh_config_ok(self):
        """last_updated_at 1h ago, max_config_age_ms=24h → OK."""
        config = _config(last_updated_at=_iso_ago(1))
        wp = WindowParams(max_config_age_ms=86_400_000, clock_skew_allowance_ms=5_000)
        sig = check_config_freshness(config, _now_ms(), wp)
        assert sig.status == SignalStatus.OK
        assert sig.reason_code == SignalReasonCode.OK


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CB Mapping Signal Producer (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckCbMapping:
    """CB mapping signal: miss vs present."""

    def test_no_dependencies_insufficient(self):
        sig = check_cb_mapping("/admin/test", None, _now_ms())
        assert sig.status == SignalStatus.INSUFFICIENT
        assert sig.reason_code == SignalReasonCode.CB_MAPPING_MISS

    def test_empty_list_insufficient(self):
        sig = check_cb_mapping("/admin/test", [], _now_ms())
        assert sig.status == SignalStatus.INSUFFICIENT
        assert sig.reason_code == SignalReasonCode.CB_MAPPING_MISS

    def test_dependencies_present_ok(self):
        sig = check_cb_mapping("/admin/test", ["db_primary"], _now_ms())
        assert sig.status == SignalStatus.OK


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Hash Computation (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeRiskContextHash:
    """Hash includes windowParams and is deterministic."""

    def test_different_window_params_different_hash(self):
        """Same inputs, different max_config_age_ms → different hash (R5)."""
        common = dict(
            tenant_id="default",
            endpoint="/admin/test",
            method="GET",
            config_hash="abc123",
            guard_deny_reason_name=None,
            derived_has_stale=False,
            derived_has_insufficient=False,
        )
        h1 = compute_risk_context_hash(
            **common,
            window_params=WindowParams(max_config_age_ms=86_400_000),
        )
        h2 = compute_risk_context_hash(
            **common,
            window_params=WindowParams(max_config_age_ms=43_200_000),
        )
        assert h1 != h2

    def test_same_inputs_same_hash(self):
        """Determinism: identical inputs → identical hash."""
        kwargs = dict(
            tenant_id="default",
            endpoint="/admin/test",
            method="GET",
            config_hash="abc123",
            window_params=WindowParams(),
            guard_deny_reason_name=None,
            derived_has_stale=False,
            derived_has_insufficient=False,
        )
        assert compute_risk_context_hash(**kwargs) == compute_risk_context_hash(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Snapshot Immutability (1 test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotImmutability:
    """Frozen dataclass: attribute assignment raises."""

    def test_cannot_mutate_snapshot(self):
        config = _config()
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=_now_ms(),
        )
        assert snapshot is not None
        with pytest.raises((FrozenInstanceError, AttributeError)):
            snapshot.tenant_id = "hacked"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SnapshotFactory (2 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshotFactory:
    """Factory builds correct snapshot; fails open on error."""

    def test_build_produces_valid_snapshot(self):
        config = _config(last_updated_at=_iso_ago(1))
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/market-prices",
            method="GET",
            dependencies=["db_primary"],
            now_ms=_now_ms(),
        )
        assert snapshot is not None
        assert snapshot.tenant_id == "default"
        assert snapshot.tenant_mode == TenantMode.SHADOW
        assert snapshot.derived_has_stale is False
        assert snapshot.derived_has_insufficient is False
        assert len(snapshot.signals) == 2
        assert len(snapshot.risk_context_hash) == 16

    def test_build_returns_none_on_exception(self):
        """SnapshotFactory.build() with broken config → None (fail-open)."""
        # Pass a non-GuardConfig object to trigger exception
        result = SnapshotFactory.build(
            guard_deny_reason=None,
            config=None,  # type: ignore[arg-type]
            endpoint="/test",
            method="GET",
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Enforcement (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnforcement:
    """Pure enforcement function — deterministic verdict."""

    def test_passthrough_on_existing_deny(self):
        """guard_deny_reason=RATE_LIMITED → PASSTHROUGH (429 semantics preserved)."""
        config = _config()
        snapshot = SnapshotFactory.build(
            guard_deny_reason=GuardDenyReason.RATE_LIMITED,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=_now_ms(),
        )
        assert evaluate(snapshot) == EnforcementVerdict.PASSTHROUGH

    def test_block_insufficient_when_no_deny_but_insufficient_signal(self):
        """No deny + insufficient signal → BLOCK_INSUFFICIENT."""
        config = _config(last_updated_at="")  # → INSUFFICIENT
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=_now_ms(),
        )
        assert snapshot is not None
        assert snapshot.derived_has_insufficient is True
        assert evaluate(snapshot) == EnforcementVerdict.BLOCK_INSUFFICIENT

    def test_block_stale_when_no_deny_but_stale_signal(self):
        """No deny + stale config → BLOCK_STALE."""
        config = _config(last_updated_at=_iso_ago(72))  # 72h ago
        wp = WindowParams(max_config_age_ms=86_400_000)  # 24h
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            window_params=wp,
            now_ms=_now_ms(),
        )
        assert snapshot is not None
        assert snapshot.derived_has_stale is True
        assert evaluate(snapshot) == EnforcementVerdict.BLOCK_STALE

    def test_allow_when_all_clear(self):
        """No deny + all signals OK → ALLOW."""
        config = _config(last_updated_at=_iso_ago(1))
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,
            config=config,
            endpoint="/admin/test",
            method="GET",
            dependencies=["db_primary"],
            now_ms=_now_ms(),
        )
        assert snapshot is not None
        assert evaluate(snapshot) == EnforcementVerdict.ALLOW

    def test_fail_open_on_none_snapshot(self):
        """snapshot=None → ALLOW (fail-open)."""
        assert evaluate(None) == EnforcementVerdict.ALLOW


# ═══════════════════════════════════════════════════════════════════════════════
# TenantMode enum — Task 2.1
# ═══════════════════════════════════════════════════════════════════════════════

class TestTenantMode:
    """TenantMode enum: str-based, exactly 3 values."""

    def test_values(self):
        assert TenantMode.SHADOW == "shadow"
        assert TenantMode.ENFORCE == "enforce"
        assert TenantMode.OFF == "off"

    def test_is_str_enum(self):
        assert isinstance(TenantMode.SHADOW, str)

    def test_construct_from_string(self):
        assert TenantMode("shadow") is TenantMode.SHADOW
        assert TenantMode("enforce") is TenantMode.ENFORCE
        assert TenantMode("off") is TenantMode.OFF

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            TenantMode("invalid")


# ═══════════════════════════════════════════════════════════════════════════════
# parse_tenant_modes — Task 2.2
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseTenantModes:
    """parse_tenant_modes: JSON → dict[str, TenantMode], fail-open."""

    def test_valid_json(self):
        raw = '{"tenantA": "enforce", "tenantB": "shadow", "tenantC": "off"}'
        result = parse_tenant_modes(raw)
        assert result == {
            "tenantA": TenantMode.ENFORCE,
            "tenantB": TenantMode.SHADOW,
            "tenantC": TenantMode.OFF,
        }

    def test_empty_string_returns_empty_dict(self):
        assert parse_tenant_modes("") == {}

    def test_whitespace_only_returns_empty_dict(self):
        assert parse_tenant_modes("   ") == {}

    def test_invalid_json_returns_empty_dict(self):
        assert parse_tenant_modes("{not valid json}") == {}

    def test_non_dict_json_returns_empty_dict(self):
        assert parse_tenant_modes('["a", "b"]') == {}

    def test_invalid_mode_value_skipped(self):
        raw = '{"tenantA": "enforce", "tenantB": "invalid_mode"}'
        result = parse_tenant_modes(raw)
        assert result == {"tenantA": TenantMode.ENFORCE}

    def test_all_invalid_modes_returns_empty_dict(self):
        raw = '{"tenantA": "bad", "tenantB": "worse"}'
        assert parse_tenant_modes(raw) == {}

    def test_mixed_valid_invalid(self):
        raw = '{"a": "shadow", "b": 123, "c": "enforce"}'
        result = parse_tenant_modes(raw)
        assert result == {"a": TenantMode.SHADOW, "c": TenantMode.ENFORCE}


# ═══════════════════════════════════════════════════════════════════════════════
# parse_tenant_allowlist — Task 2.3
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseTenantAllowlist:
    """parse_tenant_allowlist: JSON → frozenset[str], fail-open."""

    def test_valid_json_array(self):
        raw = '["tenantA", "tenantB"]'
        result = parse_tenant_allowlist(raw)
        assert result == frozenset({"tenantA", "tenantB"})

    def test_empty_string_returns_empty_frozenset(self):
        assert parse_tenant_allowlist("") == frozenset()

    def test_whitespace_only_returns_empty_frozenset(self):
        assert parse_tenant_allowlist("   ") == frozenset()

    def test_invalid_json_returns_empty_frozenset(self):
        assert parse_tenant_allowlist("[not valid") == frozenset()

    def test_non_list_json_returns_empty_frozenset(self):
        assert parse_tenant_allowlist('{"a": 1}') == frozenset()

    def test_empty_array_returns_empty_frozenset(self):
        assert parse_tenant_allowlist("[]") == frozenset()

    def test_numeric_items_converted_to_str(self):
        raw = '["a", 123]'
        result = parse_tenant_allowlist(raw)
        assert result == frozenset({"a", "123"})


# ═══════════════════════════════════════════════════════════════════════════════
# sanitize_tenant_id — Task 3.1
# ═══════════════════════════════════════════════════════════════════════════════

class TestSanitizeTenantId:
    """sanitize_tenant_id: normalize raw tenant_id to safe string."""

    def test_none_returns_default(self):
        assert sanitize_tenant_id(None) == "default"

    def test_empty_string_returns_default(self):
        assert sanitize_tenant_id("") == "default"

    def test_whitespace_only_returns_default(self):
        assert sanitize_tenant_id("   ") == "default"

    def test_tab_whitespace_returns_default(self):
        assert sanitize_tenant_id("\t\n") == "default"

    def test_normal_tenant_id_returned(self):
        assert sanitize_tenant_id("tenantA") == "tenantA"

    def test_strips_surrounding_whitespace(self):
        assert sanitize_tenant_id("  tenantB  ") == "tenantB"

    def test_default_string_passthrough(self):
        assert sanitize_tenant_id("default") == "default"


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_tenant_mode — Task 3.2
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveTenantMode:
    """resolve_tenant_mode: deterministic tenant mode resolution."""

    def test_tenant_in_map_returns_mapped_mode(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode("tenantA", TenantMode.SHADOW, modes)
        assert result == TenantMode.ENFORCE

    def test_tenant_not_in_map_returns_default(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode("tenantX", TenantMode.OFF, modes)
        assert result == TenantMode.OFF

    def test_none_tenant_id_returns_default_mode(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode(None, TenantMode.SHADOW, modes)
        assert result == TenantMode.SHADOW

    def test_empty_tenant_id_returns_default_mode(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode("", TenantMode.OFF, modes)
        assert result == TenantMode.OFF

    def test_whitespace_tenant_id_returns_default_mode(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode("   ", TenantMode.SHADOW, modes)
        assert result == TenantMode.SHADOW

    def test_empty_map_returns_default(self):
        result = resolve_tenant_mode("tenantA", TenantMode.ENFORCE, {})
        assert result == TenantMode.ENFORCE

    def test_none_tenant_with_default_in_map(self):
        """When 'default' key exists in map, None tenant_id resolves to it."""
        modes = {"default": TenantMode.OFF}
        result = resolve_tenant_mode(None, TenantMode.SHADOW, modes)
        assert result == TenantMode.OFF

    def test_strips_tenant_id_before_lookup(self):
        modes = {"tenantA": TenantMode.ENFORCE}
        result = resolve_tenant_mode("  tenantA  ", TenantMode.SHADOW, modes)
        assert result == TenantMode.ENFORCE

    def test_deterministic_same_inputs_same_output(self):
        modes = {"t1": TenantMode.ENFORCE, "t2": TenantMode.OFF}
        r1 = resolve_tenant_mode("t1", TenantMode.SHADOW, modes)
        r2 = resolve_tenant_mode("t1", TenantMode.SHADOW, modes)
        assert r1 == r2 == TenantMode.ENFORCE


class TestSanitizeMetricTenant:
    """sanitize_metric_tenant: cardinality-safe tenant label for metrics."""

    def test_tenant_in_allowlist_returns_tenant_id(self):
        result = sanitize_metric_tenant("tenantA", frozenset({"tenantA", "tenantB"}))
        assert result == "tenantA"

    def test_tenant_not_in_allowlist_returns_other(self):
        result = sanitize_metric_tenant("tenantX", frozenset({"tenantA", "tenantB"}))
        assert result == "_other"

    def test_empty_allowlist_returns_other(self):
        result = sanitize_metric_tenant("tenantA", frozenset())
        assert result == "_other"

    def test_empty_tenant_id_not_in_allowlist(self):
        result = sanitize_metric_tenant("", frozenset({"tenantA"}))
        assert result == "_other"

    def test_empty_tenant_id_in_allowlist(self):
        """Edge case: empty string is in allowlist."""
        result = sanitize_metric_tenant("", frozenset({"", "tenantA"}))
        assert result == ""

    def test_empty_allowlist_empty_tenant_id(self):
        result = sanitize_metric_tenant("", frozenset())
        assert result == "_other"
