from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class LineItem:
    code: str
    label: str
    qty_kwh: Optional[float] = None
    unit_price_tl_per_kwh: Optional[float] = None
    amount_tl: Optional[float] = None

@dataclass
class Taxes:
    btv_tl: Optional[float] = None
    other_taxes_tl: Optional[float] = None

@dataclass
class VAT:
    rate: Optional[float] = None
    base_tl: Optional[float] = None
    amount_tl: Optional[float] = None

@dataclass
class Totals:
    subtotal_tl: Optional[float] = None
    total_tl: Optional[float] = None
    payable_tl: Optional[float] = None

@dataclass
class Invoice:
    supplier_profile: Optional[str] = None
    invoice_no: Optional[str] = None
    ettn: Optional[str] = None
    period: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None

    total_kwh: Optional[float] = None
    weighted_unit_price_tl_per_kwh: Optional[float] = None

    lines: List[LineItem] = field(default_factory=list)
    taxes: Taxes = field(default_factory=Taxes)
    vat: VAT = field(default_factory=VAT)
    totals: Totals = field(default_factory=Totals)

    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "supplier_profile": self.supplier_profile,
            "invoice_no": self.invoice_no,
            "ettn": self.ettn,
            "period": self.period,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "total_kwh": self.total_kwh,
            "weighted_unit_price_tl_per_kwh": self.weighted_unit_price_tl_per_kwh,
            "lines": [li.__dict__ for li in self.lines],
            "taxes": self.taxes.__dict__,
            "vat": self.vat.__dict__,
            "totals": self.totals.__dict__,
            "warnings": self.warnings,
            "errors": self.errors,
        }
