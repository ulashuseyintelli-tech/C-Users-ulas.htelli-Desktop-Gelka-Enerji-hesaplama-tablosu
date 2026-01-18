"""Incident Service - Sprint 8.3 + 8.5 (Actionability)"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# SPRINT 8.5: ACTIONABILITY


class ActionClass(str, Enum):
    """Incident action sınıflandırması."""
    VERIFY_OCR = "VERIFY_OCR"
    VERIFY_INVOICE_LOGIC = "VERIFY_INVOICE_LOGIC"
    ACCEPT_ROUNDING_TOLERANCE = "ACCEPT_ROUNDING_TOLERANCE"


class PrimarySuspect(str, Enum):
    """Ana şüpheli kategorileri."""
    OCR_LOCALE_SUSPECT = "OCR_LOCALE_SUSPECT"
    INVOICE_LOGIC = "INVOICE_LOGIC"
    ROUNDING = "ROUNDING"


# Sabit check listeleri (tam determinism için)
CHECKS_VERIFY_OCR: List[str] = [
    "Ondalık ayırıcı kontrol (, vs .)",
    "Binlik ayırıcı normalize edilmiş mi",
    "TL/kuruş scale hatası",
    "kWh × PTF çarpımı locale kayması",
]

CHECKS_VERIFY_INVOICE_LOGIC: List[str] = [
    "Mahsup/indirim/ek bedel satırı var mı",
    "KDV dahil/hariç karışıklığı",
    "Fatura toplamı override içeriyor mu",
    "Kalem eşleme hatası",
    "Dönem çakışması",
]

CHECKS_ACCEPT_ROUNDING: List[str] = [
    "Yuvarlama farkı beklenen aralıkta",
    "Kuruş hassasiyeti",
]

# Rounding tolerance thresholds
ROUNDING_DELTA_THRESHOLD = 10.0  # TL
ROUNDING_RATIO_THRESHOLD = 0.005  # %0.5


@dataclass
class ActionHint:
    action_class: ActionClass
    primary_suspect: PrimarySuspect
    recommended_checks: List[str]
    confidence_note: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "action_class": self.action_class.value,
            "primary_suspect": self.primary_suspect.value,
            "recommended_checks": self.recommended_checks,
            "confidence_note": self.confidence_note,
        }


def generate_action_hint(flag_code: str, mismatch_info: Optional[dict], extraction_confidence: float = 1.0) -> Optional[ActionHint]:
    if flag_code != "INVOICE_TOTAL_MISMATCH":
        return None
    if not mismatch_info:
        return None
    required_fields = {"has_mismatch", "delta", "ratio", "severity"}
    if not required_fields.issubset(mismatch_info.keys()):
        return None
    if not mismatch_info.get("has_mismatch", False):
        return None
    delta = mismatch_info.get("delta", 0)
    ratio = mismatch_info.get("ratio", 0)
    suspect_reason = mismatch_info.get("suspect_reason")
    if suspect_reason == "OCR_LOCALE_SUSPECT":
        return ActionHint(action_class=ActionClass.VERIFY_OCR, primary_suspect=PrimarySuspect.OCR_LOCALE_SUSPECT, recommended_checks=CHECKS_VERIFY_OCR.copy(), confidence_note=f"Extraction confidence: {extraction_confidence:.2f}")
    if delta < ROUNDING_DELTA_THRESHOLD and ratio < ROUNDING_RATIO_THRESHOLD:
        return ActionHint(action_class=ActionClass.ACCEPT_ROUNDING_TOLERANCE, primary_suspect=PrimarySuspect.ROUNDING, recommended_checks=CHECKS_ACCEPT_ROUNDING.copy(), confidence_note=None)
    return ActionHint(action_class=ActionClass.VERIFY_INVOICE_LOGIC, primary_suspect=PrimarySuspect.INVOICE_LOGIC, recommended_checks=CHECKS_VERIFY_INVOICE_LOGIC.copy(), confidence_note=None)


class Severity:
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"


class Category:
    PARSE_FAIL = "PARSE_FAIL"
    TARIFF_MISSING = "TARIFF_MISSING"
    PRICE_MISSING = "PRICE_MISSING"
    MISMATCH = "MISMATCH"
    OUTLIER = "OUTLIER"
    VALIDATION_FAIL = "VALIDATION_FAIL"
    CALCULATION_ERROR = "CALCULATION_ERROR"
    JSON_REPAIR = "JSON_REPAIR"
    TARIFF_META_MISSING = "TARIFF_META_MISSING"
    CONSUMPTION_MISSING = "CONSUMPTION_MISSING"
    CALC_BUG = "CALC_BUG"


@dataclass
class QualityFlag:
    code: str
    severity: str
    message: str
    deduction: int = 0


QUALITY_FLAGS = {
    "MARKET_PRICE_MISSING": QualityFlag("MARKET_PRICE_MISSING", Severity.S1, "PTF/YEKDEM referans fiyati bulunamadi", 50),
    "TARIFF_LOOKUP_FAILED": QualityFlag("TARIFF_LOOKUP_FAILED", Severity.S1, "EPDK tarife lookup basarisiz", 40),
    "DISTRIBUTION_MISSING": QualityFlag("DISTRIBUTION_MISSING", Severity.S1, "Dagitim birim fiyati bulunamadi", 50),
    "TARIFF_META_MISSING": QualityFlag("TARIFF_META_MISSING", Severity.S1, "Tarife meta bilgisi okunamadi", 45),
    "CONSUMPTION_MISSING": QualityFlag("CONSUMPTION_MISSING", Severity.S1, "Tuketim degeri bulunamadi", 50),
    "MISSING_FIELDS": QualityFlag("MISSING_FIELDS", Severity.S2, "Eksik alanlar var", 20),
    "TOTAL_AVG_UNIT_PRICE_USED": QualityFlag("TOTAL_AVG_UNIT_PRICE_USED", Severity.S2, "Toplam ortalama birim fiyat kullanildi", 20),
    "DISTRIBUTION_MISMATCH": QualityFlag("DISTRIBUTION_MISMATCH", Severity.S2, "Dagitim fiyati uyusmazligi", 15),
    "INVOICE_TOTAL_MISMATCH": QualityFlag("INVOICE_TOTAL_MISMATCH", Severity.S2, "Fatura toplami ile hesaplanan toplam uyusmuyor", 25),
    "CALC_BUG": QualityFlag("CALC_BUG", Severity.S1, "Hesaplama hatasi", 50),
    "JSON_REPAIR_APPLIED": QualityFlag("JSON_REPAIR_APPLIED", Severity.S3, "JSON repair uygulandi", 10),
    "LOW_CONFIDENCE": QualityFlag("LOW_CONFIDENCE", Severity.S3, "Dusuk extraction confidence", 10),
    "VALIDATION_WARNINGS": QualityFlag("VALIDATION_WARNINGS", Severity.S3, "Validation uyarilari var", 5),
    "OUTLIER_PTF": QualityFlag("OUTLIER_PTF", Severity.S4, "PTF degeri olagandisi", 5),
    "OUTLIER_CONSUMPTION": QualityFlag("OUTLIER_CONSUMPTION", Severity.S4, "Tuketim degeri olagandisi", 5),
}


FLAG_PRIORITY = {"CALC_BUG": 5, "MARKET_PRICE_MISSING": 10, "CONSUMPTION_MISSING": 15, "TARIFF_LOOKUP_FAILED": 20, "TARIFF_META_MISSING": 25, "DISTRIBUTION_MISSING": 30, "INVOICE_TOTAL_MISMATCH": 35, "MISSING_FIELDS": 40, "TOTAL_AVG_UNIT_PRICE_USED": 50, "DISTRIBUTION_MISMATCH": 60, "JSON_REPAIR_APPLIED": 70, "LOW_CONFIDENCE": 80, "VALIDATION_WARNINGS": 90, "OUTLIER_PTF": 100, "OUTLIER_CONSUMPTION": 110}


class IncidentAction:
    USER_FIX = "USER_FIX"
    RETRY_LOOKUP = "RETRY_LOOKUP"
    FALLBACK_OK = "FALLBACK_OK"
    BUG_REPORT = "BUG_REPORT"


class IncidentOwner:
    USER = "user"
    EXTRACTION = "extraction"
    TARIFF = "tariff"
    MARKET_PRICE = "market_price"
    CALC = "calc"


class HintCode:
    PTF_YEKDEM_CHECK = "PTF_YEKDEM_CHECK"
    MANUAL_META_ENTRY = "MANUAL_META_ENTRY"
    ENGINE_REGRESSION = "ENGINE_REGRESSION"
    EPDK_TARIFF_LOOKUP = "EPDK_TARIFF_LOOKUP"
    DISTRIBUTION_SOURCE_CHECK = "DISTRIBUTION_SOURCE_CHECK"
    DISTRIBUTION_MISMATCH_REVIEW = "DISTRIBUTION_MISMATCH_REVIEW"
    MISSING_FIELDS_COMPLETE = "MISSING_FIELDS_COMPLETE"
    CONSUMPTION_MANUAL_ENTRY = "CONSUMPTION_MANUAL_ENTRY"
    AVG_PRICE_FALLBACK = "AVG_PRICE_FALLBACK"
    JSON_REPAIR_REVIEW = "JSON_REPAIR_REVIEW"
    LOW_CONFIDENCE_REVIEW = "LOW_CONFIDENCE_REVIEW"
    VALIDATION_REVIEW = "VALIDATION_REVIEW"
    OUTLIER_PTF_REVIEW = "OUTLIER_PTF_REVIEW"
    OUTLIER_CONSUMPTION_REVIEW = "OUTLIER_CONSUMPTION_REVIEW"
    INVOICE_TOTAL_MISMATCH_REVIEW = "INVOICE_TOTAL_MISMATCH_REVIEW"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ActionRecommendation:
    type: str
    owner: str
    code: str
    hint_text: str


ACTION_MAP = {"CALC_BUG": ActionRecommendation(IncidentAction.BUG_REPORT, IncidentOwner.CALC, HintCode.ENGINE_REGRESSION, "engine regression"), "MARKET_PRICE_MISSING": ActionRecommendation(IncidentAction.RETRY_LOOKUP, IncidentOwner.MARKET_PRICE, HintCode.PTF_YEKDEM_CHECK, "PTF/YEKDEM kontrol"), "CONSUMPTION_MISSING": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.CONSUMPTION_MANUAL_ENTRY, "manuel gir"), "TARIFF_LOOKUP_FAILED": ActionRecommendation(IncidentAction.RETRY_LOOKUP, IncidentOwner.TARIFF, HintCode.EPDK_TARIFF_LOOKUP, "EPDK lookup"), "TARIFF_META_MISSING": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.MANUAL_META_ENTRY, "manuel meta"), "DISTRIBUTION_MISSING": ActionRecommendation(IncidentAction.RETRY_LOOKUP, IncidentOwner.TARIFF, HintCode.DISTRIBUTION_SOURCE_CHECK, "dagitim kontrol"), "MISSING_FIELDS": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.MISSING_FIELDS_COMPLETE, "eksik alanlar"), "TOTAL_AVG_UNIT_PRICE_USED": ActionRecommendation(IncidentAction.FALLBACK_OK, IncidentOwner.EXTRACTION, HintCode.AVG_PRICE_FALLBACK, "ortalama fiyat"), "DISTRIBUTION_MISMATCH": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.DISTRIBUTION_MISMATCH_REVIEW, "dagitim uyusmazligi"), "INVOICE_TOTAL_MISMATCH": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.INVOICE_TOTAL_MISMATCH_REVIEW, "toplam uyusmazligi"), "JSON_REPAIR_APPLIED": ActionRecommendation(IncidentAction.FALLBACK_OK, IncidentOwner.EXTRACTION, HintCode.JSON_REPAIR_REVIEW, "JSON repair"), "LOW_CONFIDENCE": ActionRecommendation(IncidentAction.USER_FIX, IncidentOwner.USER, HintCode.LOW_CONFIDENCE_REVIEW, "dusuk confidence"), "VALIDATION_WARNINGS": ActionRecommendation(IncidentAction.FALLBACK_OK, IncidentOwner.EXTRACTION, HintCode.VALIDATION_REVIEW, "validation uyari"), "OUTLIER_PTF": ActionRecommendation(IncidentAction.FALLBACK_OK, IncidentOwner.MARKET_PRICE, HintCode.OUTLIER_PTF_REVIEW, "PTF outlier"), "OUTLIER_CONSUMPTION": ActionRecommendation(IncidentAction.FALLBACK_OK, IncidentOwner.USER, HintCode.OUTLIER_CONSUMPTION_REVIEW, "tuketim outlier")}


def get_action_recommendation(flag_code: str) -> dict:
    rec = ACTION_MAP.get(flag_code)
    if not rec:
        return {"type": IncidentAction.USER_FIX, "owner": IncidentOwner.USER, "code": HintCode.UNKNOWN, "hint_text": "Incele"}
    return {"type": rec.type, "owner": rec.owner, "code": rec.code, "hint_text": rec.hint_text}


def normalize_flags(flags: List[str]) -> List[str]:
    unique = list(dict.fromkeys(flags))
    return sorted(unique, key=lambda f: FLAG_PRIORITY.get(f, 999))


def check_calc_bug_conditions(validation: Optional[dict], calculation: Optional[dict]) -> Tuple[bool, str]:
    if not calculation:
        return False, ""
    validation = validation or {}
    has_ck_input = validation.get("distribution_tariff_key") or validation.get("distribution_computed_from_tariff") or validation.get("tariff_meta") or calculation.get("distribution_tariff_key")
    if not has_ck_input:
        return False, "CK input yok"
    dist_source = calculation.get("meta_distribution_source", "")
    if dist_source in ["not_found", ""]:
        return False, "Lookup yapilmadi"
    dist_total = calculation.get("distribution_total_tl", 0)
    consumption = calculation.get("consumption_kwh", 0)
    if dist_total == 0 and consumption and consumption > 0:
        return True, f"Dagitim 0 TL hesaplandi (consumption={consumption})"
    if dist_total < 0:
        return True, f"Dagitim negatif: {dist_total} TL"
    if consumption and consumption > 1000 and dist_total > 0 and dist_total < 1:
        return True, f"Dagitim asiri dusuk: {dist_total} TL (consumption={consumption})"
    return False, ""


def select_primary_flag(flags: List[str]) -> Optional[str]:
    if not flags:
        return None
    normalized = normalize_flags(flags)
    return normalized[0] if normalized else None


def get_secondary_flags(flags: List[str], primary: str) -> List[str]:
    normalized = normalize_flags(flags)
    return [f for f in normalized if f != primary]


def flag_to_category(flag_code: str) -> str:
    if flag_code == "TARIFF_META_MISSING":
        return Category.TARIFF_META_MISSING
    elif flag_code in ["TARIFF_LOOKUP_FAILED", "DISTRIBUTION_MISSING"]:
        return Category.TARIFF_MISSING
    elif flag_code == "MARKET_PRICE_MISSING":
        return Category.PRICE_MISSING
    elif flag_code == "CONSUMPTION_MISSING":
        return Category.CONSUMPTION_MISSING
    elif flag_code == "CALC_BUG":
        return Category.CALC_BUG
    elif "MISMATCH" in flag_code:
        return Category.MISMATCH
    elif "OUTLIER" in flag_code:
        return Category.OUTLIER
    elif flag_code == "JSON_REPAIR_APPLIED":
        return Category.JSON_REPAIR
    else:
        return Category.VALIDATION_FAIL


@dataclass
class QualityScore:
    score: int
    grade: str
    flags: List[str] = field(default_factory=list)
    flag_details: List[dict] = field(default_factory=list)


def calculate_quality_score(extraction: dict, validation: dict, calculation: Optional[dict], calculation_error: Optional[str], debug_meta: Optional[dict]) -> QualityScore:
    score = 100
    flags = []
    flag_details = []
    def add_flag(flag_code: str, extra_info: str = "", severity_override: str = None, suspect_reason: str = None, delta: float = None, ratio: float = None):
        nonlocal score
        if flag_code in QUALITY_FLAGS:
            qf = QUALITY_FLAGS[flag_code]
            score -= qf.deduction
            flags.append(flag_code)
            severity = severity_override if severity_override else qf.severity
            detail = {"code": flag_code, "severity": severity, "message": qf.message + (f" ({extra_info})" if extra_info else ""), "deduction": qf.deduction}
            if suspect_reason:
                detail["suspect_reason"] = suspect_reason
            if delta is not None:
                detail["delta"] = delta
            if ratio is not None:
                detail["ratio"] = ratio
            flag_details.append(detail)
    if calculation_error:
        if "referans fiyat bulunamadi" in calculation_error.lower():
            add_flag("MARKET_PRICE_MISSING")
        elif "dagitim" in calculation_error.lower():
            add_flag("DISTRIBUTION_MISSING")
        elif "tuketim" in calculation_error.lower() or "consumption" in calculation_error.lower():
            add_flag("CONSUMPTION_MISSING")
        else:
            add_flag("TARIFF_LOOKUP_FAILED", calculation_error[:100])
    if validation:
        if not validation.get("is_ready_for_pricing", True):
            missing = validation.get("missing_fields", [])
            if missing:
                if "consumption_kwh" in missing:
                    add_flag("CONSUMPTION_MISSING")
                else:
                    add_flag("MISSING_FIELDS", ", ".join(missing[:3]))
        if validation.get("warnings"):
            add_flag("VALIDATION_WARNINGS")
        if validation.get("distribution_tariff_meta_missing"):
            add_flag("TARIFF_META_MISSING")
        elif validation.get("distribution_tariff_lookup_failed"):
            add_flag("TARIFF_LOOKUP_FAILED")
        if validation.get("distribution_line_mismatch"):
            add_flag("DISTRIBUTION_MISMATCH")
    if calculation:
        dist_source = calculation.get("meta_distribution_source", "")
        if dist_source == "not_found":
            if "TARIFF_META_MISSING" not in flags and "TARIFF_LOOKUP_FAILED" not in flags:
                add_flag("DISTRIBUTION_MISSING")
        if calculation.get("meta_distribution_mismatch_warning"):
            if "DISTRIBUTION_MISMATCH" not in flags:
                add_flag("DISTRIBUTION_MISMATCH")
        pricing_source = calculation.get("meta_pricing_source", "")
        if pricing_source == "not_found":
            add_flag("MARKET_PRICE_MISSING")
        elif pricing_source == "default":
            add_flag("MARKET_PRICE_MISSING", "default degerler kullanildi")
        dist_total = calculation.get("distribution_total_tl", 0)
        dist_src = calculation.get("meta_distribution_source", "")
        if dist_src not in ["not_found", ""] and dist_total == 0:
            consumption = calculation.get("consumption_kwh", 0)
            if consumption and consumption > 0:
                add_flag("CALC_BUG", "dagitim 0 TL hesaplandi")
        if calculation.get("meta_total_mismatch"):
            mismatch_info = calculation.get("meta_total_mismatch_info", {})
            delta = mismatch_info.get("delta", 0)
            ratio = mismatch_info.get("ratio", 0)
            severity = mismatch_info.get("severity", "S2")
            suspect_reason = mismatch_info.get("suspect_reason")
            add_flag("INVOICE_TOTAL_MISMATCH", f"fark={delta:.2f} TL (%{ratio*100:.1f})", severity_override=severity, suspect_reason=suspect_reason, delta=delta, ratio=ratio)
    if debug_meta:
        if debug_meta.get("json_repair_applied"):
            add_flag("JSON_REPAIR_APPLIED")
        if debug_meta.get("warnings"):
            for w in debug_meta.get("warnings", [])[:2]:
                if "mismatch" in w.lower() and "DISTRIBUTION_MISMATCH" not in flags:
                    add_flag("DISTRIBUTION_MISMATCH", w[:50])
    if extraction:
        consumption = extraction.get("consumption_kwh", {})
        if consumption and consumption.get("confidence", 1) < 0.7:
            add_flag("LOW_CONFIDENCE", "consumption")
        unit_price = extraction.get("current_active_unit_price_tl_per_kwh", {})
        if unit_price and unit_price.get("confidence", 1) < 0.7:
            add_flag("LOW_CONFIDENCE", "unit_price")
    score = max(0, min(100, score))
    if score >= 80:
        grade = "OK"
    elif score >= 50:
        grade = "CHECK"
    else:
        grade = "BAD"
    return QualityScore(score=score, grade=grade, flags=flags, flag_details=flag_details)


DEDUPE_WINDOW_HOURS = 24


def generate_invoice_fingerprint(supplier: str = "", invoice_no: str = "", period: str = "", consumption_kwh: float = 0, total_amount: float = 0) -> str:
    parts = [str(supplier or "").lower().strip(), str(invoice_no or "").strip(), str(period or "").strip(), f"{float(consumption_kwh or 0):.2f}", f"{float(total_amount or 0):.2f}"]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def generate_dedupe_key(tenant_id: str, category: str, period: str = "", invoice_fingerprint: str = "") -> str:
    parts = [tenant_id or "default", category or "UNKNOWN", period or "", invoice_fingerprint or ""]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def find_existing_incident(db: Session, dedupe_key: str, window_hours: int = DEDUPE_WINDOW_HOURS) -> Optional[int]:
    from .database import Incident
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    existing = db.query(Incident).filter(Incident.dedupe_key == dedupe_key, Incident.created_at > cutoff, Incident.status != "RESOLVED").first()
    return existing.id if existing else None


def increment_incident_occurrence(db: Session, incident_id: int) -> bool:
    from .database import Incident
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        return False
    incident.occurrence_count = (incident.occurrence_count or 1) + 1
    incident.last_seen_at = datetime.utcnow()
    db.commit()
    logger.info(f"[INCIDENT] Dedupe hit: #{incident_id} occurrence_count={incident.occurrence_count}")
    return True


def create_incident(db: Session, trace_id: str, severity: str, category: str, message: str, tenant_id: str = "default", invoice_id: Optional[str] = None, offer_id: Optional[int] = None, details: Optional[dict] = None, period: str = "", invoice_fingerprint: str = "") -> int:
    from .database import Incident
    dedupe_key = generate_dedupe_key(tenant_id, category, period, invoice_fingerprint)
    existing_id = find_existing_incident(db, dedupe_key)
    if existing_id:
        increment_incident_occurrence(db, existing_id)
        return existing_id
    now = datetime.utcnow()
    incident = Incident(trace_id=trace_id, tenant_id=tenant_id, invoice_id=invoice_id, offer_id=offer_id, severity=severity, category=category, message=message, details_json=details, status="OPEN", dedupe_key=dedupe_key, occurrence_count=1, first_seen_at=now, last_seen_at=now)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    logger.warning(f"[INCIDENT] Created: {severity} {category} - {message[:100]} (trace={trace_id})")
    return incident.id


def create_incidents_from_quality(db: Session, trace_id: str, quality: QualityScore, tenant_id: str = "default", invoice_id: Optional[str] = None, period: str = "", invoice_fingerprint: str = "", extraction_confidence: float = 1.0) -> List[int]:
    critical_flags = [fd for fd in quality.flag_details if fd["severity"] in [Severity.S1, Severity.S2]]
    if not critical_flags:
        return []
    flag_codes = [fd["code"] for fd in critical_flags]
    all_flags = normalize_flags(flag_codes)
    primary_flag = select_primary_flag(flag_codes)
    if not primary_flag:
        return []
    secondary_flags = get_secondary_flags(flag_codes, primary_flag)
    primary_category = flag_to_category(primary_flag)
    secondary_categories = list(set(flag_to_category(f) for f in secondary_flags))
    primary_severity = Severity.S2
    mismatch_info = None
    for fd in critical_flags:
        if fd["code"] == primary_flag:
            primary_severity = fd["severity"]
            if fd["code"] == "INVOICE_TOTAL_MISMATCH":
                mismatch_info = {"has_mismatch": True, "delta": fd.get("delta", 0), "ratio": fd.get("ratio", 0), "severity": fd["severity"], "suspect_reason": fd.get("suspect_reason")}
            break
    primary_message = QUALITY_FLAGS.get(primary_flag, QualityFlag(primary_flag, Severity.S2, primary_flag, 0)).message
    message = f"{primary_message} (+{len(secondary_flags)} ek sorun)" if secondary_flags else primary_message
    action_info = get_action_recommendation(primary_flag)
    action_hint = generate_action_hint(primary_flag, mismatch_info, extraction_confidence)
    action_hint_dict = action_hint.to_dict() if action_hint else None
    incident_id = create_incident(db=db, trace_id=trace_id, severity=primary_severity, category=primary_category, message=message, tenant_id=tenant_id, invoice_id=invoice_id, details={"primary_flag": primary_flag, "primary_category": primary_category, "all_flags": all_flags, "flag_details": critical_flags, "secondary_flags": secondary_flags, "secondary_categories": secondary_categories, "quality_score": quality.score, "quality_grade": quality.grade, "action": action_info, "action_hint": action_hint_dict}, period=period, invoice_fingerprint=invoice_fingerprint)
    return [incident_id]


def get_incidents(db: Session, tenant_id: str = "default", status: Optional[str] = None, severity: Optional[str] = None, category: Optional[str] = None, limit: int = 100) -> List[dict]:
    from .database import Incident
    query = db.query(Incident).filter(Incident.tenant_id == tenant_id)
    if status:
        query = query.filter(Incident.status == status)
    if severity:
        query = query.filter(Incident.severity == severity)
    if category:
        query = query.filter(Incident.category == category)
    records = query.order_by(Incident.last_seen_at.desc()).limit(limit).all()
    return [{"id": r.id, "trace_id": r.trace_id, "invoice_id": r.invoice_id, "severity": r.severity, "category": r.category, "message": r.message, "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else None, "details": r.details_json, "occurrence_count": r.occurrence_count or 1, "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None, "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None} for r in records]


def update_incident_status(db: Session, incident_id: int, status: str, resolution_note: Optional[str] = None, resolved_by: Optional[str] = None) -> bool:
    from .database import Incident
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        return False
    incident.status = status
    if resolution_note:
        incident.resolution_note = resolution_note
    if resolved_by:
        incident.resolved_by = resolved_by
    if status == "RESOLVED":
        incident.resolved_at = datetime.utcnow()
    db.commit()
    return True


VALID_ENVIRONMENTS = {"development", "staging", "production"}


def validate_environment(env: str) -> Tuple[bool, str]:
    if not env:
        return True, ""
    if env not in VALID_ENVIRONMENTS:
        return False, f"Invalid ENV: '{env}'. Must be one of: {', '.join(sorted(VALID_ENVIRONMENTS))}"
    return True, ""


def check_production_guard(env: str, api_key_enabled: bool, api_key: str) -> Tuple[bool, str]:
    env_valid, env_error = validate_environment(env)
    if not env_valid:
        return False, env_error
    if env != "production":
        return True, ""
    if not api_key_enabled:
        return False, "Production requires ADMIN_API_KEY_ENABLED=true"
    if not api_key or len(api_key) < 32:
        return False, "Production requires ADMIN_API_KEY with at least 32 characters"
    return True, ""
