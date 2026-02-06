"""Retry Executor - Sprint 7.0

Revision ID: 005
Revises: 004
Create Date: 2026-01-17

Adds retry execution columns to incidents table:
- retry_attempt_count: Number of retry attempts
- retry_eligible_at: When the incident becomes eligible for retry
- retry_last_attempt_at: When the last retry attempt was made
- retry_lock_until: Lock expiry for concurrent execution safety
- retry_lock_by: Worker ID that holds the lock
- retry_exhausted_at: When max retries were exhausted
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '005_retry_executor'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add retry execution columns
    op.add_column('incidents', sa.Column('retry_attempt_count', sa.Integer(), nullable=True, default=0))
    op.add_column('incidents', sa.Column('retry_eligible_at', sa.DateTime(), nullable=True))
    op.add_column('incidents', sa.Column('retry_last_attempt_at', sa.DateTime(), nullable=True))
    op.add_column('incidents', sa.Column('retry_lock_until', sa.DateTime(), nullable=True))
    op.add_column('incidents', sa.Column('retry_lock_by', sa.String(length=100), nullable=True))
    op.add_column('incidents', sa.Column('retry_exhausted_at', sa.DateTime(), nullable=True))
    
    # Index for efficient PENDING_RETRY queries
    op.create_index('ix_incidents_retry_eligible_at', 'incidents', ['retry_eligible_at'], unique=False)
    
    # Set default value for existing rows
    op.execute("UPDATE incidents SET retry_attempt_count = 0 WHERE retry_attempt_count IS NULL")


def downgrade() -> None:
    op.drop_index('ix_incidents_retry_eligible_at', table_name='incidents')
    op.drop_column('incidents', 'retry_exhausted_at')
    op.drop_column('incidents', 'retry_lock_by')
    op.drop_column('incidents', 'retry_lock_until')
    op.drop_column('incidents', 'retry_last_attempt_at')
    op.drop_column('incidents', 'retry_eligible_at')
    op.drop_column('incidents', 'retry_attempt_count')
