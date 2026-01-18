from __future__ import annotations
from dateutil import parser as dtparser

def tr_to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(" ", "")
    if not s:
        return None
    # 1.234,56 -> 1234.56
    s2 = s.replace(".", "").replace(",", ".")
    try:
        return float(s2)
    except Exception:
        return None

def parse_date_iso(s: str | None) -> str | None:
    if not s:
        return None
    try:
        d = dtparser.parse(s, dayfirst=True).date()
        return d.isoformat()
    except Exception:
        return None
