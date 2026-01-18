from __future__ import annotations
from .models import Invoice

def approx(a: float | None, b: float | None, tol_abs: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a-b) <= tol_abs

def approx_pct(a: float | None, b: float | None, tol_pct: float) -> bool:
    if a is None or b is None:
        return True
    if b == 0:
        return True
    return abs(a-b) <= abs(b) * tol_pct

def validate_invoice(inv: Invoice) -> Invoice:
    # Rule: payable ~ total (5 TL)
    if inv.totals.payable_tl is not None and inv.totals.total_tl is not None:
        if not approx(inv.totals.payable_tl, inv.totals.total_tl, 5.0):
            inv.errors.append("TOTAL_PAYABLE_MISMATCH")
    # Rule: subtotal + vat ≈ total (%1)
    if inv.totals.subtotal_tl is not None and inv.vat.amount_tl is not None and inv.totals.total_tl is not None:
        calc = inv.totals.subtotal_tl + inv.vat.amount_tl
        if not approx_pct(calc, inv.totals.total_tl, 0.01):
            inv.errors.append("TOTAL_CALC_MISMATCH")
    # Rule: if total_kwh missing -> warning
    if inv.total_kwh is None:
        inv.warnings.append("WARN_TOTAL_KWH_MISSING")
    return inv

def demand_price_rule(demand_qty, demand_unit_price, demand_amount, warnings: list[str], errors: list[str]):
    # En mantıklısı: unit price yoksa 0 kabul, sadece uyarı
    if demand_qty is not None and demand_unit_price is None and demand_amount is None:
        warnings.append("WARN_DEMAND_PRICE_MISSING")
