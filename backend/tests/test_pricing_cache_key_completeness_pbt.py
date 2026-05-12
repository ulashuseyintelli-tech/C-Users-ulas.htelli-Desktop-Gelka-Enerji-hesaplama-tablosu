"""PBT suite for pricing-cache-key-completeness bugfix.

Validates Decision 3 invariants (INV-1..INV-5) + Decision 1 version isolation +
Decision 10 voltage_level normalization counter-property.

**Exploratory phase expectation:** On UNFIXED code, INV-2 (T10) MUST FAIL.
This is the ampirical proof that the test actually catches the bug. Only after
observing this FAIL we proceed to T1 (version bump).

References:
- Spec: .kiro/specs/pricing-cache-key-completeness/{bugfix.md, design.md, tasks.md}
- B1 baseline collision: baselines/2026-05-12_pre-ptf-unification_baseline.json
  (2026-03 LOW vs HIGH profiles returned same response_hash)
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from app.pricing.pricing_cache import build_cache_key


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
kwh_strategy = st.floats(
    min_value=0.0,
    max_value=1_000_000.0,
    allow_nan=False,
    allow_infinity=False,
)
voltage_strategy = st.sampled_from(["og", "ag"])  # canonical domain only
use_template_strategy = st.one_of(st.booleans(), st.none())
period_strategy = st.sampled_from(["2026-01", "2026-02", "2026-03", "2026-04"])


def base_kwargs(**overrides) -> dict:
    """Return a baseline kwargs dict for build_cache_key.

    Core 7 fields fixed at stable values; override via kwargs when a test
    needs to vary one or more of them. The 5 new fields (t1/t2/t3,
    use_template, voltage_level) are NOT included by default — tests that
    exercise them pass explicit values.
    """
    defaults = dict(
        customer_id=None,
        period="2026-03",
        multiplier=1.1,
        dealer_commission_pct=0.0,
        imbalance_params={
            "forecast_error_rate": 0.05,
            "imbalance_cost_tl_per_mwh": 150.0,
            "smf_based_imbalance_enabled": False,
        },
        template_name=None,
        template_monthly_kwh=None,
    )
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# INV-2 — t1/t2/t3 discriminator (Decision 3 + Requirement 2.1)
# This is THE critical test: on UNFIXED code it MUST FAIL, proving the
# B1 baseline collision at (25k/12.5k/12.5k) vs (250k/125k/125k).
# ---------------------------------------------------------------------------
class TestInv2T1T2T3Discriminator:
    """INV-2: different (t1,t2,t3) tuple MUST produce different cache keys.

    Tasks reference: T10 (INV-2 PBT).
    """

    @example(low_t1=25_000.0, low_t2=12_500.0, low_t3=12_500.0, high_delta=225_000.0)
    @given(
        low_t1=kwh_strategy,
        low_t2=kwh_strategy,
        low_t3=kwh_strategy,
        high_delta=st.floats(
            min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False
        ),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_different_consumption_different_key(
        self, low_t1, low_t2, low_t3, high_delta
    ):
        """B1 baseline regression: (25k, 12.5k, 12.5k) vs (250k, 125k, 125k)
        MUST produce different keys. Currently produces SAME key (BUG)."""
        low_kwargs = base_kwargs()
        low_kwargs.update(
            t1_kwh=low_t1,
            t2_kwh=low_t2,
            t3_kwh=low_t3,
            use_template=False,
            voltage_level="og",
        )
        high_kwargs = base_kwargs()
        high_kwargs.update(
            t1_kwh=low_t1 + high_delta,
            t2_kwh=low_t2,
            t3_kwh=low_t3,
            use_template=False,
            voltage_level="og",
        )
        key_low = build_cache_key(**low_kwargs)
        key_high = build_cache_key(**high_kwargs)
        assert key_low != key_high, (
            f"INV-2 violation: t1={low_t1} and t1={low_t1 + high_delta} "
            f"(other fields identical) produced the SAME cache key {key_low[:16]}... "
            "This is the B1 baseline collision — cache key ignores t1_kwh."
        )

    def test_b1_baseline_replay_low_vs_high(self):
        """Deterministic replay of the B1 baseline collision.

        This is the exact payload pair that produced matching response_hash
        `95d6bada181889af...` in baselines/2026-05-12_pre-ptf-unification_baseline.json
        for period 2026-03 under LOW and HIGH profiles.
        """
        low_kwargs = base_kwargs()
        low_kwargs.update(
            t1_kwh=25_000, t2_kwh=12_500, t3_kwh=12_500,
            use_template=False, voltage_level="og",
        )
        high_kwargs = base_kwargs()
        high_kwargs.update(
            t1_kwh=250_000, t2_kwh=125_000, t3_kwh=125_000,
            use_template=False, voltage_level="og",
        )
        key_low = build_cache_key(**low_kwargs)
        key_high = build_cache_key(**high_kwargs)
        assert key_low != key_high, (
            "B1 BASELINE REGRESSION: LOW (25k/12.5k/12.5k) and HIGH "
            "(250k/125k/125k) produced the same cache key. This is the "
            "production financial bug — two customers with 10x different "
            "consumption share the same cached response."
        )


# ---------------------------------------------------------------------------
# INV-1 — Determinism (Decision 3 / Requirement 2.4)
# ---------------------------------------------------------------------------
class TestInv1Determinism:
    """INV-1: Same full input MUST produce same cache key (idempotent).

    Tasks reference: T9.
    """

    @given(
        t1=kwh_strategy,
        t2=kwh_strategy,
        t3=kwh_strategy,
        use_tpl=use_template_strategy,
        vlt=voltage_strategy,
        mult=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_same_input_same_key(self, t1, t2, t3, use_tpl, vlt, mult):
        kwargs = base_kwargs(multiplier=mult)
        kwargs.update(
            t1_kwh=t1, t2_kwh=t2, t3_kwh=t3,
            use_template=use_tpl, voltage_level=vlt,
        )
        assert build_cache_key(**kwargs) == build_cache_key(**kwargs)


# ---------------------------------------------------------------------------
# INV-3 — voltage_level (Decision 3 + 10 / Requirement 2.9)
# ---------------------------------------------------------------------------
class TestInv3VoltageLevel:
    """INV-3: canonical voltage values discriminate; None ≡ "og" ≡ empty.

    Tasks reference: T11.
    """

    def test_og_and_ag_different_key(self):
        """Canonical domain: "og" vs "ag" MUST produce different keys."""
        kwargs_og = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                     "t3_kwh": 25_000, "use_template": False, "voltage_level": "og"}
        kwargs_ag = {**kwargs_og, "voltage_level": "ag"}
        assert build_cache_key(**kwargs_og) != build_cache_key(**kwargs_ag)

    def test_none_equals_og_counter_property(self):
        """Decision 10: None normalizes to canonical "og" — SAME key."""
        kwargs_og = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                     "t3_kwh": 25_000, "use_template": False, "voltage_level": "og"}
        kwargs_none = {**kwargs_og, "voltage_level": None}
        assert build_cache_key(**kwargs_og) == build_cache_key(**kwargs_none), (
            "Decision 10 violation: voltage_level=None and voltage_level='og' "
            "MUST produce the same cache key (both canonical 'og')."
        )

    def test_empty_string_equals_og(self):
        """Falsy values (empty string) normalize to "og" (or-truthy pattern)."""
        kwargs_og = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                     "t3_kwh": 25_000, "use_template": False, "voltage_level": "og"}
        kwargs_empty = {**kwargs_og, "voltage_level": ""}
        assert build_cache_key(**kwargs_og) == build_cache_key(**kwargs_empty)


# ---------------------------------------------------------------------------
# INV-4 — use_template (Decision 3 / Requirement 2.2)
# ---------------------------------------------------------------------------
class TestInv4UseTemplate:
    """INV-4: use_template domain {True, False, None} — all pairwise different.

    None is preserved (NOT converted to False) because it represents
    "validation path not chosen" vs explicit False = "T1/T2/T3 enforced".

    Tasks reference: T12.
    """

    def _base(self, **overrides):
        kwargs = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                  "t3_kwh": 25_000, "voltage_level": "og"}
        kwargs.update(overrides)
        return kwargs

    def test_true_vs_false_different_key(self):
        assert build_cache_key(**self._base(use_template=True)) != build_cache_key(**self._base(use_template=False))

    def test_none_vs_false_different_key(self):
        """None ≠ False: None = not validated path, False = explicitly chosen."""
        assert build_cache_key(**self._base(use_template=None)) != build_cache_key(**self._base(use_template=False))

    def test_none_vs_true_different_key(self):
        assert build_cache_key(**self._base(use_template=None)) != build_cache_key(**self._base(use_template=True))


# ---------------------------------------------------------------------------
# INV-5 — Core-7 regression preserved (Decision 3 / Requirement 3.3)
# ---------------------------------------------------------------------------
class TestInv5Core7Regression:
    """INV-5: Changing any one of the original 7 fields MUST still change the key.

    Tasks reference: T13.
    """

    def _full_kwargs(self, **overrides):
        kwargs = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                  "t3_kwh": 25_000, "use_template": False, "voltage_level": "og"}
        kwargs.update(overrides)
        return kwargs

    @pytest.mark.parametrize(
        "field,value_a,value_b",
        [
            ("customer_id", "CUST-A", "CUST-B"),
            ("period", "2026-01", "2026-02"),
            ("multiplier", 1.1, 1.2),
            ("dealer_commission_pct", 0.0, 5.0),
            ("template_name", "3_vardiya_sanayi", "ticari_buro"),
            ("template_monthly_kwh", 50_000.0, 100_000.0),
        ],
    )
    def test_single_core_field_change(self, field, value_a, value_b):
        key_a = build_cache_key(**self._full_kwargs(**{field: value_a}))
        key_b = build_cache_key(**self._full_kwargs(**{field: value_b}))
        assert key_a != key_b, f"INV-5 violation: changing {field} did not change key"

    def test_imbalance_params_change(self):
        """imbalance_params is a dict; dict value change must discriminate."""
        base_imb = {"forecast_error_rate": 0.05, "imbalance_cost_tl_per_mwh": 50.0,
                    "smf_based_imbalance_enabled": False}
        alt_imb = {**base_imb, "forecast_error_rate": 0.10}
        key_a = build_cache_key(**self._full_kwargs(imbalance_params=base_imb))
        key_b = build_cache_key(**self._full_kwargs(imbalance_params=alt_imb))
        assert key_a != key_b


# ---------------------------------------------------------------------------
# Version isolation (Decision 1 / Requirement 2.6)
# ---------------------------------------------------------------------------
class TestCacheVersionIsolation:
    """v1 ↔ v2 isolation: version bump MUST produce different key for same input.

    Tasks reference: T14.
    """

    def test_v1_v2_produce_different_keys(self, monkeypatch):
        """Ampirical proof that bumping CACHE_KEY_VERSION isolates old records."""
        kwargs = {**base_kwargs(), "t1_kwh": 50_000, "t2_kwh": 25_000,
                  "t3_kwh": 25_000, "use_template": False, "voltage_level": "og"}
        key_v2 = build_cache_key(**kwargs)
        # Simulate v1 behavior: patch the version constant
        monkeypatch.setattr("app.pricing.pricing_cache.CACHE_KEY_VERSION", "v1")
        key_v1 = build_cache_key(**kwargs)
        assert key_v1 != key_v2, (
            "Version bump ineffective: v1 and v2 produced the same key "
            "for identical input. _cache_version prefix not applied?"
        )
