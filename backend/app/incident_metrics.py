"""
Incident Metrics - Sprint 8.2 + 8.6 (System Health Dashboard) + 8.8 (Config)

KPI query fonksiyonları ve metrik hesaplamaları.

Sprint 8.2 Metrikler:
- Daily counts
- Status distribution
- Retry funnel
- Top primary flags / action codes
- MTTR (Mean Time To Resolve)
- False success rate
- Stuck pending recompute count

Sprint 8.6 Metrikler:
- Mismatch ratio histogram
- Drift detection (triple guard)
- Top offenders (by rate, not count)
- Action class distribution
- System health report
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date
from typing import Optional, List, Dict, Tuple
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_

from .database import Incident
from .resolution_reasons import ResolutionReason
from .config import THRESHOLDS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.6: SYSTEM HEALTH DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════


class AlertType(str, Enum):
    """Drift alert tipleri."""
    S1_RATE_DRIFT = "S1_RATE_DRIFT"
    OCR_SUSPECT_DRIFT = "OCR_SUSPECT_DRIFT"
    MISMATCH_RATE_DRIFT = "MISMATCH_RATE_DRIFT"


# Histogram bucket sınırları (ratio olarak)
HISTOGRAM_BUCKETS = [
    (0.00, 0.02, "0-2%"),
    (0.02, 0.05, "2-5%"),
    (0.05, 0.10, "5-10%"),
    (0.10, 0.20, "10-20%"),
    (0.20, float('inf'), "20%+"),
]

# Drift detection thresholds - NOW FROM CONFIG
# DEPRECATED: Use THRESHOLDS.Drift.* directly
DRIFT_MIN_SAMPLE = THRESHOLDS.Drift.MIN_SAMPLE
DRIFT_MIN_ABSOLUTE_DELTA = THRESHOLDS.Drift.MIN_ABSOLUTE_DELTA
DRIFT_RATE_MULTIPLIER = THRESHOLDS.Drift.RATE_MULTIPLIER

# Top offenders minimum invoice threshold
TOP_OFFENDERS_MIN_INVOICES = THRESHOLDS.Drift.TOP_OFFENDERS_MIN_INVOICES

# Mismatch ratio epsilon (zero protection)
RATIO_EPSILON = 0.01

# Stuck threshold - NOW FROM CONFIG
STUCK_THRESHOLD_MINUTES = THRESHOLDS.Recovery.STUCK_MINUTES


@dataclass
class PeriodStats:
    """Dönem istatistikleri."""
    start_date: date
    end_date: date
    total_invoices: int = 0
    mismatch_count: int = 0
    s1_count: int = 0
    s2_count: int = 0
    ocr_suspect_count: int = 0
    
    @property
    def mismatch_rate(self) -> float:
        """Mismatch oranı."""
        if self.total_invoices == 0:
            return 0.0
        return self.mismatch_count / self.total_invoices
    
    @property
    def s1_rate(self) -> float:
        """S1 oranı (S1 / (S1 + S2))."""
        total_severity = self.s1_count + self.s2_count
        if total_severity == 0:
            return 0.0
        return self.s1_count / total_severity
    
    @property
    def ocr_suspect_rate(self) -> float:
        """OCR suspect oranı."""
        if self.mismatch_count == 0:
            return 0.0
        return self.ocr_suspect_count / self.mismatch_count


@dataclass
class DriftAlert:
    """Drift alert bilgisi."""
    alert_type: AlertType
    old_rate: float
    new_rate: float
    old_count: int
    new_count: int
    triggered: bool
    message: str = ""
    
    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type.value,
            "old_rate": round(self.old_rate, 4),
            "new_rate": round(self.new_rate, 4),
            "old_count": self.old_count,
            "new_count": self.new_count,
            "triggered": self.triggered,
            "message": self.message,
        }


@dataclass
class TopOffender:
    """Provider bazlı mismatch bilgisi."""
    provider: str
    total_count: int
    mismatch_count: int
    
    @property
    def mismatch_rate(self) -> float:
        """Mismatch oranı (rate, count değil!)."""
        if self.total_count == 0:
            return 0.0
        return self.mismatch_count / self.total_count
    
    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "total_count": self.total_count,
            "mismatch_count": self.mismatch_count,
            "mismatch_rate": round(self.mismatch_rate, 4),
        }


@dataclass
class HistogramBucket:
    """Histogram bucket bilgisi."""
    label: str
    min_ratio: float
    max_ratio: float
    count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "count": self.count,
        }


@dataclass
class ActionClassDistribution:
    """Action class dağılımı."""
    verify_ocr: int = 0
    verify_invoice_logic: int = 0
    accept_rounding: int = 0
    
    @property
    def total(self) -> int:
        return self.verify_ocr + self.verify_invoice_logic + self.accept_rounding
    
    def to_dict(self) -> dict:
        total = self.total
        return {
            "VERIFY_OCR": self.verify_ocr,
            "VERIFY_INVOICE_LOGIC": self.verify_invoice_logic,
            "ACCEPT_ROUNDING_TOLERANCE": self.accept_rounding,
            "total": total,
            "rates": {
                "VERIFY_OCR": round(self.verify_ocr / total, 4) if total > 0 else 0,
                "VERIFY_INVOICE_LOGIC": round(self.verify_invoice_logic / total, 4) if total > 0 else 0,
                "ACCEPT_ROUNDING_TOLERANCE": round(self.accept_rounding / total, 4) if total > 0 else 0,
            }
        }


@dataclass
class SystemHealthReport:
    """Sistem sağlık raporu."""
    generated_at: datetime
    current_period: PeriodStats
    previous_period: PeriodStats
    drift_alerts: List[DriftAlert] = field(default_factory=list)
    top_offenders_by_rate: List[TopOffender] = field(default_factory=list)
    top_offenders_by_count: List[TopOffender] = field(default_factory=list)
    histogram: List[HistogramBucket] = field(default_factory=list)
    action_class_distribution: Optional[ActionClassDistribution] = None
    
    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "current_period": {
                "start_date": self.current_period.start_date.isoformat(),
                "end_date": self.current_period.end_date.isoformat(),
                "total_invoices": self.current_period.total_invoices,
                "mismatch_count": self.current_period.mismatch_count,
                "mismatch_rate": round(self.current_period.mismatch_rate, 4),
                "s1_count": self.current_period.s1_count,
                "s2_count": self.current_period.s2_count,
                "s1_rate": round(self.current_period.s1_rate, 4),
                "ocr_suspect_count": self.current_period.ocr_suspect_count,
                "ocr_suspect_rate": round(self.current_period.ocr_suspect_rate, 4),
            },
            "previous_period": {
                "start_date": self.previous_period.start_date.isoformat(),
                "end_date": self.previous_period.end_date.isoformat(),
                "total_invoices": self.previous_period.total_invoices,
                "mismatch_count": self.previous_period.mismatch_count,
                "mismatch_rate": round(self.previous_period.mismatch_rate, 4),
                "s1_count": self.previous_period.s1_count,
                "s2_count": self.previous_period.s2_count,
                "s1_rate": round(self.previous_period.s1_rate, 4),
                "ocr_suspect_count": self.previous_period.ocr_suspect_count,
                "ocr_suspect_rate": round(self.previous_period.ocr_suspect_rate, 4),
            },
            "drift_alerts": [a.to_dict() for a in self.drift_alerts],
            "top_offenders_by_rate": [o.to_dict() for o in self.top_offenders_by_rate],
            "top_offenders_by_count": [o.to_dict() for o in self.top_offenders_by_count],
            "histogram": [b.to_dict() for b in self.histogram],
            "action_class_distribution": self.action_class_distribution.to_dict() if self.action_class_distribution else None,
        }


def get_ratio_bucket(ratio: float) -> str:
    """
    Ratio değerini histogram bucket'ına eşle.
    
    Bucket'lar: [0-2%, 2-5%, 5-10%, 10-20%, 20%+]
    """
    for min_r, max_r, label in HISTOGRAM_BUCKETS:
        if min_r <= ratio < max_r:
            return label
    return "20%+"  # Fallback


def calculate_mismatch_histogram(
    incidents: List[dict],
) -> List[HistogramBucket]:
    """
    Mismatch ratio histogram hesapla.
    
    Args:
        incidents: Incident listesi (details_json içinde ratio bilgisi olmalı)
    
    Returns:
        Histogram bucket listesi
    """
    # Initialize buckets
    buckets = {
        label: HistogramBucket(label=label, min_ratio=min_r, max_ratio=max_r)
        for min_r, max_r, label in HISTOGRAM_BUCKETS
    }
    
    for inc in incidents:
        details = inc.get("details") or {}
        flag_details = details.get("flag_details", [])
        
        for fd in flag_details:
            if fd.get("code") == "INVOICE_TOTAL_MISMATCH":
                ratio = fd.get("ratio", 0)
                bucket_label = get_ratio_bucket(ratio)
                if bucket_label in buckets:
                    buckets[bucket_label].count += 1
                break
    
    # Return in order
    return [buckets[label] for _, _, label in HISTOGRAM_BUCKETS]


def detect_drift(
    old_count: int,
    new_count: int,
    old_total: int,
    new_total: int,
    alert_type: AlertType,
) -> DriftAlert:
    """
    Drift detection (triple guard + zero rate handling).
    
    Alarm koşulu:
    1. curr_total >= min_sample (20)
    2. abs(curr_count - prev_count) >= min_abs_delta (5)
    3. prev_rate > 0 ise: curr_rate >= 2 * prev_rate
       prev_rate == 0 ise: rate guard atlanır, sadece count guard yeterli
    
    Args:
        old_count: Önceki dönem count
        new_count: Yeni dönem count
        old_total: Önceki dönem total
        new_total: Yeni dönem total
        alert_type: Alert tipi
    
    Returns:
        DriftAlert
    """
    old_rate = old_count / old_total if old_total > 0 else 0
    new_rate = new_count / new_total if new_total > 0 else 0
    
    abs_delta = abs(new_count - old_count)
    
    # Guard 1: Minimum sample
    has_min_sample = new_total >= DRIFT_MIN_SAMPLE
    
    # Guard 2: Minimum absolute delta
    has_min_delta = abs_delta >= DRIFT_MIN_ABSOLUTE_DELTA
    
    # Guard 3: Rate doubling (with zero rate handling)
    if old_rate > 0:
        # Normal case: check rate doubling
        has_rate_condition = new_rate >= DRIFT_RATE_MULTIPLIER * old_rate
    else:
        # Zero rate case: rate guard atlanır, count guard yeterli
        # Ama yine de new_count >= min_abs_delta olmalı
        has_rate_condition = new_count >= DRIFT_MIN_ABSOLUTE_DELTA
    
    triggered = has_min_sample and has_min_delta and has_rate_condition
    
    message = ""
    if triggered:
        if old_rate > 0:
            message = f"{alert_type.value}: {old_rate:.1%} → {new_rate:.1%} (delta={abs_delta})"
        else:
            message = f"{alert_type.value}: 0% → {new_rate:.1%} (new count={new_count})"
    
    return DriftAlert(
        alert_type=alert_type,
        old_rate=old_rate,
        new_rate=new_rate,
        old_count=old_count,
        new_count=new_count,
        triggered=triggered,
        message=message,
    )


def get_top_offenders_by_rate(
    db: Session,
    tenant_id: str,
    start_date: date,
    end_date: date,
    limit: int = 10,
    min_volume: int = TOP_OFFENDERS_MIN_INVOICES,
) -> List[TopOffender]:
    """
    Top offenders by mismatch RATE (not count!).
    
    Minimum volume guard: Sadece total_count >= min_volume olan provider'lar dahil.
    Bu, düşük hacimli provider'ların yüksek rate ile yanıltıcı görünmesini engeller.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        start_date: Başlangıç tarihi
        end_date: Bitiş tarihi
        limit: Maksimum sonuç sayısı
        min_volume: Minimum invoice sayısı (varsayılan: 20)
    
    Returns:
        TopOffender listesi (rate'e göre sıralı, yüksekten düşüğe)
    """
    start = datetime.combine(start_date, datetime.min.time())
    end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
    
    # Provider bazlı toplam incident sayısı
    total_by_provider = dict(
        db.query(Incident.provider, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.provider.isnot(None),
                Incident.created_at >= start,
                Incident.created_at < end,
            )
        )
        .group_by(Incident.provider)
        .all()
    )
    
    # Provider bazlı mismatch sayısı (INVOICE_TOTAL_MISMATCH flag'i olan)
    mismatch_by_provider = dict(
        db.query(Incident.provider, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.provider.isnot(None),
                Incident.created_at >= start,
                Incident.created_at < end,
                Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
            )
        )
        .group_by(Incident.provider)
        .all()
    )
    
    # TopOffender listesi oluştur (min volume guard ile)
    offenders = []
    for provider, total in total_by_provider.items():
        if total < min_volume:
            continue  # Düşük hacimli provider'ları hariç tut
        mismatch = mismatch_by_provider.get(provider, 0)
        offenders.append(TopOffender(
            provider=provider,
            total_count=total,
            mismatch_count=mismatch,
        ))
    
    # Rate'e göre sırala (yüksekten düşüğe)
    offenders.sort(key=lambda x: x.mismatch_rate, reverse=True)
    
    return offenders[:limit]


def get_top_offenders_by_count(
    db: Session,
    tenant_id: str,
    start_date: date,
    end_date: date,
    limit: int = 10,
) -> List[TopOffender]:
    """
    Top offenders by mismatch COUNT (en büyük etki).
    
    Min volume guard yok - en çok mismatch üreten provider'lar.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        start_date: Başlangıç tarihi
        end_date: Bitiş tarihi
        limit: Maksimum sonuç sayısı
    
    Returns:
        TopOffender listesi (count'a göre sıralı, yüksekten düşüğe)
    """
    start = datetime.combine(start_date, datetime.min.time())
    end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
    
    # Provider bazlı toplam incident sayısı
    total_by_provider = dict(
        db.query(Incident.provider, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.provider.isnot(None),
                Incident.created_at >= start,
                Incident.created_at < end,
            )
        )
        .group_by(Incident.provider)
        .all()
    )
    
    # Provider bazlı mismatch sayısı
    mismatch_by_provider = dict(
        db.query(Incident.provider, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.provider.isnot(None),
                Incident.created_at >= start,
                Incident.created_at < end,
                Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
            )
        )
        .group_by(Incident.provider)
        .all()
    )
    
    # TopOffender listesi oluştur
    offenders = []
    for provider, total in total_by_provider.items():
        mismatch = mismatch_by_provider.get(provider, 0)
        if mismatch > 0:  # Sadece mismatch olan provider'lar
            offenders.append(TopOffender(
                provider=provider,
                total_count=total,
                mismatch_count=mismatch,
            ))
    
    # Count'a göre sırala (yüksekten düşüğe)
    offenders.sort(key=lambda x: x.mismatch_count, reverse=True)
    
    return offenders[:limit]


# Backward compatibility alias
def get_top_offenders(
    db: Session,
    tenant_id: str,
    start_date: date,
    end_date: date,
    limit: int = 10,
) -> List[TopOffender]:
    """Backward compatibility - returns top by rate."""
    return get_top_offenders_by_rate(db, tenant_id, start_date, end_date, limit)


def get_action_class_distribution(
    db: Session,
    tenant_id: str,
    start_date: date,
    end_date: date,
) -> ActionClassDistribution:
    """
    Action class dağılımı hesapla.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        start_date: Başlangıç tarihi
        end_date: Bitiş tarihi
    
    Returns:
        ActionClassDistribution
    """
    start = datetime.combine(start_date, datetime.min.time())
    end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
    
    # INVOICE_TOTAL_MISMATCH flag'li incident'ları al
    incidents = (
        db.query(Incident)
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
                Incident.created_at >= start,
                Incident.created_at < end,
            )
        )
        .all()
    )
    
    dist = ActionClassDistribution()
    
    for inc in incidents:
        details = inc.details_json or {}
        action_hint = details.get("action_hint", {})
        action_class = action_hint.get("action_class", "")
        
        if action_class == "VERIFY_OCR":
            dist.verify_ocr += 1
        elif action_class == "VERIFY_INVOICE_LOGIC":
            dist.verify_invoice_logic += 1
        elif action_class == "ACCEPT_ROUNDING_TOLERANCE":
            dist.accept_rounding += 1
    
    return dist


def get_period_stats(
    db: Session,
    tenant_id: str,
    start_date: date,
    end_date: date,
) -> PeriodStats:
    """
    Dönem istatistiklerini hesapla.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        start_date: Başlangıç tarihi
        end_date: Bitiş tarihi
    
    Returns:
        PeriodStats
    """
    start = datetime.combine(start_date, datetime.min.time())
    end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
    
    # Toplam incident sayısı
    total = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.created_at >= start,
            Incident.created_at < end,
        )
    ).count()
    
    # Mismatch sayısı
    mismatch = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
            Incident.created_at >= start,
            Incident.created_at < end,
        )
    ).count()
    
    # S1 sayısı
    s1 = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.severity == "S1",
            Incident.created_at >= start,
            Incident.created_at < end,
        )
    ).count()
    
    # S2 sayısı
    s2 = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.severity == "S2",
            Incident.created_at >= start,
            Incident.created_at < end,
        )
    ).count()
    
    # OCR suspect sayısı (details_json içinde suspect_reason kontrolü)
    # Bu biraz daha karmaşık, incident'ları çekip kontrol etmemiz gerekiyor
    ocr_suspect = 0
    mismatch_incidents = (
        db.query(Incident)
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
                Incident.created_at >= start,
                Incident.created_at < end,
            )
        )
        .all()
    )
    
    for inc in mismatch_incidents:
        details = inc.details_json or {}
        flag_details = details.get("flag_details", [])
        for fd in flag_details:
            if fd.get("suspect_reason") == "OCR_LOCALE_SUSPECT":
                ocr_suspect += 1
                break
    
    return PeriodStats(
        start_date=start_date,
        end_date=end_date,
        total_invoices=total,
        mismatch_count=mismatch,
        s1_count=s1,
        s2_count=s2,
        ocr_suspect_count=ocr_suspect,
    )


def generate_system_health_report(
    db: Session,
    tenant_id: str,
    reference_date: Optional[date] = None,
    period_days: int = 7,
) -> SystemHealthReport:
    """
    Sistem sağlık raporu oluştur.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        reference_date: Referans tarih (varsayılan: bugün)
        period_days: Dönem uzunluğu (varsayılan: 7 gün)
    
    Returns:
        SystemHealthReport
    """
    if reference_date is None:
        reference_date = date.today()
    
    # Current period: son 7 gün
    current_end = reference_date
    current_start = current_end - timedelta(days=period_days - 1)
    
    # Previous period: önceki 7 gün
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    
    # Period stats
    current_stats = get_period_stats(db, tenant_id, current_start, current_end)
    previous_stats = get_period_stats(db, tenant_id, previous_start, previous_end)
    
    # Drift alerts
    drift_alerts = []
    
    # S1 rate drift
    s1_drift = detect_drift(
        old_count=previous_stats.s1_count,
        new_count=current_stats.s1_count,
        old_total=previous_stats.s1_count + previous_stats.s2_count,
        new_total=current_stats.s1_count + current_stats.s2_count,
        alert_type=AlertType.S1_RATE_DRIFT,
    )
    drift_alerts.append(s1_drift)
    
    # OCR suspect drift
    ocr_drift = detect_drift(
        old_count=previous_stats.ocr_suspect_count,
        new_count=current_stats.ocr_suspect_count,
        old_total=previous_stats.mismatch_count,
        new_total=current_stats.mismatch_count,
        alert_type=AlertType.OCR_SUSPECT_DRIFT,
    )
    drift_alerts.append(ocr_drift)
    
    # Mismatch rate drift
    mismatch_drift = detect_drift(
        old_count=previous_stats.mismatch_count,
        new_count=current_stats.mismatch_count,
        old_total=previous_stats.total_invoices,
        new_total=current_stats.total_invoices,
        alert_type=AlertType.MISMATCH_RATE_DRIFT,
    )
    drift_alerts.append(mismatch_drift)
    
    # Top offenders (iki liste)
    top_offenders_by_rate = get_top_offenders_by_rate(db, tenant_id, current_start, current_end)
    top_offenders_by_count = get_top_offenders_by_count(db, tenant_id, current_start, current_end)
    
    # Histogram - incident'ları çek
    start = datetime.combine(current_start, datetime.min.time())
    end = datetime.combine(current_end, datetime.min.time()) + timedelta(days=1)
    
    incidents = (
        db.query(Incident)
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.primary_flag == "INVOICE_TOTAL_MISMATCH",
                Incident.created_at >= start,
                Incident.created_at < end,
            )
        )
        .all()
    )
    
    incident_dicts = [
        {"details": inc.details_json}
        for inc in incidents
    ]
    histogram = calculate_mismatch_histogram(incident_dicts)
    
    # Action class distribution
    action_dist = get_action_class_distribution(db, tenant_id, current_start, current_end)
    
    return SystemHealthReport(
        generated_at=datetime.now(timezone.utc),
        current_period=current_stats,
        previous_period=previous_stats,
        drift_alerts=drift_alerts,
        top_offenders_by_rate=top_offenders_by_rate,
        top_offenders_by_count=top_offenders_by_count,
        histogram=histogram,
        action_class_distribution=action_dist,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.2: EXISTING METRICS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RetryFunnel:
    """Retry funnel metrikleri."""
    attempts_total: int = 0
    attempts_success: int = 0  # retry_success=True
    resolved_after_retry: int = 0  # PENDING_RETRY → RESOLVED
    still_pending: int = 0  # retry_success=True ama RESOLVED değil
    exhausted: int = 0
    
    @property
    def success_rate(self) -> float:
        """Retry success rate."""
        if self.attempts_total == 0:
            return 0.0
        return self.attempts_success / self.attempts_total
    
    @property
    def false_success_rate(self) -> float:
        """False success rate: retry_success=True ama RESOLVED değil."""
        if self.attempts_success == 0:
            return 0.0
        return self.still_pending / self.attempts_success


@dataclass
class IncidentMetrics:
    """Incident metrikleri."""
    # Genel
    total_incidents: int = 0
    new_today: int = 0
    resolved_today: int = 0
    
    # Status dağılımı
    by_status: dict = field(default_factory=dict)
    
    # Retry funnel
    retry_funnel: RetryFunnel = field(default_factory=RetryFunnel)
    
    # Recompute
    recompute_limit_exceeded_count: int = 0
    stuck_pending_recompute_count: int = 0
    reclassified_count: int = 0
    
    # Top lists
    top_primary_flags: list = field(default_factory=list)
    top_action_codes: list = field(default_factory=list)
    top_providers: list = field(default_factory=list)
    
    # MTTR
    mttr_minutes: Optional[float] = None
    
    # Alerts
    alerts: list = field(default_factory=list)


def get_daily_counts(
    db: Session,
    tenant_id: str,
    target_date: date,
) -> dict:
    """
    Günlük incident sayıları.
    
    Returns:
        {"total": int, "new": int, "resolved": int}
    """
    start = datetime.combine(target_date, datetime.min.time())
    end = start + timedelta(days=1)
    
    total = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.created_at < end,
        )
    ).count()
    
    new = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.created_at >= start,
            Incident.created_at < end,
        )
    ).count()
    
    resolved = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.resolved_at >= start,
            Incident.resolved_at < end,
        )
    ).count()
    
    return {
        "total": total,
        "new": new,
        "resolved": resolved,
    }


def get_status_distribution(
    db: Session,
    tenant_id: str,
) -> dict:
    """
    Status dağılımı.
    
    Returns:
        {"OPEN": 5, "RESOLVED": 10, ...}
    """
    results = (
        db.query(Incident.status, func.count(Incident.id))
        .filter(Incident.tenant_id == tenant_id)
        .group_by(Incident.status)
        .all()
    )
    
    return {status: count for status, count in results}


def get_retry_funnel(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> RetryFunnel:
    """
    Retry funnel metrikleri.
    
    Returns:
        RetryFunnel dataclass
    """
    query = db.query(Incident).filter(Incident.tenant_id == tenant_id)
    
    if start_date:
        start = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Incident.created_at >= start)
    
    if end_date:
        end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Incident.created_at < end)
    
    # Retry attempt olan incident'lar
    retry_incidents = query.filter(Incident.retry_attempt_count > 0).all()
    
    funnel = RetryFunnel()
    funnel.attempts_total = len(retry_incidents)
    
    for inc in retry_incidents:
        if inc.retry_success:
            funnel.attempts_success += 1
            if inc.status == "RESOLVED":
                funnel.resolved_after_retry += 1
            else:
                funnel.still_pending += 1
        
        if inc.retry_exhausted_at:
            funnel.exhausted += 1
    
    return funnel


def get_top_primary_flags(
    db: Session,
    tenant_id: str,
    limit: int = 10,
) -> list:
    """
    En çok görülen primary flag'ler.
    
    Returns:
        [("CALC_BUG", 15), ("MARKET_PRICE_MISSING", 12), ...]
    """
    results = (
        db.query(Incident.primary_flag, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.primary_flag.isnot(None),
            )
        )
        .group_by(Incident.primary_flag)
        .order_by(func.count(Incident.id).desc())
        .limit(limit)
        .all()
    )
    
    return [(flag, count) for flag, count in results]


def get_top_action_codes(
    db: Session,
    tenant_id: str,
    limit: int = 10,
) -> list:
    """
    En çok görülen action code'lar.
    
    Returns:
        [("ENGINE_REGRESSION", 10), ("PTF_YEKDEM_CHECK", 8), ...]
    """
    results = (
        db.query(Incident.action_code, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.action_code.isnot(None),
            )
        )
        .group_by(Incident.action_code)
        .order_by(func.count(Incident.id).desc())
        .limit(limit)
        .all()
    )
    
    return [(code, count) for code, count in results]


def get_top_providers(
    db: Session,
    tenant_id: str,
    limit: int = 10,
) -> list:
    """
    En çok incident üreten provider'lar.
    
    Returns:
        [("ck_bogazici", 20), ("enerjisa", 15), ...]
    """
    results = (
        db.query(Incident.provider, func.count(Incident.id))
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.provider.isnot(None),
            )
        )
        .group_by(Incident.provider)
        .order_by(func.count(Incident.id).desc())
        .limit(limit)
        .all()
    )
    
    return [(provider, count) for provider, count in results]


def get_stuck_pending_recompute_count(
    db: Session,
    tenant_id: str,
    threshold_minutes: int = STUCK_THRESHOLD_MINUTES,
    now: Optional[datetime] = None,
) -> int:
    """
    Stuck PENDING_RECOMPUTE incident sayısı.
    
    Kural: PENDING_RECOMPUTE + updated_at <= now - threshold
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    threshold = now - timedelta(minutes=threshold_minutes)
    
    return db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.status == "PENDING_RECOMPUTE",
            Incident.updated_at < threshold,
        )
    ).count()


