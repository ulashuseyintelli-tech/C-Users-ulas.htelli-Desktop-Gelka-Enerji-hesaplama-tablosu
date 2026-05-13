"""
Extend ptf_drift_log severity CHECK to include 'missing_legacy'
(PTF SoT Unification — Phase 2 T2.2)

Revision ID: 013_extend_ptf_drift_severity
Revises: 012_add_ptf_drift_log_table
Create Date: 2026-05-13

Purpose
-------
Phase 2 T2.2 introduces a third severity value, `missing_legacy`. This is NOT
a drift severity in the same sense as 'low'/'high' — it is an operational
state signal: canonical was read but legacy was unavailable / empty for the
period. We log it explicitly (not via legacy_price IS NULL hack) so that
Phase 3 readiness queries can group on `severity` directly.

Schema change
-------------
Drop and recreate `ck_ptf_drift_log_severity`:
    BEFORE:  severity IN ('low', 'high')
    AFTER:   severity IN ('low', 'high', 'missing_legacy')

Downgrade safety
----------------
A naive downgrade that just recreates the old CHECK would fail if any rows
already exist with severity='missing_legacy'. We DELETE those rows first,
explicitly. Phase 4 drops the entire ptf_drift_log table anyway, so losing
missing_legacy rows on downgrade is acceptable; the loud DELETE makes the
data loss visible rather than silent.

Idempotency
-----------
SQLite (used in tests) doesn't support ALTER CONSTRAINT directly, so we use
op.batch_alter_table which recreates the table under the hood. This works
on both SQLite and PostgreSQL.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '013_extend_ptf_drift_severity'
down_revision = '012_add_ptf_drift_log_table'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Replace severity CHECK constraint with the 3-value version."""
    with op.batch_alter_table('ptf_drift_log') as batch_op:
        batch_op.drop_constraint(
            'ck_ptf_drift_log_severity', type_='check',
        )
        batch_op.create_check_constraint(
            'ck_ptf_drift_log_severity',
            "severity IN ('low', 'high', 'missing_legacy')",
        )


def downgrade() -> None:
    """Restore the 2-value CHECK; explicit DELETE for missing_legacy rows.

    This is a destructive downgrade by design. Sessile alternative would be
    to leave missing_legacy rows in the table and break the new CHECK, which
    is silently broken (CHECK violations only fire on INSERT/UPDATE; existing
    rows are tolerated until touched). We prefer loud data loss to silent
    inconsistency. Phase 4 drops the table outright, so missing_legacy rows
    have no long-term value.
    """
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM ptf_drift_log WHERE severity = 'missing_legacy'"
    ))
    with op.batch_alter_table('ptf_drift_log') as batch_op:
        batch_op.drop_constraint(
            'ck_ptf_drift_log_severity', type_='check',
        )
        batch_op.create_check_constraint(
            'ck_ptf_drift_log_severity',
            "severity IN ('low', 'high')",
        )
