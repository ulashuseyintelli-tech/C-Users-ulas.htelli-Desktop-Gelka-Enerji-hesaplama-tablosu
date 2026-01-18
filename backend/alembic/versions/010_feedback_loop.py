"""
Sprint 8.7: Feedback Loop - Add feedback_json column to incidents table.

Revision ID: 010_feedback_loop
Revises: 009_resolution_reasons
Create Date: 2026-01-17

Feedback Schema:
{
    "action_taken": "VERIFIED_OCR" | "VERIFIED_LOGIC" | "ACCEPTED_ROUNDING" | "ESCALATED" | "NO_ACTION_REQUIRED",
    "was_hint_correct": true | false,
    "actual_root_cause": "optional string (max 200 char)",
    "resolution_time_seconds": 120,
    "feedback_at": "2025-01-17T15:00:00Z",
    "feedback_by": "user_id"
}

Rules:
- Feedback is OPTIONAL (nullable)
- Only RESOLVED incidents can have feedback (state guard in service layer)
- UPSERT semantics: each submission overwrites previous feedback
- feedback_at is server-time (not client-provided)
- feedback_by is required (from auth context)
- No backfill: existing records remain null
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '010_feedback_loop'
down_revision = '009_resolution_reasons'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add feedback_json column to incidents table."""
    # Add feedback_json column (JSONB for PostgreSQL, JSON for SQLite)
    # Nullable - feedback is optional
    op.add_column(
        'incidents',
        sa.Column('feedback_json', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Remove feedback_json column from incidents table."""
    op.drop_column('incidents', 'feedback_json')