def get_recompute_limit_exceeded_count(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> int:
    """
    Recompute limit aşan incident sayısı.
    """
    query = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.resolution_reason == ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED,
        )
    )
    
    if start_date:
        start = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Incident.updated_at >= start)
    
    if end_date:
        end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Incident.updated_at < end)
    
    return query.count()


def get_reclassified_count(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> int:
    """
    Reclassify edilen incident sayısı.
    """
    query = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.reclassified_at.isnot(None),
        )
    )
    
    if start_date:
        start = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Incident.reclassified_at >= start)
    
    if end_date:
        end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Incident.reclassified_at < end)
    
    return query.count()


def get_mttr(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Optional[float]:
    """
    Mean Time To Resolve (dakika).
    
    Tanım: resolved_at - first_seen_at ortalaması
    Sadece RESOLVED_SET'teki resolution_reason'lar dahil.
    
    Returns:
        Ortalama dakika veya None (veri yoksa)
    """
    query = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.resolved_at.isnot(None),
            Incident.first_seen_at.isnot(None),
            Incident.resolution_reason.in_(list(ResolutionReason.RESOLVED_SET)),
        )
    )
    
    if start_date:
        start = datetime.combine(start_date, datetime.min.time())
        query = query.filter(Incident.resolved_at >= start)
    
    if end_date:
        end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1)
        query = query.filter(Incident.resolved_at < end)
    
    incidents = query.all()
    
    if not incidents:
        return None
    
    total_minutes = 0
    for inc in incidents:
        delta = inc.resolved_at - inc.first_seen_at
        total_minutes += delta.total_seconds() / 60
    
    return total_minutes / len(incidents)


