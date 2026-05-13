"""
PTF Drift Log — Phase 1 T1.3 skeleton (ptf-sot-unification)

Persistence layer for canonical↔legacy PTF drift observations captured during
the Phase 2 dual-read window. Phase 1 adds only the SQLAlchemy model + sibling
alembic migration (012); the `record_drift` / `compute_drift` functions land in
T2.2 once the dispatcher exists.

## Design decisions (locked)

1. **Sync write, best-effort, fail-open.** When `record_drift` ships in T2.2 it
   MUST swallow any exception and let the pricing response proceed. A drift
   observability outage is not a pricing outage. No Celery / background queue /
   async pipeline in v1 — the simplest thing that works.

2. **Minimal columns.** The table captures the 8 fields that are actionable in a
   dashboard query; it is NOT a raw-payload archive. No request JSON, no
   stacktrace, no response snapshot. If you find yourself wanting to add a
   `raw_payload` column, stop and open a new spec — drift log is not a debug log.

3. **`request_hash` is mandatory.** SHA-256 over the canonical normalized input
   tuple (period + profile kWh + multiplier + voltage etc.). Purpose: identify
   repeating drifts. The hash is produced from the normalized request — NOT the
   full body — so benign differences (timestamps, trace ids) don't split groups.
   The exact input tuple and serializer are defined in T2.2.

4. **Retention target: 30 days.** This is NOT enforced in code (T4.6 decides the
   final policy). But downstream queries, dashboards, and alembic downgrade
   procedures should not assume indefinite retention. A janitor job or alembic
   013 will prune older rows; out of scope for Phase 1.

5. **Indexes now, not later.** Reporting queries will filter on
   `(created_at, period, request_hash)`. Without indexes the drift analysis in
   T2.5 becomes a full-table scan, which is fine at day 1 but intolerable at
   day 14. Three secondary indexes are declared at creation time.

## Columns

- `id` (PK)
- `created_at` (indexed, UTC)
- `period` (indexed, YYYY-MM)
- `canonical_price` (TL/MWh, always set)
- `legacy_price` (TL/MWh, nullable — legacy read may fail)
- `delta_abs` (TL/MWh, nullable when legacy_price is None)
- `delta_pct` (percent, nullable when legacy_price is None or canonical is 0)
- `severity` (VARCHAR 10: "low" | "high", design §3.2 gate)
- `request_hash` (indexed, SHA-256 hex, 64 chars)
- `customer_id` (nullable; filled only when request is customer-scoped)

Tech-debt notes:
- Retention: 30-day target, policy decision pending (T4.6).
- Writer helpers (`compute_drift`, `record_drift`) intentionally omitted here;
  they land in T2.2 alongside dispatcher wiring so the skeleton stays small.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)

from .database import Base

logger = logging.getLogger(__name__)


class PtfDriftLog(Base):
    """Canonical↔legacy PTF drift observation — append-only, 30-day retention target."""

    __tablename__ = "ptf_drift_log"
    __table_args__ = (
        # severity is constrained to the three values the Phase 2 dashboard
        # expects: 'low' / 'high' (drift severities) and 'missing_legacy'
        # (operational state — legacy read returned empty/None, so no delta
        # was computed). Any fourth value means "pipeline bug" and should
        # fail CHECK rather than pollute the series.
        # Migration 013 widened this from the 2-value version in 012.
        CheckConstraint(
            "severity IN ('low', 'high', 'missing_legacy')",
            name="ck_ptf_drift_log_severity",
        ),
        # request_hash must be a 64-char sha256 hex; enforce length to catch
        # accidental md5/sha1 or raw bytes smuggled through the writer.
        CheckConstraint(
            "length(request_hash) = 64", name="ck_ptf_drift_log_request_hash_len"
        ),
        # Secondary indexes for the Phase 2 analysis queries:
        #   (created_at) — time-series windowing, retention cleanup
        #   (period)     — per-period drift aggregation
        #   (request_hash) — "same drift repeating?" dedupe lookup
        Index("ix_ptf_drift_log_created_at", "created_at"),
        Index("ix_ptf_drift_log_period", "period"),
        Index("ix_ptf_drift_log_request_hash", "request_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    period = Column(String(7), nullable=False)
    canonical_price = Column(Float, nullable=False)
    # Nullable: legacy read may fail (table empty for period, connection error).
    # `record_drift` (T2.2) must still write a row with severity=low so we know
    # dual-read is alive even when legacy is silent.
    legacy_price = Column(Float, nullable=True)
    delta_abs = Column(Float, nullable=True)
    delta_pct = Column(Float, nullable=True)
    severity = Column(String(10), nullable=False)
    request_hash = Column(String(64), nullable=False)
    # Customer-scoped requests (e.g., /full-process) fill this so a single
    # customer's drifts can be isolated during incident review. Nullable
    # because /api/pricing/analyze isn't customer-scoped.
    customer_id = Column(Integer, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover — debug aid
        return (
            f"<PtfDriftLog id={self.id} period={self.period!r} "
            f"severity={self.severity!r} delta_pct={self.delta_pct}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Fail-open write helper (Phase 1 T1.3 — persistence-only skeleton)
# ─────────────────────────────────────────────────────────────────────────────
#
# Purpose
# -------
# Provide a single, narrow entry point for writing a drift observation to the
# `ptf_drift_log` table that is GUARANTEED to never raise. The caller (which
# arrives in T2.2) computes drift and hands a fully-formed row; this helper
# inserts it.
#
# Why fail-open is locked at this layer
# -------------------------------------
# Drift observability is a side-channel. A drift-write outage MUST NOT become a
# pricing outage. Concretely: if the DB connection dies, the table is locked,
# the row violates a CHECK, the SQLAlchemy session is in a bad state, etc.,
# this function logs a warning and returns False. The caller proceeds with the
# canonical price as if no drift logging existed.
#
# Scope discipline (T1.3 vs T2.2 vs T2.4)
# ---------------------------------------
#   T1.3 (here):  write surface only. Caller hands a finished record.
#                 No drift math, no canonical/legacy comparison, no severity
#                 classification, no request-hash construction.
#   T2.2:         compute_drift() + record_drift() — given canonical/legacy
#                 numbers, produce a record and call write_drift_record() below.
#   T2.4:         flip ptf_drift_log_enabled default to True after dual-read
#                 dispatcher (T2.1) is wired.
#
# This helper is intentionally NOT called from any production path yet. It
# exists so the persistence surface can be unit-tested for fail-open semantics
# in isolation, and so T2.2 has a stable target to import.

class DriftRecord:
    """Plain in-memory drift observation, decoupled from SQLAlchemy session.

    The caller in T2.2 will populate this. Keeping it as a tiny dataclass-like
    object (rather than handing a `PtfDriftLog` instance directly) means the
    write helper owns session lifecycle and the caller can't accidentally
    attach a stray object to its own session.
    """

    __slots__ = (
        "period",
        "canonical_price",
        "legacy_price",
        "delta_abs",
        "delta_pct",
        "severity",
        "request_hash",
        "customer_id",
    )

    def __init__(
        self,
        *,
        period: str,
        canonical_price: float,
        severity: str,
        request_hash: str,
        legacy_price: float | None = None,
        delta_abs: float | None = None,
        delta_pct: float | None = None,
        customer_id: int | None = None,
    ) -> None:
        self.period = period
        self.canonical_price = canonical_price
        self.legacy_price = legacy_price
        self.delta_abs = delta_abs
        self.delta_pct = delta_pct
        self.severity = severity
        self.request_hash = request_hash
        self.customer_id = customer_id


def write_drift_record(db_session: Any, record: DriftRecord) -> bool:
    """Best-effort, fail-open insert of a single drift observation.

    Contract:
        - On success: row committed, returns True.
        - On ANY failure (DB error, CHECK violation, type error, session in bad
          state, etc.): rolls back if possible, logs a warning, returns False.
        - NEVER raises. Pricing pipeline depends on this guarantee.

    Args:
        db_session: An active SQLAlchemy session. Caller owns its lifecycle.
        record: Pre-built DriftRecord. Caller (T2.2) is responsible for drift
                math, severity classification, and request-hash construction.

    Returns:
        True if the row landed and was committed. False if anything went wrong.
    """
    if record is None:
        # Defensive: a None record is a programmer error, but we still don't
        # raise into the pricing path.
        logger.warning("[PTF-DRIFT] write_drift_record received None — skipped")
        return False

    try:
        row = PtfDriftLog(
            period=record.period,
            canonical_price=record.canonical_price,
            legacy_price=record.legacy_price,
            delta_abs=record.delta_abs,
            delta_pct=record.delta_pct,
            severity=record.severity,
            request_hash=record.request_hash,
            customer_id=record.customer_id,
        )
        db_session.add(row)
        db_session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        # Best-effort rollback. If the session itself is the problem, this
        # also fails silently — that's still acceptable. The pricing path
        # has its own session and is unaffected either way.
        try:
            db_session.rollback()
        except Exception:  # pragma: no cover — secondary failure path
            pass
        logger.warning(
            "[PTF-DRIFT] write_drift_record failed (fail-open) "
            "period=%s severity=%s err=%s",
            getattr(record, "period", "<unknown>"),
            getattr(record, "severity", "<unknown>"),
            exc,
        )
        return False



# ─────────────────────────────────────────────────────────────────────────────
# Drift computation + record helpers (Phase 2 T2.2)
# ─────────────────────────────────────────────────────────────────────────────
#
# These functions wrap the write_drift_record() persistence layer with the
# actual canonical↔legacy comparison. They are pure-Python (no DB query)
# except for record_drift() which calls write_drift_record(). Anyone wiring
# drift telemetry into a new code path imports record_drift() and hands it
# the two record lists already loaded by the dispatcher.
#
# Design contracts:
#   1. NEVER raise. compute_drift returns None on bad input; record_drift
#      catches everything and returns False.
#   2. Symmetric drift baseline. delta_pct uses max(|canonical|, |legacy|),
#      not canonical alone. Otherwise the drift % becomes biased toward the
#      authoritative side and the threshold gate stops being neutral.
#   3. Six-decimal rounding on delta_abs and delta_pct. Without rounding,
#      float jitter creates dashboard noise and flaky tests.
#   4. missing_legacy is a first-class severity (not a NULL-coded hack), so
#      Phase 3 readiness queries can group on `severity` directly.
#
# Phase 2 observation threshold (DRIFT_HIGH_PCT) is observation-only telemetry,
# NOT an automated cutover gate. Operators read severity counts in the Phase 2
# decision review (T2.6) and decide. Migration is never auto-aborted by drift.

# Phase 2 observation threshold only.
# Not yet an automated cutover gate.
DRIFT_HIGH_PCT: float = 0.5

_DRIFT_ROUND_NDIGITS: int = 6


def _weighted_avg_ptf(records) -> float | None:
    """Mean of ptf_tl_per_mwh across ParsedMarketRecord items.

    Returns None for empty / None inputs, never raises. We treat this as
    "no data available" so the caller can decide between missing_legacy
    (legacy side) and "skip drift entirely" (canonical side).
    """
    if records is None:
        return None
    try:
        n = len(records)
    except TypeError:
        return None
    if n == 0:
        return None
    try:
        total = 0.0
        for r in records:
            total += float(r.ptf_tl_per_mwh)
        return total / n
    except (AttributeError, TypeError, ValueError):
        return None


def compute_drift(
    canonical_records,
    legacy_records,
    *,
    period: str,
    request_hash: str,
    customer_id: int | None = None,
) -> DriftRecord | None:
    """Compute canonical↔legacy drift and produce a DriftRecord.

    Args:
        canonical_records: list of ParsedMarketRecord from canonical reader.
        legacy_records: list of ParsedMarketRecord from legacy reader, or None.
        period: YYYY-MM, copied into the record.
        request_hash: SHA-256 hex (64 chars). Caller is responsible for shape.
        customer_id: optional, copied into the record.

    Returns:
        DriftRecord ready for write_drift_record(), or None if canonical
        is unusable (the caller raises 404 anyway, so no row is needed).

    Severity classification:
        - canonical missing/empty           → return None (no record)
        - legacy missing/empty/unreadable   → severity='missing_legacy',
                                              delta_abs/pct = None,
                                              legacy_price = None
        - both present, equal               → severity='low',
                                              delta_abs = 0.0, delta_pct = 0.0
        - both present, |drift| <= 0.5%     → severity='low'
        - both present, |drift| > 0.5%      → severity='high'

    Symmetric drift formula:
        baseline = max(|canonical_avg|, |legacy_avg|)
        delta_pct = (|delta_abs| / baseline) * 100  if baseline > 0
                  = 0.0                              if both averages are 0
                  = None                             on division anomaly

    Both delta_abs and delta_pct are rounded to 6 decimals to suppress
    float jitter in telemetry and tests.

    NEVER raises.
    """
    canonical_avg = _weighted_avg_ptf(canonical_records)
    if canonical_avg is None:
        return None  # caller raises 404; nothing useful to log

    canonical_price = round(float(canonical_avg), _DRIFT_ROUND_NDIGITS)

    legacy_avg = _weighted_avg_ptf(legacy_records)
    if legacy_avg is None:
        return DriftRecord(
            period=period,
            canonical_price=canonical_price,
            legacy_price=None,
            delta_abs=None,
            delta_pct=None,
            severity="missing_legacy",
            request_hash=request_hash,
            customer_id=customer_id,
        )

    try:
        legacy_price = round(float(legacy_avg), _DRIFT_ROUND_NDIGITS)
        delta_abs_raw = canonical_price - legacy_price
        delta_abs = round(delta_abs_raw, _DRIFT_ROUND_NDIGITS)

        # Symmetric baseline — neutral between canonical and legacy.
        # Both-zero case is "no drift" (severity=low, deltas=0), not None.
        baseline = max(abs(canonical_price), abs(legacy_price))
        if baseline == 0.0:
            # canonical_avg == 0 AND legacy_avg == 0 → identical empty market
            delta_pct = 0.0
        else:
            delta_pct = round(
                (abs(delta_abs) / baseline) * 100.0,
                _DRIFT_ROUND_NDIGITS,
            )

        severity = "low" if abs(delta_pct) <= DRIFT_HIGH_PCT else "high"
    except (TypeError, ValueError, ArithmeticError) as exc:
        # Defensive: any numeric anomaly → record a missing_legacy row so
        # the operational signal is not lost, and log the cause.
        logger.warning(
            "[PTF-DRIFT] compute_drift numeric anomaly (degraded to missing_legacy) "
            "period=%s err=%s",
            period, exc,
        )
        return DriftRecord(
            period=period,
            canonical_price=canonical_price,
            legacy_price=None,
            delta_abs=None,
            delta_pct=None,
            severity="missing_legacy",
            request_hash=request_hash,
            customer_id=customer_id,
        )

    return DriftRecord(
        period=period,
        canonical_price=canonical_price,
        legacy_price=legacy_price,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        severity=severity,
        request_hash=request_hash,
        customer_id=customer_id,
    )


def record_drift(
    db_session,
    canonical_records,
    legacy_records,
    *,
    period: str,
    request_hash: str,
    customer_id: int | None = None,
) -> bool:
    """Compute drift and persist it. Best-effort, fail-open.

    Returns True if a row was committed, False if compute returned None
    (canonical unusable) or write_drift_record failed. NEVER raises.
    """
    try:
        record = compute_drift(
            canonical_records, legacy_records,
            period=period,
            request_hash=request_hash,
            customer_id=customer_id,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        logger.warning(
            "[PTF-DRIFT] record_drift compute step raised (suppressed) "
            "period=%s err=%s",
            period, exc,
        )
        return False

    if record is None:
        return False

    return write_drift_record(db_session, record)
