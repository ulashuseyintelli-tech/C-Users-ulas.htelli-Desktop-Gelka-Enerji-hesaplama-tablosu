"""Phase F integration tests — enforcement decision engine.

Tests:
  F6.1   mode=off → action="pass"
  F6.2   mode=shadow → action="pass", shadow_result present
  F6.3   mode=enforce_soft, valid → action="pass"
  F6.4   mode=enforce_soft, invalid → action="warn"
  F6.5   mode=enforce_hard, blocker → action="block"
  F6.6   mode=enforce_hard, advisory only → action="warn"
  F6.7   mode=enforce_hard, valid → action="pass"
  F6.8   rollback: enforce_hard → shadow → action="pass"
  F6.9   custom blocker_codes override
  F6.10  metric counters
  F6.11  EnforcementDecision.to_dict() round-trip
  F6.12  canonical_to_validator_dict mapping
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.invoice.validation import (
    ENFORCE_BLOCKED_TOTAL,
    ENFORCE_SOFTWARN_TOTAL,
    ENFORCE_TOTAL,
    EnforcementConfig,
    EnforcementDecision,
    ValidationMode,
    canonical_to_validator_dict,
    enforce_validation,
    get_enforcement_counters,
    reset_enforcement_counters,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoices" / "validation_totals"


def _load(name: str) -> dict:
    fp = FIXTURE_DIR / name
    return json.loads(fp.read_text(encoding="utf-8"))["invoice"]


@pytest.fixture(autouse=True)
def _reset():
    reset_enforcement_counters()
    yield
    reset_enforcement_counters()


# -----------------------------------------------------------------------
# F6.1 — mode=off
# -----------------------------------------------------------------------

def test_mode_off_returns_pass() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.OFF)
    invoice = _load("payable_total_mismatch.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "pass"
    assert d.mode == ValidationMode.OFF
    assert len(d.errors) == 0


# -----------------------------------------------------------------------
# F6.2 — mode=shadow
# -----------------------------------------------------------------------

def test_mode_shadow_returns_pass_with_shadow_result() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.SHADOW)
    invoice = _load("totals_ok.json")
    d = enforce_validation(invoice, [], invoice_id="test-shadow", config=cfg)
    assert d.action == "pass"
    assert d.mode == ValidationMode.SHADOW
    # shadow_result may be None if sampling skips — that's fine for shadow mode


# -----------------------------------------------------------------------
# F6.3 — enforce_soft, valid invoice
# -----------------------------------------------------------------------

def test_enforce_soft_valid_returns_pass() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.ENFORCE_SOFT)
    invoice = _load("totals_ok.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "pass"


# -----------------------------------------------------------------------
# F6.4 — enforce_soft, invalid invoice
# -----------------------------------------------------------------------

def test_enforce_soft_invalid_returns_warn() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.ENFORCE_SOFT)
    invoice = _load("payable_total_mismatch.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "warn"
    assert len(d.errors) > 0
    codes = [e.code.value for e in d.errors]
    assert "PAYABLE_TOTAL_MISMATCH" in codes


# -----------------------------------------------------------------------
# F6.5 — enforce_hard, blocker code
# -----------------------------------------------------------------------

def test_enforce_hard_blocker_returns_block() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.ENFORCE_HARD)
    invoice = _load("payable_total_mismatch.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "block"
    assert "PAYABLE_TOTAL_MISMATCH" in d.blocker_codes


# -----------------------------------------------------------------------
# F6.6 — enforce_hard, advisory only
# -----------------------------------------------------------------------

def test_enforce_hard_advisory_only_returns_warn() -> None:
    cfg = EnforcementConfig(
        mode=ValidationMode.ENFORCE_HARD,
        blocker_codes=frozenset(),  # nothing is a blocker
    )
    invoice = _load("payable_total_mismatch.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "warn"
    assert len(d.blocker_codes) == 0


# -----------------------------------------------------------------------
# F6.7 — enforce_hard, valid invoice
# -----------------------------------------------------------------------

def test_enforce_hard_valid_returns_pass() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.ENFORCE_HARD)
    invoice = _load("totals_ok.json")
    d = enforce_validation(invoice, [], config=cfg)
    assert d.action == "pass"


# -----------------------------------------------------------------------
# F6.8 — rollback: enforce_hard → shadow
# -----------------------------------------------------------------------

def test_rollback_enforce_hard_to_shadow() -> None:
    invoice = _load("payable_total_mismatch.json")

    # enforce_hard → block
    cfg_hard = EnforcementConfig(mode=ValidationMode.ENFORCE_HARD)
    d1 = enforce_validation(invoice, [], config=cfg_hard)
    assert d1.action == "block"

    # flip to shadow → pass
    cfg_shadow = EnforcementConfig(mode=ValidationMode.SHADOW)
    d2 = enforce_validation(invoice, [], config=cfg_shadow)
    assert d2.action == "pass"


# -----------------------------------------------------------------------
# F6.9 — custom blocker_codes
# -----------------------------------------------------------------------

def test_custom_blocker_codes() -> None:
    cfg = EnforcementConfig(
        mode=ValidationMode.ENFORCE_HARD,
        blocker_codes=frozenset({"ZERO_CONSUMPTION"}),
    )
    # payable_total_mismatch is NOT in custom blockers → warn
    invoice_ptm = _load("payable_total_mismatch.json")
    d1 = enforce_validation(invoice_ptm, [], config=cfg)
    assert d1.action == "warn"

    # zero_consumption IS in custom blockers → block
    invoice_zc = _load("zero_consumption.json")
    d2 = enforce_validation(invoice_zc, [], config=cfg)
    assert d2.action == "block"
    assert "ZERO_CONSUMPTION" in d2.blocker_codes


# -----------------------------------------------------------------------
# F6.10 — metric counters
# -----------------------------------------------------------------------

def test_metric_counters() -> None:
    cfg_hard = EnforcementConfig(mode=ValidationMode.ENFORCE_HARD)
    cfg_soft = EnforcementConfig(mode=ValidationMode.ENFORCE_SOFT)

    invoice_bad = _load("payable_total_mismatch.json")
    invoice_ok = _load("totals_ok.json")

    enforce_validation(invoice_bad, [], config=cfg_hard)   # block
    enforce_validation(invoice_bad, [], config=cfg_soft)   # warn
    enforce_validation(invoice_ok, [], config=cfg_hard)    # pass

    c = get_enforcement_counters()
    assert c[ENFORCE_TOTAL] == 3
    assert c[ENFORCE_BLOCKED_TOTAL] == 1
    assert c[ENFORCE_SOFTWARN_TOTAL] == 1


# -----------------------------------------------------------------------
# F6.11 — to_dict round-trip
# -----------------------------------------------------------------------

def test_enforcement_decision_to_dict() -> None:
    cfg = EnforcementConfig(mode=ValidationMode.ENFORCE_HARD)
    invoice = _load("payable_total_mismatch.json")
    d = enforce_validation(invoice, [], config=cfg)
    as_dict = d.to_dict()

    assert as_dict["action"] == "block"
    assert as_dict["mode"] == "enforce_hard"
    assert isinstance(as_dict["errors"], list)
    assert isinstance(as_dict["blocker_codes"], list)
    # JSON-serializable
    json.dumps(as_dict)


# -----------------------------------------------------------------------
# F6.12 — canonical_to_validator_dict
# -----------------------------------------------------------------------

def test_canonical_to_validator_dict() -> None:
    from app.supplier_profiles import (
        CanonicalInvoice,
        InvoiceLine,
        LineCode,
        TaxBreakdown,
        Totals,
        VATInfo,
    )

    canonical = CanonicalInvoice(
        totals=Totals(total=1000.0, payable=1000.0),
        lines=[
            InvoiceLine(
                code=LineCode.ACTIVE_ENERGY,
                label="Enerji",
                qty_kwh=2400.0,
                unit_price=0.30,
                amount=720.0,
            ),
        ],
        taxes=TaxBreakdown(other=80.0),
        vat=VATInfo(amount=80.0),
    )

    d = canonical_to_validator_dict(canonical)

    assert d["totals"]["total"] == 1000.0
    assert d["totals"]["payable"] == 1000.0
    assert len(d["lines"]) == 1
    assert d["lines"][0]["qty_kwh"] == 2400.0
    assert d["lines"][0]["amount"] == 720.0
    assert isinstance(d["lines"][0]["qty_kwh"], float)  # not bool
    assert d["taxes_total"] == 80.0
    assert d["vat_amount"] == 80.0