def get_false_success_rate(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> float:
    """
    False success rate: retry_success=True ama RESOLVED değil.
    
    Returns:
        Oran (0.0 - 1.0)
    """
    funnel = get_retry_funnel(db, tenant_id, start_date, end_date)
    return funnel.false_success_rate


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.7: FEEDBACK LOOP
# ═══════════════════════════════════════════════════════════════════════════════


class FeedbackAction(str, Enum):
    """
    Feedback action types - what the operator actually did.
    
    These map to (but are distinct from) ActionClass:
    - VERIFIED_OCR → operator confirmed OCR/locale issue
    - VERIFIED_LOGIC → operator confirmed invoice logic issue
    - ACCEPTED_ROUNDING → operator accepted as rounding difference
    - ESCALATED → operator escalated to higher level
    - NO_ACTION_REQUIRED → operator reviewed, no action needed (expected behavior)
    """
    VERIFIED_OCR = "VERIFIED_OCR"
    VERIFIED_LOGIC = "VERIFIED_LOGIC"
    ACCEPTED_ROUNDING = "ACCEPTED_ROUNDING"
    ESCALATED = "ESCALATED"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"


# Validation constants - NOW FROM CONFIG
FEEDBACK_ROOT_CAUSE_MAX_LENGTH = THRESHOLDS.Feedback.ROOT_CAUSE_MAX_LENGTH


@dataclass
class IncidentFeedback:
    """
    Feedback data structure for an incident.
    
    Stored in incidents.feedback_json column.
    """
    action_taken: FeedbackAction
    was_hint_correct: bool
    actual_root_cause: Optional[str]
    resolution_time_seconds: int
    feedback_at: datetime
    feedback_by: str
    
    def to_dict(self) -> dict:
        return {
            "action_taken": self.action_taken.value,
            "was_hint_correct": self.was_hint_correct,
            "actual_root_cause": self.actual_root_cause,
            "resolution_time_seconds": self.resolution_time_seconds,
            "feedback_at": self.feedback_at.isoformat(),
            "feedback_by": self.feedback_by,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "IncidentFeedback":
        """Parse feedback from JSON dict."""
        return cls(
            action_taken=FeedbackAction(data["action_taken"]),
            was_hint_correct=data["was_hint_correct"],
            actual_root_cause=data.get("actual_root_cause"),
            resolution_time_seconds=data["resolution_time_seconds"],
            feedback_at=datetime.fromisoformat(data["feedback_at"]),
            feedback_by=data["feedback_by"],
        )


@dataclass
class FeedbackStats:
    """
    Calibration metrics from feedback data.
    
    All rates are null-safe: return 0.0 when denominator is 0.
    """
    # Overall accuracy
    hint_accuracy_rate: float  # was_hint_correct=true / total_feedback
    total_feedback_count: int
    
    # Per action class accuracy
    action_class_accuracy: Dict[str, float]  # {action_class: accuracy_rate}
    
    # Resolution time by class
    avg_resolution_time_by_class: Dict[str, float]  # {action_class: avg_seconds}
    
    # Coverage
    feedback_coverage: float  # resolved_with_feedback / resolved_total
    resolved_with_feedback: int
    resolved_total: int
    
    def to_dict(self) -> dict:
        return {
            "hint_accuracy_rate": round(self.hint_accuracy_rate, 4),
            "total_feedback_count": self.total_feedback_count,
            "action_class_accuracy": {
                k: round(v, 4) for k, v in self.action_class_accuracy.items()
            },
            "avg_resolution_time_by_class": {
                k: round(v, 1) for k, v in self.avg_resolution_time_by_class.items()
            },
            "feedback_coverage": round(self.feedback_coverage, 4),
            "resolved_with_feedback": self.resolved_with_feedback,
            "resolved_total": self.resolved_total,
        }


class FeedbackValidationError(Exception):
    """Raised when feedback validation fails."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_feedback(
    payload: dict,
    incident_status: str,
) -> None:
    """
    Validate feedback payload.
    
    Raises:
        FeedbackValidationError: If validation fails
    
    Validation rules:
    - incident must be RESOLVED (state guard)
    - action_taken must be valid enum
    - was_hint_correct must not be null
    - resolution_time_seconds must be >= 0
    - actual_root_cause must be <= 200 chars
    """
    # State guard: only RESOLVED incidents can have feedback
    if incident_status != "RESOLVED":
        raise FeedbackValidationError(
            code="incident_not_resolved",
            message=f"Feedback can only be submitted for RESOLVED incidents. Current status: {incident_status}"
        )
    
    # action_taken must be valid enum
    action_taken = payload.get("action_taken")
    if action_taken is None:
        raise FeedbackValidationError(
            code="invalid_feedback_data",
            message="action_taken is required"
        )
    try:
        FeedbackAction(action_taken)
    except ValueError:
        raise FeedbackValidationError(
            code="invalid_feedback_action",
            message=f"Invalid action_taken: {action_taken}. Must be one of: {[a.value for a in FeedbackAction]}"
        )
    
    # was_hint_correct must not be null
    was_hint_correct = payload.get("was_hint_correct")
    if was_hint_correct is None:
        raise FeedbackValidationError(
            code="invalid_feedback_data",
            message="was_hint_correct is required and cannot be null"
        )
    if not isinstance(was_hint_correct, bool):
        raise FeedbackValidationError(
            code="invalid_feedback_data",
            message="was_hint_correct must be a boolean"
        )
    
    # resolution_time_seconds must be >= 0
    resolution_time = payload.get("resolution_time_seconds", 0)
    if not isinstance(resolution_time, (int, float)):
        raise FeedbackValidationError(
            code="invalid_feedback_data",
            message="resolution_time_seconds must be a number"
        )
    if resolution_time < 0:
        raise FeedbackValidationError(
            code="invalid_feedback_data",
            message="resolution_time_seconds must be >= 0"
        )
    
    # actual_root_cause must be <= 200 chars
    root_cause = payload.get("actual_root_cause")
    if root_cause is not None:
        if not isinstance(root_cause, str):
            raise FeedbackValidationError(
                code="invalid_feedback_data",
                message="actual_root_cause must be a string"
            )
        if len(root_cause) > FEEDBACK_ROOT_CAUSE_MAX_LENGTH:
            raise FeedbackValidationError(
                code="invalid_feedback_data",
                message=f"actual_root_cause must be <= {FEEDBACK_ROOT_CAUSE_MAX_LENGTH} characters"
            )


def submit_feedback(
    db: Session,
    incident_id: int,
    payload: dict,
    user_id: str,
) -> Incident:
    """
    Submit feedback for an incident.
    
    UPSERT semantics: each submission overwrites previous feedback.
    Both feedback_at and updated_at are always updated.
    
    Args:
        db: Database session
        incident_id: Incident ID
        payload: Feedback payload (action_taken, was_hint_correct, etc.)
        user_id: User ID from auth context (required)
    
    Returns:
        Updated Incident
    
    Raises:
        FeedbackValidationError: If validation fails
        ValueError: If incident not found
    """
    # Get incident
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise ValueError(f"Incident not found: {incident_id}")
    
    # Validate
    validate_feedback(payload, incident.status)
    
    # Build feedback object
    now = datetime.now(timezone.utc)
    feedback = IncidentFeedback(
        action_taken=FeedbackAction(payload["action_taken"]),
        was_hint_correct=payload["was_hint_correct"],
        actual_root_cause=payload.get("actual_root_cause"),
        resolution_time_seconds=int(payload.get("resolution_time_seconds", 0)),
        feedback_at=now,
        feedback_by=user_id,
    )
    
    # UPSERT: overwrite previous feedback
    incident.feedback_json = feedback.to_dict()
    incident.updated_at = now.replace(tzinfo=None)  # SQLite compatibility
    
    db.commit()
    db.refresh(incident)
    
    return incident


def get_feedback_stats(
    db: Session,
    tenant_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> FeedbackStats:
    """
    Calculate feedback calibration metrics.
    
    All rates are null-safe: return 0.0 when denominator is 0.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        start_date: Start date filter (optional)
        end_date: End date filter (optional)
    
    Returns:
        FeedbackStats with calibration metrics
    """
    # Build date filters
    start = datetime.combine(start_date, datetime.min.time()) if start_date else None
    end = datetime.combine(end_date, datetime.min.time()) + timedelta(days=1) if end_date else None
    
    # Get all resolved incidents in period
    resolved_query = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.status == "RESOLVED",
        )
    )
    if start:
        resolved_query = resolved_query.filter(Incident.resolved_at >= start)
    if end:
        resolved_query = resolved_query.filter(Incident.resolved_at < end)
    
    resolved_incidents = resolved_query.all()
    resolved_total = len(resolved_incidents)
    
    # Filter to those with feedback
    incidents_with_feedback = [
        inc for inc in resolved_incidents
        if inc.feedback_json is not None
    ]
    resolved_with_feedback = len(incidents_with_feedback)
    
    # Calculate hint accuracy
    correct_count = sum(
        1 for inc in incidents_with_feedback
        if inc.feedback_json.get("was_hint_correct", False)
    )
    total_feedback = len(incidents_with_feedback)
    hint_accuracy_rate = correct_count / total_feedback if total_feedback > 0 else 0.0
    
    # Calculate per-action-class accuracy
    action_class_stats: Dict[str, Dict[str, int]] = {}  # {class: {correct: n, total: n}}
    resolution_times: Dict[str, List[int]] = {}  # {class: [times]}
    
    for inc in incidents_with_feedback:
        fb = inc.feedback_json
        action = fb.get("action_taken", "UNKNOWN")
        was_correct = fb.get("was_hint_correct", False)
        res_time = fb.get("resolution_time_seconds", 0)
        
        if action not in action_class_stats:
            action_class_stats[action] = {"correct": 0, "total": 0}
            resolution_times[action] = []
        
        action_class_stats[action]["total"] += 1
        if was_correct:
            action_class_stats[action]["correct"] += 1
        resolution_times[action].append(res_time)
    
    # Compute accuracy rates
    action_class_accuracy = {
        action: stats["correct"] / stats["total"] if stats["total"] > 0 else 0.0
        for action, stats in action_class_stats.items()
    }
    
    # Compute avg resolution times
    avg_resolution_time_by_class = {
        action: sum(times) / len(times) if times else 0.0
        for action, times in resolution_times.items()
    }
    
    # Calculate coverage
    feedback_coverage = resolved_with_feedback / resolved_total if resolved_total > 0 else 0.0
    
    return FeedbackStats(
        hint_accuracy_rate=hint_accuracy_rate,
        total_feedback_count=total_feedback,
        action_class_accuracy=action_class_accuracy,
        avg_resolution_time_by_class=avg_resolution_time_by_class,
        feedback_coverage=feedback_coverage,
        resolved_with_feedback=resolved_with_feedback,
        resolved_total=resolved_total,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.8: RUN SUMMARY GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class RunSummary:
    """
    Pipeline run summary for observability.
    
    Sprint 8.8: "5 soru / 30 saniye" kriteri için tek bakışta özet.
    """
    generated_at: str
    period_start: str
    period_end: str
    
    # Counts
    total_invoices: int
    incident_count: int
    s1_count: int
    s2_count: int
    ocr_suspect_count: int
    resolved_count: int
    feedback_count: int
    
    # Rates
    mismatch_rate: float
    s1_rate: float
    ocr_suspect_rate: float
    feedback_coverage: float
    hint_accuracy_rate: float
    
    # Latency (pipeline_total_ms)
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    
    # Errors
    error_4xx_count: int = 0
    error_5xx_count: int = 0
    error_by_code: Dict[str, int] = field(default_factory=dict)
    
    # Queue
    queue_depth: int = 0
    queue_stuck: bool = False
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "generated_at": self.generated_at,
            "period": {
                "start": self.period_start,
                "end": self.period_end,
            },
            "counts": {
                "total_invoices": self.total_invoices,
                "incident_count": self.incident_count,
                "s1_count": self.s1_count,
                "s2_count": self.s2_count,
                "ocr_suspect_count": self.ocr_suspect_count,
                "resolved_count": self.resolved_count,
                "feedback_count": self.feedback_count,
            },
            "rates": {
                "mismatch_rate": round(self.mismatch_rate, 4),
                "s1_rate": round(self.s1_rate, 4),
                "ocr_suspect_rate": round(self.ocr_suspect_rate, 4),
                "feedback_coverage": round(self.feedback_coverage, 4),
                "hint_accuracy_rate": round(self.hint_accuracy_rate, 4),
            },
            "latency": {
                "pipeline_total_ms": {
                    "p50": self.latency_p50_ms,
                    "p95": self.latency_p95_ms,
                    "p99": self.latency_p99_ms,
                }
            },
            "errors": {
                "by_code": self.error_by_code,
                "total_4xx": self.error_4xx_count,
                "total_5xx": self.error_5xx_count,
            },
            "queue": {
                "current_depth": self.queue_depth,
                "stuck_detected": self.queue_stuck,
            },
        }


def generate_run_summary(
    db: Session,
    tenant_id: str = "default",
    period_hours: int = 24,
    latency_samples: Optional[List[float]] = None,
) -> RunSummary:
    """
    Generate run summary for observability dashboard.
    
    Sprint 8.8: "5 soru / 30 saniye" kriteri için tek bakışta özet.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        period_hours: Period to analyze (default 24h)
        latency_samples: Optional list of pipeline latency samples (ms)
    
    Returns:
        RunSummary with all metrics
    """
    from .database import Job
    
    now = datetime.now(timezone.utc)
    period_start = now - timedelta(hours=period_hours)
    
    # Query incidents in period
    incidents = db.query(Incident).filter(
        Incident.tenant_id == tenant_id,
        Incident.created_at >= period_start,
    ).all()
    
    # Count by severity
    incident_count = len(incidents)
    s1_count = sum(1 for i in incidents if i.severity == "S1")
    s2_count = sum(1 for i in incidents if i.severity == "S2")
    
    # Count OCR suspects (from details_json)
    ocr_suspect_count = 0
    for inc in incidents:
        if inc.details_json:
            action_hint = inc.details_json.get("action_hint", {})
            if action_hint and action_hint.get("action_class") == "VERIFY_OCR":
                ocr_suspect_count += 1
    
    # Count resolved and feedback
    resolved_count = sum(1 for i in incidents if i.status == "RESOLVED")
    feedback_count = sum(1 for i in incidents if i.feedback_json)
    
    # Estimate total invoices (from Job table if available)
    try:
        total_invoices = db.query(Job).filter(
            Job.created_at >= period_start,
            Job.job_type == "full_process",
        ).count()
    except:
        # Fallback: estimate from incidents
        total_invoices = incident_count * 5  # Rough estimate
    
    # Calculate rates
    mismatch_rate = incident_count / max(total_invoices, 1)
    s1_rate = s1_count / max(incident_count, 1)
    ocr_suspect_rate = ocr_suspect_count / max(incident_count, 1)
    feedback_coverage = feedback_count / max(resolved_count, 1)
    
    # Calculate hint accuracy
    correct_hints = sum(
        1 for i in incidents
        if i.feedback_json and i.feedback_json.get("was_hint_correct", False)
    )
    hint_accuracy_rate = correct_hints / max(feedback_count, 1)
    
    # Calculate latency percentiles
    latency_p50 = None
    latency_p95 = None
    latency_p99 = None
    
    if latency_samples and len(latency_samples) > 0:
        sorted_samples = sorted(latency_samples)
        n = len(sorted_samples)
        latency_p50 = sorted_samples[int(n * 0.50)]
        latency_p95 = sorted_samples[int(n * 0.95)] if n >= 20 else sorted_samples[-1]
        latency_p99 = sorted_samples[int(n * 0.99)] if n >= 100 else sorted_samples[-1]
    
    # Queue status
    try:
        queue_depth = db.query(Job).filter(Job.status == "pending").count()
        stuck_threshold = now - timedelta(minutes=10)
        stuck_count = db.query(Job).filter(
            Job.status == "processing",
            Job.updated_at < stuck_threshold,
        ).count()
        queue_stuck = stuck_count > 0
    except:
        queue_depth = 0
        queue_stuck = False
    
    return RunSummary(
        generated_at=now.isoformat(),
        period_start=period_start.isoformat(),
        period_end=now.isoformat(),
        total_invoices=total_invoices,
        incident_count=incident_count,
        s1_count=s1_count,
        s2_count=s2_count,
        ocr_suspect_count=ocr_suspect_count,
        resolved_count=resolved_count,
        feedback_count=feedback_count,
        mismatch_rate=mismatch_rate,
        s1_rate=s1_rate,
        ocr_suspect_rate=ocr_suspect_rate,
        feedback_coverage=feedback_coverage,
        hint_accuracy_rate=hint_accuracy_rate,
        latency_p50_ms=latency_p50,
        latency_p95_ms=latency_p95,
        latency_p99_ms=latency_p99,
        queue_depth=queue_depth,
        queue_stuck=queue_stuck,
    )
