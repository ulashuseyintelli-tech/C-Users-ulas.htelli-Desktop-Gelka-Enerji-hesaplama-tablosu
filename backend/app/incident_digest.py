"""
Incident Digest - Sprint 8.2 + 8.8 (Config)

Günlük özet raporu üretir.
Alert kuralları ile operasyonel uyarılar.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from sqlalchemy.orm import Session

from .incident_metrics import (
    IncidentMetrics,
    RetryFunnel,
    get_daily_counts,
    get_status_distribution,
    get_retry_funnel,
    get_top_primary_flags,
    get_top_action_codes,
    get_top_providers,
    get_stuck_pending_recompute_count,
    get_recompute_limit_exceeded_count,
    get_reclassified_count,
    get_mttr,
)
from .config import THRESHOLDS

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    """Alert konfigürasyonu - NOW FROM THRESHOLDS."""
    enabled: bool = False  # Başlangıçta kapalı (baseline toplama)
    bug_report_rate_threshold: float = THRESHOLDS.Alert.BUG_REPORT_RATE
    exhausted_rate_threshold: float = THRESHOLDS.Alert.EXHAUSTED_RATE
    stuck_count_threshold: int = THRESHOLDS.Alert.STUCK_COUNT
    recompute_limit_threshold: int = THRESHOLDS.Alert.RECOMPUTE_LIMIT


# Default config - başlangıçta kapalı
DEFAULT_ALERT_CONFIG = AlertConfig(enabled=False)


@dataclass
class DailyDigest:
    """Günlük özet raporu."""
    date: date
    tenant_id: str
    metrics: IncidentMetrics
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> dict:
        """Dict'e çevir (JSON serialization için)."""
        return {
            "date": self.date.isoformat(),
            "tenant_id": self.tenant_id,
            "generated_at": self.generated_at.isoformat(),
            "summary": {
                "total_incidents": self.metrics.total_incidents,
                "new_today": self.metrics.new_today,
                "resolved_today": self.metrics.resolved_today,
            },
            "status_distribution": self.metrics.by_status,
            "retry_funnel": {
                "attempts_total": self.metrics.retry_funnel.attempts_total,
                "attempts_success": self.metrics.retry_funnel.attempts_success,
                "resolved_after_retry": self.metrics.retry_funnel.resolved_after_retry,
                "still_pending": self.metrics.retry_funnel.still_pending,
                "exhausted": self.metrics.retry_funnel.exhausted,
                "success_rate": round(self.metrics.retry_funnel.success_rate, 4),
                "false_success_rate": round(self.metrics.retry_funnel.false_success_rate, 4),
            },
            "recompute": {
                "limit_exceeded_count": self.metrics.recompute_limit_exceeded_count,
                "stuck_pending_count": self.metrics.stuck_pending_recompute_count,
                "reclassified_count": self.metrics.reclassified_count,
            },
            "top_primary_flags": [
                {"flag": flag, "count": count}
                for flag, count in self.metrics.top_primary_flags
            ],
            "top_action_codes": [
                {"code": code, "count": count}
                for code, count in self.metrics.top_action_codes
            ],
            "top_providers": [
                {"provider": provider, "count": count}
                for provider, count in self.metrics.top_providers
            ],
            "mttr_minutes": self.metrics.mttr_minutes,
            "alerts": self.metrics.alerts,
        }
    
    def to_markdown(self) -> str:
        """Markdown formatında rapor."""
        lines = [
            f"# Daily Incident Digest - {self.date.isoformat()}",
            f"",
            f"**Tenant:** {self.tenant_id}",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Incidents | {self.metrics.total_incidents} |",
            f"| New Today | {self.metrics.new_today} |",
            f"| Resolved Today | {self.metrics.resolved_today} |",
            f"",
            f"## Status Distribution",
            f"",
        ]
        
        for status, count in self.metrics.by_status.items():
            lines.append(f"- **{status}:** {count}")
        
        lines.extend([
            f"",
            f"## Retry Funnel",
            f"",
            f"| Stage | Count |",
            f"|-------|-------|",
            f"| Attempts Total | {self.metrics.retry_funnel.attempts_total} |",
            f"| Success | {self.metrics.retry_funnel.attempts_success} |",
            f"| Resolved After Retry | {self.metrics.retry_funnel.resolved_after_retry} |",
            f"| Still Pending | {self.metrics.retry_funnel.still_pending} |",
            f"| Exhausted | {self.metrics.retry_funnel.exhausted} |",
            f"",
            f"**Success Rate:** {self.metrics.retry_funnel.success_rate:.1%}",
            f"**False Success Rate:** {self.metrics.retry_funnel.false_success_rate:.1%}",
            f"",
            f"## Recompute Metrics",
            f"",
            f"- **Limit Exceeded:** {self.metrics.recompute_limit_exceeded_count}",
            f"- **Stuck Pending:** {self.metrics.stuck_pending_recompute_count}",
            f"- **Reclassified:** {self.metrics.reclassified_count}",
            f"",
        ])
        
        if self.metrics.mttr_minutes is not None:
            lines.append(f"## MTTR")
            lines.append(f"")
            lines.append(f"**Mean Time To Resolve:** {self.metrics.mttr_minutes:.1f} minutes")
            lines.append(f"")
        
        if self.metrics.top_primary_flags:
            lines.extend([
                f"## Top Primary Flags",
                f"",
            ])
            for i, (flag, count) in enumerate(self.metrics.top_primary_flags[:5], 1):
                lines.append(f"{i}. **{flag}:** {count}")
            lines.append(f"")
        
        if self.metrics.top_action_codes:
            lines.extend([
                f"## Top Action Codes",
                f"",
            ])
            for i, (code, count) in enumerate(self.metrics.top_action_codes[:5], 1):
                lines.append(f"{i}. **{code}:** {count}")
            lines.append(f"")
        
        if self.metrics.alerts:
            lines.extend([
                f"## ⚠️ Alerts",
                f"",
            ])
            for alert in self.metrics.alerts:
                lines.append(f"- {alert}")
            lines.append(f"")
        
        return "\n".join(lines)


