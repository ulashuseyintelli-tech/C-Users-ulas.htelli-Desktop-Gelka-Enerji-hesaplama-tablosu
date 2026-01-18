from __future__ import annotations
import re
from .models import LineItem
from .normalize import tr_to_float

def map_label_to_code(label: str) -> str:
    t = (label or "").lower()
    if "dağıtım" in t:
        return "distribution"
    if "yek" in t or "yekdem" in t:
        return "yek"
    if "vergi" in t or "btv" in t or "fon" in t:
        return "tax"
    # enerji bedeli / aktif
    return "active_energy"

def parse_line_items_from_json(lines_json) -> list[LineItem]:
    out=[]
    for row in lines_json or []:
        label = (row.get("label") or "").strip()
        qty = tr_to_float(row.get("qty_kwh"))
        unit_price = tr_to_float(row.get("unit_price_tl_per_kwh"))
        amount = tr_to_float(row.get("amount_tl"))
        out.append(LineItem(code=map_label_to_code(label), label=label, qty_kwh=qty, unit_price_tl_per_kwh=unit_price, amount_tl=amount))
    return out

def compute_total_kwh(lines: list[LineItem]) -> float | None:
    # SADECE kalemlerden: qty_kwh toplamı (enerji satırları dahil)
    vals=[li.qty_kwh for li in lines if li.qty_kwh is not None]
    return sum(vals) if vals else None

def compute_weighted_unit_price(lines: list[LineItem]) -> float | None:
    # ağırlıklı ortalama: sum(amount)/sum(qty) yalnız enerji satırları
    qty=0.0
    amt=0.0
    for li in lines:
        if li.code == "active_energy" and li.qty_kwh and li.amount_tl is not None:
            qty += li.qty_kwh
            amt += li.amount_tl
    if qty == 0:
        return None
    return amt/qty
