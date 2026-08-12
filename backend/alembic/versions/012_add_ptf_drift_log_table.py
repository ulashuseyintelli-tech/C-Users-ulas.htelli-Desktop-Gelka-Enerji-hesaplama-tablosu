"""
Create ptf_drift_log table (PTF SoT Unification — Phase 1 T1.3)

Revision ID: 012_add_ptf_drift_log_table
Revises: 011_market_prices_ptf_admin
Create Date: 2026-05-12

Purpose
-------
Persistence for canonical↔legacy PTF drift observations captured during the
Phase 2 dual-read window (see spec: ptf-sot-unification). The writer and
compare logic (`compute_drift`, `record_drift`) land in T2.2; this migration
only materializes the storage so model imports don't break and so ops can
verify the DDL in isolation before any write path exists.

Schema (design §3.1 / backend/app/ptf_drift_log.py)
---------------------------------------------------
- id              INTEGER PK AUTOINCREMENT
- created_at      DATETIME NOT NULL, default CURRENT_TIMESTAMP
- period          VARCHAR(7) NOT NULL  (YYYY-MM)
- canonical_price FLOAT NOT NULL       (TL/MWh)
- legacy_price    FLOAT NULL           (nullable: legacy read may fail)
- delta_abs       FLOAT NULL
- delta_pct       FLOAT NULL
- severity        VARCHAR(10) NOT NULL ('low' | 'high') — CHECK constraint
- request_hash    VARCHAR(64) NOT NULL (sha256 hex) — CHECK length = 64
- customer_id     INTEGER NULL         (nullable — not all requests customer-scoped)

Indexes
-------
- ix_ptf_drift_log_created_at    — retention cleanup / time-series queries
- ix_ptf_drift_log_period        — per-period aggregation
- ix_ptf_drift_log_request_hash  — duplicate-drift dedupe lookup

Retention target (NOT enforced here): 30 days. Final policy is decided in T4.6.

Idempotency
-----------
upgrade() checks `table_exists` before CREATE and `index_exists` before
CREATE INDEX so reruns (e.g., after a downgrade partial failure) don't explode.
downgrade() is symmetric: indexes first, then table, all conditional.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = '012_add_ptf_drift_log_table'
down_revision = '011_market_prices_ptf_admin'
branch_labels = None
depends_on = None


def table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    if not table_exists(table_name):
        return False
    bind = op.get_bind()
    inspector = inspect(bind)
    return index_name in {idx['name'] for idx in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not table_exists('ptf_drift_log'):
        op.create_table(
            'ptf_drift_log',
            sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text('CURRENT_TIMESTAMP'),
            ),
            sa.Column('period', sa.String(length=7), nullable=False),
            sa.Column('canonical_price', sa.Float(), nullable=False),
            sa.Column('legacy_price', sa.Float(), nullable=True),
            sa.Column('delta_abs', sa.Float(), nullable=True),
            sa.Column('delta_pct', sa.Float(), nullable=True),
            sa.Column('severity', sa.String(length=10), nullable=False),
            sa.Column('request_hash', sa.String(length=64), nullable=False),
            sa.Column('customer_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.CheckConstraint(
                "severity IN ('low', 'high')",
                name='ck_ptf_drift_log_severity',
            ),
            sa.CheckConstraint(
                "length(request_hash) = 64",
                name='ck_ptf_drift_log_request_hash_len',
            ),
        )

    if not index_exists('ptf_drift_log', 'ix_ptf_drift_log_created_at'):
        op.create_index(
            'ix_ptf_drift_log_created_at',
            'ptf_drift_log',
            ['created_at'],
            unique=False,
        )
    if not index_exists('ptf_drift_log', 'ix_ptf_drift_log_period'):
        op.create_index(
            'ix_ptf_drift_log_period',
            'ptf_drift_log',
            ['period'],
            unique=False,
        )
    if not index_exists('ptf_drift_log', 'ix_ptf_drift_log_request_hash'):
        op.create_index(
            'ix_ptf_drift_log_request_hash',
            'ptf_drift_log',
            ['request_hash'],
            unique=False,
        )


def downgrade() -> None:
    # Drop indexes first (SQLite tolerates index drops before table drop, but
    # other backends are happier this way).
    if index_exists('ptf_drift_log', 'ix_ptf_drift_log_request_hash'):
        op.drop_index('ix_ptf_drift_log_request_hash', table_name='ptf_drift_log')
    if index_exists('ptf_drift_log', 'ix_ptf_drift_log_period'):
        op.drop_index('ix_ptf_drift_log_period', table_name='ptf_drift_log')
    if index_exists('ptf_drift_log', 'ix_ptf_drift_log_created_at'):
        op.drop_index('ix_ptf_drift_log_created_at', table_name='ptf_drift_log')
    if table_exists('ptf_drift_log'):
        op.drop_table('ptf_drift_log')
