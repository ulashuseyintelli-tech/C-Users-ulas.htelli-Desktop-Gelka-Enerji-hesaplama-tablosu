"""Invoice validation engine (Faz A / 4.1).

Rules implemented: ETTN, periods (T1/T2/T3), reactive penalty.
"""

from __future__ import annotations

import datetime
import re

from .error_codes import ValidationErrorCode
from .types import InvoiceValidationError, InvoiceValidationResult

_ETTN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_REQUIRED_PERIOD_CODES = {"T1", "T2", "T3"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _err(
    code: ValidationErrorCode,
    field: str,
    message: str,
) -> InvoiceValidationError:
    return InvoiceValidationError(code=code, field=field, message=message)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Rule: ETTN
# ---------------------------------------------------------------------------

def _validate_ettn(invoice: dict) -> list[InvoiceValidationError]:
    ettn = invoice.get("ettn")

    if ettn is None or ettn == "":
        return [_err(ValidationErrorCode.MISSING_FIELD, "ettn", "ettn is missing or empty")]

    if not isinstance(ettn, str):
        return [_err(ValidationErrorCode.INVALID_FORMAT, "ettn", "ettn must be a string")]

    if not _ETTN_RE.match(ettn.strip()):
        return [_err(ValidationErrorCode.INVALID_ETTN, "ettn", "ettn does not match UUID format")]

    return []


# ---------------------------------------------------------------------------
# Rule: Periods (T1/T2/T3)
# ---------------------------------------------------------------------------

def _validate_periods(invoice: dict) -> list[InvoiceValidationError]:
    periods = invoice.get("periods")
    errors: list[InvoiceValidationError] = []

    # 1 — missing / empty
    if not periods or not isinstance(periods, list) or len(periods) == 0:
        return [_err(ValidationErrorCode.MISSING_FIELD, "periods", "periods is missing or empty")]

    # 2 — required codes present
    codes = {p.get("code") for p in periods if isinstance(p, dict)}
    if not _REQUIRED_PERIOD_CODES.issubset(codes):
        missing = _REQUIRED_PERIOD_CODES - codes
        errors.append(
            _err(
                ValidationErrorCode.MISSING_FIELD,
                "periods.codes",
                f"Missing required period codes: {sorted(missing)}",
            )
        )
        return errors  # can't do date/value checks without required codes

    # Build lookup for T1/T2/T3 only
    by_code: dict[str, dict] = {}
    for p in periods:
        if isinstance(p, dict) and p.get("code") in _REQUIRED_PERIOD_CODES:
            by_code[p["code"]] = p

    # 3 — date parsing
    parsed_starts: list[datetime.date] = []
    parsed_ends: list[datetime.date] = []
    date_ok = True

    for code in sorted(_REQUIRED_PERIOD_CODES):
        p = by_code[code]
        for date_key, collector in [("start", parsed_starts), ("end", parsed_ends)]:
            raw = p.get(date_key)
            if not isinstance(raw, str):
                errors.append(
                    _err(
                        ValidationErrorCode.INVALID_DATETIME,
                        f"periods.{code}.{date_key}",
                        f"{code}.{date_key} is not a valid date string",
                    )
                )
                date_ok = False
                continue
            try:
                collector.append(datetime.date.fromisoformat(raw))
            except (ValueError, TypeError):
                errors.append(
                    _err(
                        ValidationErrorCode.INVALID_DATETIME,
                        f"periods.{code}.{date_key}",
                        f"{code}.{date_key} cannot be parsed as YYYY-MM-DD",
                    )
                )
                date_ok = False

    # 4 — consistency (only if all dates parsed)
    if date_ok and parsed_starts and parsed_ends:
        if len(set(parsed_starts)) > 1 or len(set(parsed_ends)) > 1:
            errors.append(
                _err(
                    ValidationErrorCode.INCONSISTENT_PERIODS,
                    "periods",
                    "T1/T2/T3 start or end dates are not consistent",
                )
            )

    # 5 & 6 — kwh / amount type + negative
    for code in sorted(_REQUIRED_PERIOD_CODES):
        p = by_code[code]
        for val_key in ("kwh", "amount"):
            val = p.get(val_key)
            if not _is_number(val):
                errors.append(
                    _err(
                        ValidationErrorCode.INVALID_FORMAT,
                        f"periods.{code}.{val_key}",
                        f"{code}.{val_key} must be a number",
                    )
                )
            elif val < 0:
                errors.append(
                    _err(
                        ValidationErrorCode.NEGATIVE_VALUE,
                        f"periods.{code}.{val_key}",
                        f"{code}.{val_key} is negative",
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# Rule: Reactive penalty (bidirectional)
# ---------------------------------------------------------------------------

def _validate_reactive(invoice: dict) -> list[InvoiceValidationError]:
    reactive = invoice.get("reactive")
    if reactive is None:
        return []  # optional section — skip

    errors: list[InvoiceValidationError] = []

    has_amount = "penalty_amount" in reactive
    has_kvarh = "penalty_kvarh" in reactive

    # 1 — bidirectional missing field
    if has_amount and not has_kvarh:
        errors.append(
            _err(ValidationErrorCode.MISSING_FIELD, "reactive.penalty_kvarh", "penalty_kvarh is missing")
        )
        return errors
    if has_kvarh and not has_amount:
        errors.append(
            _err(ValidationErrorCode.MISSING_FIELD, "reactive.penalty_amount", "penalty_amount is missing")
        )
        return errors
    if not has_amount and not has_kvarh:
        return errors  # both absent — nothing to validate

    amount = reactive["penalty_amount"]
    kvarh = reactive["penalty_kvarh"]

    # 2 — type check
    for key, val in [("penalty_amount", amount), ("penalty_kvarh", kvarh)]:
        if not _is_number(val):
            errors.append(
                _err(ValidationErrorCode.INVALID_FORMAT, f"reactive.{key}", f"{key} must be a number")
            )
    if errors:
        return errors  # can't do numeric checks

    # 3 — negative
    for key, val in [("penalty_amount", amount), ("penalty_kvarh", kvarh)]:
        if val < 0:
            errors.append(
                _err(ValidationErrorCode.NEGATIVE_VALUE, f"reactive.{key}", f"{key} is negative")
            )
    if errors:
        return errors

    # 4 — bidirectional mismatch
    if amount > 0 and kvarh <= 0:
        errors.append(
            _err(
                ValidationErrorCode.REACTIVE_PENALTY_MISMATCH,
                "reactive",
                "penalty_amount > 0 but penalty_kvarh <= 0",
            )
        )
    elif kvarh > 0 and amount <= 0:
        errors.append(
            _err(
                ValidationErrorCode.REACTIVE_PENALTY_MISMATCH,
                "reactive",
                "penalty_kvarh > 0 but penalty_amount <= 0",
            )
        )

    return errors


# ---------------------------------------------------------------------------
# Rule: Totals — payable ≈ total, lines+taxes+vat ≈ total
# Ported from CanonicalInvoice.validate() rules 1 & 2
# ---------------------------------------------------------------------------

_PAYABLE_TOLERANCE = 5.0  # TL — birebir eski approx(a, b, tol=5.0)


def _validate_totals(invoice: dict) -> list[InvoiceValidationError]:
    totals = invoice.get("totals")
    if not isinstance(totals, dict):
        return []  # optional section — skip

    errors: list[InvoiceValidationError] = []

    total = totals.get("total")
    payable = totals.get("payable")

    # Rule 1: payable ≈ total
    if _is_number(total) and _is_number(payable):
        if abs(payable - total) > _PAYABLE_TOLERANCE:
            errors.append(
                _err(
                    ValidationErrorCode.PAYABLE_TOTAL_MISMATCH,
                    "totals",
                    f"payable={payable}, total={total}, diff={abs(payable - total):.2f}",
                )
            )

    # Rule 2: lines + taxes + vat ≈ total
    if _is_number(total):
        lines = invoice.get("lines")
        if isinstance(lines, list) and len(lines) > 0:
            lines_sum = sum(
                line.get("amount", 0)
                for line in lines
                if isinstance(line, dict) and _is_number(line.get("amount"))
            )
            taxes_total = invoice.get("taxes_total", 0)
            vat_amount = invoice.get("vat_amount", 0)
            if not _is_number(taxes_total):
                taxes_total = 0
            if not _is_number(vat_amount):
                vat_amount = 0

            calculated = lines_sum + taxes_total + vat_amount
            tol = max(5.0, total * 0.01)
            if abs(calculated - total) > tol:
                errors.append(
                    _err(
                        ValidationErrorCode.TOTAL_MISMATCH,
                        "totals.total",
                        f"calculated={calculated:.2f}, extracted={total:.2f}, diff={abs(calculated - total):.2f}",
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# Rule: Lines — zero consumption, line crosscheck
# Ported from CanonicalInvoice.validate() rules 3 & 4
# ---------------------------------------------------------------------------

_LINE_CROSSCHECK_TOLERANCE = 0.02  # %2 relatif — birebir eski crosscheck(tolerance=0.02)


def _validate_lines(invoice: dict) -> list[InvoiceValidationError]:
    lines = invoice.get("lines")
    if not isinstance(lines, list) or len(lines) == 0:
        return []  # optional section — skip

    errors: list[InvoiceValidationError] = []

    # Rule 3: zero consumption
    qty_values = [
        line.get("qty_kwh")
        for line in lines
        if isinstance(line, dict) and _is_number(line.get("qty_kwh"))
    ]
    if qty_values:
        consumption_kwh = sum(qty_values)
        if consumption_kwh <= 0:
            errors.append(
                _err(
                    ValidationErrorCode.ZERO_CONSUMPTION,
                    "lines",
                    f"total consumption_kwh={consumption_kwh}",
                )
            )

    # Rule 4: line crosscheck (qty_kwh × unit_price ≈ amount)
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        qty = line.get("qty_kwh")
        price = line.get("unit_price")
        amount = line.get("amount")

        if not (_is_number(qty) and _is_number(price) and _is_number(amount)):
            continue  # can't check
        if amount == 0:
            continue

        calculated = qty * price
        delta = abs((calculated - amount) / amount)
        if delta > _LINE_CROSSCHECK_TOLERANCE:
            label = line.get("label", f"line[{i}]")
            errors.append(
                _err(
                    ValidationErrorCode.LINE_CROSSCHECK_FAIL,
                    f"lines[{i}]",
                    f"{label}: qty={qty}, price={price}, amount={amount}, calculated={calculated:.2f}",
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(
    invoice: dict,
    supplier: str | None = None,  # 4.1'de unused; 4.2 hook point
) -> InvoiceValidationResult:
    """Validate a canonical invoice dict.

    Invariant: result.valid == (len(result.errors) == 0)
    """
    errors: list[InvoiceValidationError] = []
    errors.extend(_validate_ettn(invoice))
    errors.extend(_validate_periods(invoice))
    errors.extend(_validate_reactive(invoice))
    errors.extend(_validate_totals(invoice))
    errors.extend(_validate_lines(invoice))
    return InvoiceValidationResult(valid=len(errors) == 0, errors=errors)
