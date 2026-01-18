"""
Action Router - Sprint 6.0

Incident action type'a gore routing yapar.
4 route: USER_FIX, RETRY_LOOKUP, BUG_REPORT, FALLBACK_OK
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from .issue_payload import IssuePayloadBuilder


ActionType = Literal["USER_FIX", "RETRY_LOOKUP", "BUG_REPORT", "FALLBACK_OK"]
IncidentStatus = Literal["OPEN", "PENDING_RETRY", "REPORTED", "AUTO_RESOLVED"]


@dataclass(frozen=True)
class UiAlertPayload:
    """USER_FIX icin UI alert payload."""
    message: str
    code: str
    
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrySchedule:
    """RETRY_LOOKUP icin retry schedule."""
    retry_eligible_at: datetime
    reason_code: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "retry_eligible_at": self.retry_eligible_at.isoformat(),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class RoutedAction:
    """Router ciktisi."""
    action_type: ActionType
    status: IncidentStatus
    payload: Optional[dict[str, Any]]
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "status": self.status,
            "payload": self.payload,
        }


class ActionRouter:
    """
    Incident action router.
    
    Routes:
    - USER_FIX → UI alert payload, status=OPEN
    - RETRY_LOOKUP → Retry schedule, status=PENDING_RETRY
    - BUG_REPORT → Issue payload, status=REPORTED
    - FALLBACK_OK → No payload, status=AUTO_RESOLVED
    """
    
    # Default retry delay (dakika)
    DEFAULT_RETRY_DELAY_MINUTES = 30
    
    def __init__(
        self,
        *,
        issue_builder: Optional[IssuePayloadBuilder] = None,
        retry_delay_minutes: int = DEFAULT_RETRY_DELAY_MINUTES,
    ):
        """
        Args:
            issue_builder: IssuePayloadBuilder instance (BUG_REPORT icin)
            retry_delay_minutes: Retry delay suresi
        """
        self.issue_builder = issue_builder or IssuePayloadBuilder()
        self.retry_delay_minutes = retry_delay_minutes
    
    def route(
        self,
        *,
        incident: dict,
        provider: str,
        invoice_id: str,
        period: str,
        dedupe_key: str,
        calc_context: Optional[dict[str, Any]] = None,
        lookup_evidence: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> RoutedAction:
        """
        Incident'i route eder.
        
        Args:
            incident: Incident dict (action, primary_flag, category, severity, all_flags)
            provider: Fatura saglayici
            invoice_id: Fatura ID
            period: YYYY-MM
            dedupe_key: Stabil dedupe key
            calc_context: Hesaplama context'i
            lookup_evidence: Lookup sonuclari
            now: Simdi (test icin override)
        
        Returns:
            RoutedAction instance
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        action = incident.get("action") or {}
        action_type = action.get("type")
        
        if action_type == "USER_FIX":
            return self._route_user_fix(action)
        
        if action_type == "RETRY_LOOKUP":
            return self._route_retry_lookup(action, now)
        
        if action_type == "BUG_REPORT":
            return self._route_bug_report(
                incident=incident,
                provider=provider,
                invoice_id=invoice_id,
                period=period,
                dedupe_key=dedupe_key,
                calc_context=calc_context,
                lookup_evidence=lookup_evidence,
            )
        
        if action_type == "FALLBACK_OK":
            return self._route_fallback_ok()
        
        # Unknown action type → default to USER_FIX
        return RoutedAction(
            action_type="USER_FIX",
            status="OPEN",
            payload={"ui_alert": {"message": "Incele", "code": "UNKNOWN"}},
        )
    
    def _route_user_fix(self, action: dict) -> RoutedAction:
        """USER_FIX route."""
        code = action.get("code") or "UNKNOWN"
        hint_text = action.get("hint_text") or "Incele"
        
        ui_alert = UiAlertPayload(message=hint_text, code=code)
        
        return RoutedAction(
            action_type="USER_FIX",
            status="OPEN",
            payload={"ui_alert": ui_alert.to_dict()},
        )
    
    def _route_retry_lookup(self, action: dict, now: datetime) -> RoutedAction:
        """RETRY_LOOKUP route - sadece schedule, gercek retry Sprint 7'de."""
        code = action.get("code") or "UNKNOWN"
        
        retry_schedule = RetrySchedule(
            retry_eligible_at=now + timedelta(minutes=self.retry_delay_minutes),
            reason_code=code,
        )
        
        return RoutedAction(
            action_type="RETRY_LOOKUP",
            status="PENDING_RETRY",
            payload={"retry": retry_schedule.to_dict()},
        )
    
    def _route_bug_report(
        self,
        *,
        incident: dict,
        provider: str,
        invoice_id: str,
        period: str,
        dedupe_key: str,
        calc_context: Optional[dict[str, Any]],
        lookup_evidence: Optional[dict[str, Any]],
    ) -> RoutedAction:
        """BUG_REPORT route - issue payload uretir."""
        issue = self.issue_builder.build(
            incident=incident,
            dedupe_key=dedupe_key,
            provider=provider,
            invoice_id=invoice_id,
            period=period,
            calc_context=calc_context,
            lookup_evidence=lookup_evidence,
        )
        
        return RoutedAction(
            action_type="BUG_REPORT",
            status="REPORTED",
            payload={"issue": issue.to_dict()},
        )
    
    def _route_fallback_ok(self) -> RoutedAction:
        """FALLBACK_OK route - auto-resolved, payload yok."""
        return RoutedAction(
            action_type="FALLBACK_OK",
            status="AUTO_RESOLVED",
            payload=None,
        )