def generate_alerts(
    metrics: IncidentMetrics,
    config: AlertConfig = DEFAULT_ALERT_CONFIG,
) -> list:
    """
    Alert kurallarını uygula.
    
    NOT: Başlangıçta config.enabled=False, sadece INFO log.
    Baseline toplandıktan sonra açılır.
    """
    alerts = []
    
    # Stuck pending recompute
    if metrics.stuck_pending_recompute_count > 0:
        msg = f"{metrics.stuck_pending_recompute_count} incidents stuck in PENDING_RECOMPUTE"
        if config.enabled and metrics.stuck_pending_recompute_count >= config.stuck_count_threshold:
            alerts.append(f"🔴 CRITICAL: {msg}")
        else:
            logger.info(f"[ALERT-INFO] {msg}")
    
    # Recompute limit exceeded
    if metrics.recompute_limit_exceeded_count > 0:
        msg = f"{metrics.recompute_limit_exceeded_count} incidents hit recompute limit"
        if config.enabled and metrics.recompute_limit_exceeded_count >= config.recompute_limit_threshold:
            alerts.append(f"🟠 WARNING: {msg}")
        else:
            logger.info(f"[ALERT-INFO] {msg}")
    
    # High exhausted rate
    if metrics.retry_funnel.attempts_total > 0:
        exhausted_rate = metrics.retry_funnel.exhausted / metrics.retry_funnel.attempts_total
        if exhausted_rate > config.exhausted_rate_threshold:
            msg = f"Retry exhausted rate {exhausted_rate:.1%} > {config.exhausted_rate_threshold:.0%}"
            if config.enabled:
                alerts.append(f"🟠 WARNING: {msg}")
            else:
                logger.info(f"[ALERT-INFO] {msg}")
    
    # High false success rate
    if metrics.retry_funnel.false_success_rate > 0.3:  # %30
        msg = f"False success rate {metrics.retry_funnel.false_success_rate:.1%} is high"
        logger.info(f"[ALERT-INFO] {msg}")
        if config.enabled:
            alerts.append(f"🟡 INFO: {msg}")
    
    return alerts


def generate_daily_digest(
    db: Session,
    tenant_id: str,
    target_date: date,
    alert_config: AlertConfig = DEFAULT_ALERT_CONFIG,
    now: Optional[datetime] = None,
) -> DailyDigest:
    """
    Günlük özet raporu üretir.
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        target_date: Rapor tarihi
        alert_config: Alert konfigürasyonu
        now: Şimdi (test için override)
    
    Returns:
        DailyDigest
    """
    now = now or datetime.now(timezone.utc)
    
    metrics = IncidentMetrics()
    
    # Daily counts
    daily = get_daily_counts(db, tenant_id, target_date)
    metrics.total_incidents = daily["total"]
    metrics.new_today = daily["new"]
    metrics.resolved_today = daily["resolved"]
    
    # Status distribution
    metrics.by_status = get_status_distribution(db, tenant_id)
    
    # Retry funnel
    metrics.retry_funnel = get_retry_funnel(db, tenant_id, target_date, target_date)
    
    # Recompute metrics
    metrics.recompute_limit_exceeded_count = get_recompute_limit_exceeded_count(
        db, tenant_id, target_date, target_date
    )
    metrics.stuck_pending_recompute_count = get_stuck_pending_recompute_count(
        db, tenant_id, now=now.replace(tzinfo=None) if now.tzinfo else now
    )
    metrics.reclassified_count = get_reclassified_count(
        db, tenant_id, target_date, target_date
    )
    
    # Top lists
    metrics.top_primary_flags = get_top_primary_flags(db, tenant_id, limit=10)
    metrics.top_action_codes = get_top_action_codes(db, tenant_id, limit=10)
    metrics.top_providers = get_top_providers(db, tenant_id, limit=10)
    
    # MTTR
    metrics.mttr_minutes = get_mttr(db, tenant_id, target_date, target_date)
    
    # Alerts
    metrics.alerts = generate_alerts(metrics, alert_config)
    
    return DailyDigest(
        date=target_date,
        tenant_id=tenant_id,
        metrics=metrics,
        generated_at=now,
    )
