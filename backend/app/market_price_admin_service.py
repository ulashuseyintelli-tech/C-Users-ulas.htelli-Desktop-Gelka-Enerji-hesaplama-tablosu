"""
MarketPriceAdminService - PTF Admin Management için CRUD operasyonları.

Sorumluluklar:
- Upsert (tek kayıt / bulk) with status transition rules
- Get / List with filtering and pagination
- Calculation lookup (final > provisional priority)
- Audit trail (change_reason, updated_by, source)

Feature: ptf-admin-management
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .database import MarketReferencePrice, PriceChangeHistory
from .market_price_validator import (
    NormalizedMarketPriceInput,
    ErrorCode as ValidatorErrorCode,
    ValidationError,
)

logger = logging.getLogger(__name__)

TR_TIMEZONE = ZoneInfo("Europe/Istanbul")


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR CODES (stable contract)
# ═══════════════════════════════════════════════════════════════════════════════

class ServiceErrorCode(str, Enum):
    """Service-level error codes."""
    # Business rule errors
    CHANGE_REASON_REQUIRED = "CHANGE_REASON_REQUIRED"
    PERIOD_LOCKED = "PERIOD_LOCKED"
    FINAL_RECORD_PROTECTED = "FINAL_RECORD_PROTECTED"
    STATUS_DOWNGRADE_FORBIDDEN = "STATUS_DOWNGRADE_FORBIDDEN"
    
    # Lookup errors
    PERIOD_NOT_FOUND = "PERIOD_NOT_FOUND"
    FUTURE_PERIOD = "FUTURE_PERIOD"
    
    # DB errors
    DB_CONFLICT = "DB_CONFLICT"
    DB_WRITE_FAILED = "DB_WRITE_FAILED"


@dataclass
class ServiceError:
    """Structured service error."""
    error_code: ServiceErrorCode
    field: Optional[str]
    message: str
    
    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code.value,
            "field": self.field,
            "message": self.message
        }


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT TYPES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class UpsertResult:
    """Result of single upsert operation."""
    success: bool
    created: bool  # True if new record, False if update
    changed: bool  # True if data actually changed (no-op if same)
    record: Optional[MarketReferencePrice] = None
    error: Optional[ServiceError] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class BulkRowError:
    """Error for a single row in bulk operation."""
    row_index: int
    error_code: str
    field: Optional[str]
    message: str


@dataclass
class BulkUpsertResult:
    """Result of bulk upsert operation."""
    success: bool
    created_count: int = 0
    updated_count: int = 0
    noop_count: int = 0  # Same data, no write
    failed_count: int = 0
    errors: List[BulkRowError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class MarketPriceLookupResult:
    """Result of calculation lookup."""
    period: str
    value: Decimal  # TL/MWh
    status: str
    price_type: str
    is_provisional_used: bool
    source: str
    captured_at: datetime


@dataclass
class PaginatedResult:
    """Paginated list result."""
    items: List[MarketReferencePrice]
    total: int
    has_more: bool
    next_cursor: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class MarketPriceAdminService:
    """
    Admin service for market price management.
    
    Key behaviors:
    - Upsert with status transition rules (provisional→final OK, final→final needs force_update)
    - No-op detection (same data = no write, no audit churn)
    - change_reason required for updates
    - Calculation lookup with final > provisional priority
    """
    
    def upsert_price(
        self,
        db: Session,
        normalized: NormalizedMarketPriceInput,
        *,
        updated_by: str,
        source: str,
        change_reason: Optional[str] = None,
        captured_at: Optional[datetime] = None,
        force_update: bool = False,
    ) -> UpsertResult:
        """
        Insert or update a market price record.
        
        Status transition rules:
        - provisional → provisional: ALLOW
        - provisional → final: ALLOW (upgrade)
        - final → provisional: REJECT (downgrade forbidden)
        - final → final (same value): ALLOW (no-op)
        - final → final (diff value): REQUIRE force_update
        
        **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7**
        """
        warnings: List[str] = []
        
        # Check existing record
        existing = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.price_type == normalized.price_type,
            MarketReferencePrice.period == normalized.period
        ).first()
        
        if existing:
            # UPDATE path
            return self._handle_update(
                db=db,
                existing=existing,
                normalized=normalized,
                updated_by=updated_by,
                source=source,
                change_reason=change_reason,
                captured_at=captured_at,
                force_update=force_update,
                warnings=warnings,
            )
        else:
            # INSERT path
            return self._handle_insert(
                db=db,
                normalized=normalized,
                updated_by=updated_by,
                source=source,
                change_reason=change_reason,
                captured_at=captured_at,
                warnings=warnings,
            )
    
    def _write_history(
        self,
        db: Session,
        record: MarketReferencePrice,
        action: str,
        old_value: Optional[float],
        new_value: float,
        old_status: Optional[str],
        new_status: str,
        change_reason: Optional[str],
        updated_by: Optional[str],
        source: Optional[str],
    ) -> None:
        """
        Append-only history write. Best-effort: errors are logged, never raised.
        
        Called after successful commit in _handle_insert() and _handle_update().
        No-op updates never reach this method (early return in _handle_update).
        
        Feature: audit-history, Requirement 1.1, 1.2, 1.5
        """
        try:
            history = PriceChangeHistory(
                price_record_id=record.id,
                price_type=record.price_type,
                period=record.period,
                action=action,
                old_value=old_value,
                new_value=new_value,
                old_status=old_status,
                new_status=new_status,
                change_reason=change_reason,
                updated_by=updated_by,
                source=source,
            )
            db.add(history)
            db.commit()
            logger.debug(
                f"History written: {action} {record.price_type}/{record.period} "
                f"by {updated_by}"
            )
        except Exception as e:
            db.rollback()
            logger.warning(
                f"History write failed for {record.price_type}/{record.period}: {e}"
            )

    def _handle_insert(
        self,
        db: Session,
        normalized: NormalizedMarketPriceInput,
        updated_by: str,
        source: str,
        change_reason: Optional[str],
        captured_at: Optional[datetime],
        warnings: List[str],
    ) -> UpsertResult:
        """Handle INSERT path."""
        now = datetime.utcnow()
        
        try:
            record = MarketReferencePrice(
                price_type=normalized.price_type,
                period=normalized.period,
                ptf_tl_per_mwh=float(normalized.value),
                yekdem_tl_per_mwh=0,  # Default, can be extended
                status=normalized.status,
                source=source,
                captured_at=captured_at or now,
                change_reason=change_reason,
                updated_by=updated_by,
                is_locked=0,
                created_at=now,
                updated_at=now,
            )
            
            db.add(record)
            db.commit()
            db.refresh(record)
            
            logger.info(
                f"Created market price: {normalized.price_type}/{normalized.period} "
                f"= {normalized.value} TL/MWh ({normalized.status}) by {updated_by}"
            )
            
            # Audit history — best-effort write (Requirement 1.1)
            self._write_history(
                db=db,
                record=record,
                action="INSERT",
                old_value=None,
                new_value=float(normalized.value),
                old_status=None,
                new_status=normalized.status,
                change_reason=change_reason,
                updated_by=updated_by,
                source=source,
            )
            
            return UpsertResult(
                success=True,
                created=True,
                changed=True,
                record=record,
                warnings=warnings,
            )
            
        except IntegrityError as e:
            db.rollback()
            logger.error(f"DB integrity error on insert: {e}")
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.DB_CONFLICT,
                    field=None,
                    message="Kayıt zaten mevcut (race condition)."
                ),
            )
        except Exception as e:
            db.rollback()
            logger.error(f"DB write error on insert: {e}")
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.DB_WRITE_FAILED,
                    field=None,
                    message=f"Veritabanı yazma hatası: {str(e)}"
                ),
            )
    
    def _handle_update(
        self,
        db: Session,
        existing: MarketReferencePrice,
        normalized: NormalizedMarketPriceInput,
        updated_by: str,
        source: str,
        change_reason: Optional[str],
        captured_at: Optional[datetime],
        force_update: bool,
        warnings: List[str],
    ) -> UpsertResult:
        """Handle UPDATE path with status transition rules."""
        
        # Check if locked
        if existing.is_locked:
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.PERIOD_LOCKED,
                    field="period",
                    message=f"Dönem {normalized.period} kilitli, güncellenemez."
                ),
            )
        
        # Check status transition
        old_status = existing.status
        new_status = normalized.status
        old_value = Decimal(str(existing.ptf_tl_per_mwh))
        new_value = normalized.value
        
        # Status downgrade check (final → provisional)
        if old_status == "final" and new_status == "provisional":
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.STATUS_DOWNGRADE_FORBIDDEN,
                    field="status",
                    message="Final kayıt provisional'a düşürülemez."
                ),
            )
        
        # Final record protection (final → final with different value)
        if old_status == "final" and new_status == "final":
            if old_value != new_value and not force_update:
                return UpsertResult(
                    success=False,
                    created=False,
                    changed=False,
                    error=ServiceError(
                        error_code=ServiceErrorCode.FINAL_RECORD_PROTECTED,
                        field="value",
                        message="Final kayıt değiştirmek için force_update gerekli."
                    ),
                )
        
        # No-op detection (same value and status)
        if old_value == new_value and old_status == new_status:
            logger.debug(
                f"No-op update for {normalized.price_type}/{normalized.period}: "
                f"same value ({new_value}) and status ({new_status})"
            )
            return UpsertResult(
                success=True,
                created=False,
                changed=False,
                record=existing,
                warnings=warnings,
            )
        
        # change_reason required for actual updates
        if not change_reason:
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.CHANGE_REASON_REQUIRED,
                    field="change_reason",
                    message="Güncelleme için değişiklik nedeni zorunludur."
                ),
            )
        
        # Perform update
        try:
            existing.ptf_tl_per_mwh = float(new_value)
            existing.status = new_status
            existing.source = source
            existing.change_reason = change_reason
            existing.updated_by = updated_by
            existing.updated_at = datetime.utcnow()
            # captured_at only updated if explicitly provided
            if captured_at:
                existing.captured_at = captured_at
            
            db.commit()
            db.refresh(existing)
            
            logger.info(
                f"Updated market price: {normalized.price_type}/{normalized.period} "
                f"{old_value}→{new_value} TL/MWh ({old_status}→{new_status}) by {updated_by}"
            )
            
            # Audit history — best-effort write (Requirement 1.2)
            self._write_history(
                db=db,
                record=existing,
                action="UPDATE",
                old_value=float(old_value),
                new_value=float(new_value),
                old_status=old_status,
                new_status=new_status,
                change_reason=change_reason,
                updated_by=updated_by,
                source=source,
            )
            
            return UpsertResult(
                success=True,
                created=False,
                changed=True,
                record=existing,
                warnings=warnings,
            )
            
        except Exception as e:
            db.rollback()
            logger.error(f"DB write error on update: {e}")
            return UpsertResult(
                success=False,
                created=False,
                changed=False,
                error=ServiceError(
                    error_code=ServiceErrorCode.DB_WRITE_FAILED,
                    field=None,
                    message=f"Veritabanı yazma hatası: {str(e)}"
                ),
            )
    
    def bulk_upsert(
        self,
        db: Session,
        normalized_list: List[NormalizedMarketPriceInput],
        *,
        updated_by: str,
        source: str,
        change_reason: Optional[str] = None,
        captured_at: Optional[datetime] = None,
        atomic: bool = True,
        force_update: bool = False,
    ) -> BulkUpsertResult:
        """
        Bulk upsert market prices.
        
        Args:
            atomic: If True, rollback all on any failure. If False, best-effort.
            force_update: If True, allow overwriting final records.
        
        **Validates: Requirements 5.4, 5.5, 5.7**
        """
        created_count = 0
        updated_count = 0
        noop_count = 0
        failed_count = 0
        errors: List[BulkRowError] = []
        warnings: List[str] = []
        
        for idx, normalized in enumerate(normalized_list):
            result = self.upsert_price(
                db=db,
                normalized=normalized,
                updated_by=updated_by,
                source=source,
                change_reason=change_reason,
                captured_at=captured_at,
                force_update=force_update,
            )
            
            if result.success:
                if result.created:
                    created_count += 1
                elif result.changed:
                    updated_count += 1
                else:
                    noop_count += 1
                warnings.extend(result.warnings)
            else:
                failed_count += 1
                if result.error:
                    errors.append(BulkRowError(
                        row_index=idx,
                        error_code=result.error.error_code.value,
                        field=result.error.field,
                        message=result.error.message,
                    ))
                
                if atomic:
                    # Rollback and return
                    db.rollback()
                    return BulkUpsertResult(
                        success=False,
                        created_count=0,
                        updated_count=0,
                        noop_count=0,
                        failed_count=failed_count,
                        errors=errors,
                        warnings=[f"Atomic mode: rollback due to error at row {idx}"],
                    )
        
        return BulkUpsertResult(
            success=failed_count == 0,
            created_count=created_count,
            updated_count=updated_count,
            noop_count=noop_count,
            failed_count=failed_count,
            errors=errors,
            warnings=warnings,
        )
    
    def get_by_key(
        self,
        db: Session,
        *,
        price_type: str,
        period: str,
    ) -> Optional[MarketReferencePrice]:
        """Get single record by (price_type, period)."""
        return db.query(MarketReferencePrice).filter(
            MarketReferencePrice.price_type == price_type,
            MarketReferencePrice.period == period
        ).first()
    
    def get_for_calculation(
        self,
        db: Session,
        period: str,
        price_type: str = "PTF",
    ) -> Tuple[Optional[MarketPriceLookupResult], Optional[ServiceError]]:
        """
        Get market price for calculation with final > provisional priority.
        
        Rules:
        1. If final record exists → return final, is_provisional_used=False
        2. If only provisional exists → return provisional, is_provisional_used=True
        3. If no record exists → return error PERIOD_NOT_FOUND
        4. If future period → return error FUTURE_PERIOD
        
        **Validates: Requirements 7.1, 7.2, 7.3, 7.5, 7.6, 7.7**
        """
        # Future period check
        now_tr = datetime.now(TR_TIMEZONE)
        current_period = now_tr.strftime("%Y-%m")
        if period > current_period:
            return None, ServiceError(
                error_code=ServiceErrorCode.FUTURE_PERIOD,
                field="period",
                message=f"Gelecek dönem ({period}) için fiyat sorgulanamaz."
            )
        
        record = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.price_type == price_type,
            MarketReferencePrice.period == period
        ).first()
        
        if not record:
            return None, ServiceError(
                error_code=ServiceErrorCode.PERIOD_NOT_FOUND,
                field="period",
                message=f"Dönem {period} için {price_type} kaydı bulunamadı."
            )
        
        return MarketPriceLookupResult(
            period=record.period,
            value=Decimal(str(record.ptf_tl_per_mwh)),
            status=record.status,
            price_type=record.price_type,
            is_provisional_used=(record.status == "provisional"),
            source=record.source,
            captured_at=record.captured_at,
        ), None
    
    def list_prices(
        self,
        db: Session,
        *,
        price_type: Optional[str] = None,
        period_from: Optional[str] = None,
        period_to: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
        sort_by: str = "period",
        sort_order: str = "desc",
    ) -> PaginatedResult:
        """
        List market prices with filtering and pagination.
        
        **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5**
        """
        query = db.query(MarketReferencePrice)
        
        # Filters
        if price_type:
            query = query.filter(MarketReferencePrice.price_type == price_type)
        if status:
            query = query.filter(MarketReferencePrice.status == status)
        if period_from:
            query = query.filter(MarketReferencePrice.period >= period_from)
        if period_to:
            query = query.filter(MarketReferencePrice.period <= period_to)
        
        # Total count
        total = query.count()
        
        # Sorting
        sort_column = getattr(MarketReferencePrice, sort_by, MarketReferencePrice.period)
        if sort_order == "desc":
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
        
        # Pagination
        items = query.offset(offset).limit(limit).all()
        has_more = (offset + len(items)) < total
        
        return PaginatedResult(
            items=items,
            total=total,
            has_more=has_more,
            next_cursor=str(offset + limit) if has_more else None,
        )

    def get_history(
        self,
        db: Session,
        period: str,
        price_type: str = "PTF",
    ) -> Optional[List[PriceChangeHistory]]:
        """
        Fetch change history for a period+price_type.
        
        Returns None if the price record doesn't exist (→ 404 at API layer).
        Returns [] if record exists but no history yet (→ 200 with empty list).
        
        Feature: audit-history, Requirement 3.1, 3.3, 3.4
        """
        # Check if the price record exists
        record = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.price_type == price_type,
            MarketReferencePrice.period == period,
        ).first()
        
        if record is None:
            return None
        
        # Fetch history ordered by created_at DESC
        history = db.query(PriceChangeHistory).filter(
            PriceChangeHistory.price_type == price_type,
            PriceChangeHistory.period == period,
        ).order_by(PriceChangeHistory.created_at.desc()).all()
        
        return history


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════

_service = MarketPriceAdminService()


def get_market_price_admin_service() -> MarketPriceAdminService:
    """Get singleton service instance."""
    return _service
