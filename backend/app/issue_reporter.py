"""
Issue Reporter - Sprint 7.1

BUG_REPORT incident'larını external issue tracker'a raporlar.
Idempotent: (tenant_id, dedupe_key, dedupe_bucket) bazında tek issue.

Desteklenen tracker'lar:
- GitHub Issues (MVP)
- Jira (Sprint 7.2)
- Webhook (generic)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from sqlalchemy.orm import Session
from sqlalchemy import and_

from .database import Incident

logger = logging.getLogger(__name__)


@dataclass
class IssueCreationResult:
    """Issue oluşturma sonucu."""
    success: bool
    issue_id: Optional[str] = None
    issue_url: Optional[str] = None
    error_message: Optional[str] = None


class IssueTrackerAdapter(Protocol):
    """Issue tracker adapter protocol."""
    
    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        metadata: dict,
    ) -> IssueCreationResult:
        """Issue oluştur."""
        ...


class MockIssueTracker:
    """Test için mock issue tracker."""
    
    def __init__(self, *, should_fail: bool = False):
        self.should_fail = should_fail
        self.created_issues: list[dict] = []
        self._issue_counter = 0
    
    def create_issue(
        self,
        title: str,
        body: str,
        labels: list[str],
        metadata: dict,
    ) -> IssueCreationResult:
        if self.should_fail:
            return IssueCreationResult(
                success=False,
                error_message="Mock tracker configured to fail",
            )
        
        self._issue_counter += 1
        issue_id = f"MOCK-{self._issue_counter}"
        issue_url = f"https://mock-tracker.example/issues/{issue_id}"
        
        self.created_issues.append({
            "id": issue_id,
            "title": title,
            "body": body,
            "labels": labels,
            "metadata": metadata,
        })
        
        return IssueCreationResult(
            success=True,
            issue_id=issue_id,
            issue_url=issue_url,
        )


class IssueReporter:
    """
    BUG_REPORT incident'larını issue tracker'a raporlar.
    
    Idempotency:
    - (tenant_id, dedupe_key, dedupe_bucket) bazında tek issue
    - external_issue_id varsa skip
    - Aynı incident tekrar gelirse mevcut issue'ya link verir
    """
    
    def __init__(
        self,
        tracker: IssueTrackerAdapter,
        *,
        dry_run: bool = False,
    ):
        """
        Args:
            tracker: Issue tracker adapter
            dry_run: True ise issue oluşturmaz, sadece log
        """
        self.tracker = tracker
        self.dry_run = dry_run
    
    def get_unreported_bugs(
        self,
        db: Session,
        tenant_id: str,
        limit: int = 50,
    ) -> list[Incident]:
        """
        Raporlanmamış BUG_REPORT incident'larını getir.
        
        Kriterler:
        - status = REPORTED
        - action_type = BUG_REPORT
        - external_issue_id IS NULL
        """
        return (
            db.query(Incident)
            .filter(
                and_(
                    Incident.tenant_id == tenant_id,
                    Incident.status == "REPORTED",
                    Incident.action_type == "BUG_REPORT",
                    Incident.external_issue_id.is_(None),
                )
            )
            .order_by(Incident.created_at.asc())
            .limit(limit)
            .all()
        )
    
    def _build_issue_title(self, incident: Incident) -> str:
        """Issue başlığı oluştur."""
        primary_flag = incident.primary_flag or "UNKNOWN"
        provider = incident.provider or "unknown"
        period = incident.period or "unknown"
        
        return f"[{primary_flag}] {provider} - {period}"
    
    def _build_issue_body(self, incident: Incident) -> str:
        """Issue body oluştur (markdown)."""
        lines = [
            f"## Incident #{incident.id}",
            "",
            f"**Primary Flag:** `{incident.primary_flag}`",
            f"**Category:** `{incident.category}`",
            f"**Severity:** `{incident.severity}`",
            f"**Provider:** `{incident.provider}`",
            f"**Period:** `{incident.period}`",
            "",
            "### All Flags",
            "",
        ]
        
        all_flags = incident.all_flags or []
        for flag in all_flags:
            lines.append(f"- `{flag}`")
        
        lines.extend([
            "",
            "### Action",
            "",
            f"- **Type:** `{incident.action_type}`",
            f"- **Owner:** `{incident.action_owner}`",
            f"- **Code:** `{incident.action_code}`",
            "",
            "### Context",
            "",
            f"- **Dedupe Key:** `{incident.dedupe_key}`",
            f"- **Dedupe Bucket:** `{incident.dedupe_bucket}`",
            f"- **Occurrence Count:** `{incident.occurrence_count}`",
            f"- **First Seen:** `{incident.first_seen_at}`",
            f"- **Last Seen:** `{incident.last_seen_at}`",
            "",
        ])
        
        # Routed payload varsa ekle
        if incident.routed_payload:
            lines.extend([
                "### Routed Payload",
                "",
                "```json",
                str(incident.routed_payload)[:2000],  # Truncate
                "```",
                "",
            ])
        
        lines.extend([
            "---",
            f"*Auto-generated by Incident System (trace_id: {incident.trace_id})*",
        ])
        
        return "\n".join(lines)
    
    def _build_labels(self, incident: Incident) -> list[str]:
        """Issue label'ları oluştur."""
        labels = ["incident", "auto-generated"]
        
        if incident.category:
            labels.append(f"category:{incident.category}")
        
        if incident.severity:
            labels.append(f"severity:{incident.severity}")
        
        if incident.primary_flag:
            labels.append(f"flag:{incident.primary_flag}")
        
        if incident.action_owner:
            labels.append(f"owner:{incident.action_owner}")
        
        return labels
    
    def report_incident(
        self,
        db: Session,
        incident_id: int,
        now: Optional[datetime] = None,
    ) -> IssueCreationResult:
        """
        Tek bir incident'ı issue tracker'a raporla.
        
        Idempotent: external_issue_id varsa skip.
        
        Args:
            db: Database session
            incident_id: Incident ID
            now: Şimdi (test için override)
        
        Returns:
            IssueCreationResult
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            return IssueCreationResult(
                success=False,
                error_message=f"Incident #{incident_id} not found",
            )
        
        # Idempotency check
        if incident.external_issue_id:
            logger.info(
                f"[ISSUE] Incident #{incident_id} already reported: "
                f"{incident.external_issue_id}"
            )
            return IssueCreationResult(
                success=True,
                issue_id=incident.external_issue_id,
                issue_url=incident.external_issue_url,
            )
        
        # Build issue content
        title = self._build_issue_title(incident)
        body = self._build_issue_body(incident)
        labels = self._build_labels(incident)
        metadata = {
            "incident_id": incident.id,
            "tenant_id": incident.tenant_id,
            "dedupe_key": incident.dedupe_key,
            "dedupe_bucket": incident.dedupe_bucket,
            "trace_id": incident.trace_id,
        }
        
        if self.dry_run:
            logger.info(f"[ISSUE] DRY RUN: Would create issue for incident #{incident_id}")
            return IssueCreationResult(
                success=True,
                issue_id="DRY-RUN",
                issue_url="https://dry-run.example/issues/DRY-RUN",
            )
        
        # Create issue
        result = self.tracker.create_issue(title, body, labels, metadata)
        
        if result.success:
            # Update incident
            incident.external_issue_id = result.issue_id
            incident.external_issue_url = result.issue_url
            incident.reported_at = now
            incident.updated_at = now
            db.commit()
            
            logger.info(
                f"[ISSUE] Created issue {result.issue_id} for incident #{incident_id}"
            )
        else:
            logger.error(
                f"[ISSUE] Failed to create issue for incident #{incident_id}: "
                f"{result.error_message}"
            )
        
        return result
    
    def report_batch(
        self,
        db: Session,
        tenant_id: str,
        limit: int = 50,
        now: Optional[datetime] = None,
    ) -> dict:
        """
        Batch issue reporting.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            limit: Max batch size
            now: Şimdi (test için override)
        
        Returns:
            Summary dict
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        incidents = self.get_unreported_bugs(db, tenant_id, limit)
        
        summary = {
            "total": len(incidents),
            "success": 0,
            "failed": 0,
            "skipped": 0,
        }
        
        for incident in incidents:
            result = self.report_incident(db, incident.id, now)
            
            if result.success:
                if result.issue_id == "DRY-RUN":
                    summary["skipped"] += 1
                else:
                    summary["success"] += 1
            else:
                summary["failed"] += 1
        
        logger.info(
            f"[ISSUE] Batch complete: total={summary['total']} "
            f"success={summary['success']} failed={summary['failed']} "
            f"skipped={summary['skipped']}"
        )
        
        return summary
