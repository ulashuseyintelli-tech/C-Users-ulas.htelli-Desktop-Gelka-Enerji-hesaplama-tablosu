"""
Issue Payload Builder - Sprint 6.0

BUG_REPORT icin PII-safe issue payload uretir.
GitHub/Jira entegrasyonu icin hazir format.
"""

from dataclasses import dataclass, asdict
from typing import Any, Optional


@dataclass(frozen=True)
class IssuePayload:
    """Issue payload - BUG_REPORT icin."""
    title: str
    labels: list[str]
    severity: str
    dedupe_key: str
    invoice: dict[str, str]
    primary_flag: str
    category: str
    action: dict[str, str]
    all_flags: list[str]
    lookup_evidence: dict[str, Any]
    normalized_inputs: dict[str, Any]
    repro_hint: str
    
    def to_dict(self) -> dict[str, Any]:
        """Dict'e cevir (JSON serialization icin)."""
        return asdict(self)


class IssuePayloadBuilder:
    """
    PII-safe issue payload builder.
    
    Sadece allowlist'teki alanlar payload'a girer.
    Gercek fatura verisi (unvan, adres, abone no, vergi no, sayac no) ASLA girmez.
    """
    
    # PII-safe alanlar - sadece bunlar payload'a girebilir
    INPUT_ALLOWLIST = {
        "invoice_period",
        "consumption_kwh",
        "ptf_date",
        "yekdem_date",
        "market_price_source",
        "tariff_code",
        "tariff_period",
        "ck_meta_present",
        "distribution_line_present",
        "meta_distribution_source",
        "computed_distribution_unit_price",
        "distribution_unit_price_invoice",
        "distribution_mismatch_pct",
        "confidence",
        "json_repair_applied",
        "distribution_total_tl",
        "energy_total_tl",
        "total_amount_tl",
    }
    
    def build(
        self,
        *,
        incident: dict,
        dedupe_key: str,
        provider: str,
        invoice_id: str,
        period: str,
        calc_context: Optional[dict[str, Any]] = None,
        lookup_evidence: Optional[dict[str, Any]] = None,
    ) -> IssuePayload:
        """
        Issue payload olusturur.
        
        Args:
            incident: Incident dict (primary_flag, category, severity, action, all_flags)
            dedupe_key: Stabil dedupe key
            provider: Fatura saglayici
            invoice_id: Fatura ID
            period: YYYY-MM
            calc_context: Hesaplama context'i (redact edilecek)
            lookup_evidence: Lookup sonuclari
        
        Returns:
            IssuePayload instance
        """
        primary_flag = incident.get("primary_flag", "UNKNOWN")
        category = incident.get("category", "UNKNOWN")
        severity = incident.get("severity", "S2")
        action = incident.get("action") or {}
        all_flags = incident.get("all_flags") or []
        
        # Normalize + redact inputs (allowlist)
        ctx = dict(calc_context or {})
        ctx["invoice_period"] = period
        safe_inputs = {k: ctx.get(k) for k in self.INPUT_ALLOWLIST if k in ctx}
        
        # Lookup evidence: sadece status & source
        le = lookup_evidence or {}
        safe_lookup = {
            "market_price": {
                "status": le.get("market_price_status"),
                "source": le.get("market_price_source"),
            },
            "tariff": {
                "status": le.get("tariff_status"),
                "source": le.get("tariff_source"),
            },
        }
        
        # Repro hint: synthetic, PII yok
        repro_hint = self._build_repro_hint(primary_flag, all_flags)
        
        # Title ve labels
        title = f"[{primary_flag}] provider={provider} invoice={invoice_id} period={period}"
        labels = ["incident", category, primary_flag, action.get("owner", "unknown")]
        
        return IssuePayload(
            title=title,
            labels=labels,
            severity=severity,
            dedupe_key=dedupe_key,
            invoice={"provider": provider, "invoice_id": invoice_id, "period": period},
            primary_flag=primary_flag,
            category=category,
            action={
                "type": action.get("type"),
                "owner": action.get("owner"),
                "code": action.get("code"),
            },
            all_flags=all_flags,
            lookup_evidence=safe_lookup,
            normalized_inputs=safe_inputs,
            repro_hint=repro_hint,
        )
    
    def _build_repro_hint(self, primary_flag: str, all_flags: list[str]) -> str:
        """
        Repro hint olusturur - synthetic, PII yok.
        
        Gercek veri ASLA bu hint'e girmez.
        """
        hints = {
            "CALC_BUG": (
                "Create synthetic fixture: CK meta present, lookup performed, "
                "computed distribution absurd (0/negative/very low)."
            ),
            "MARKET_PRICE_MISSING": (
                "Create synthetic fixture: valid extraction fields but "
                "market price provider returns not_found for period."
            ),
            "TARIFF_LOOKUP_FAILED": (
                "Create synthetic fixture: tariff code present, "
                "tariff lookup returns not_found/failed."
            ),
            "TARIFF_META_MISSING": (
                "Create synthetic fixture: distribution_line_present OR "
                "expected CK meta, but tariff_meta missing."
            ),
            "CONSUMPTION_MISSING": (
                "Create synthetic fixture: missing consumption_kwh "
                "while other required fields present."
            ),
            "DISTRIBUTION_MISSING": (
                "Create synthetic fixture: valid invoice but "
                "distribution tariff lookup returns not_found."
            ),
            "DISTRIBUTION_MISMATCH": (
                "Create synthetic fixture: distribution_line_present, "
                "tariff lookup success, but values differ >3%."
            ),
            "MISSING_FIELDS": (
                "Create synthetic fixture: some required fields missing "
                "(invoice_date, period, etc.) but consumption present."
            ),
        }
        
        if primary_flag in hints:
            return hints[primary_flag]
        
        # Generic hint
        flags_str = ",".join(all_flags[:5]) if all_flags else primary_flag
        return f"Create synthetic fixture triggering primary_flag={primary_flag} with flags={flags_str}."
