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
        # severity is constrained to the two values the Phase 2 dashboard expects;
        # any third value means "pipeline bug" and should fail CHECK rather than
        # pollute the series.
        CheckConstraint(
            "severity IN ('low', 'high')", name="ck_ptf_drift_log_severity"
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
