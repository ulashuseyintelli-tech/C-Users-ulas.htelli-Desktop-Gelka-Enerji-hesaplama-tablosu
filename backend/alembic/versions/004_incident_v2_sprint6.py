"""Incident v2 - Sprint 6.1 Action Router Integration

Revision ID: 004
Revises: 18100a648086
Create Date: 2026-01-17

Adds:
- provider, period columns
- dedupe_bucket for 24h TTL
- primary_flag, action_type, action_owner, action_code
- all_flags, secondary_flags (JSON)
- deduction_total
- routed_payload (JSON) for UI alert / retry schedule / issue payload
- Unique constraint on (tenant_id, dedupe_key, dedupe_bucket)
- New indexes for common queries
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '004'
down_revision: Union[str, None] = '18100a648086'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to incidents table
    op.add_column('incidents', sa.Column('provider', sa.String(length=100), nullable=True))
    op.add_column('incidents', sa.Column('period', sa.String(length=7), nullable=True))
    op.add_column('incidents', sa.Column('dedupe_bucket', sa.Integer(), nullable=True))
    op.add_column('incidents', sa.Column('primary_flag', sa.String(length=50), nullable=True))
    op.add_column('incidents', sa.Column('action_type', sa.String(length=30), nullable=True))
    op.add_column('incidents', sa.Column('action_owner', sa.String(length=30), nullable=True))
    op.add_column('incidents', sa.Column('action_code', sa.String(length=50), nullable=True))
    op.add_column('incidents', sa.Column('all_flags', sa.JSON(), nullable=True))
    op.add_column('incidents', sa.Column('secondary_flags', sa.JSON(), nullable=True))
    op.add_column('incidents', sa.Column('deduction_total', sa.Integer(), nullable=True, default=0))
    op.add_column('incidents', sa.Column('routed_payload', sa.JSON(), nullable=True))
    
    # Create indexes for common queries
    op.create_index('ix_incidents_provider', 'incidents', ['provider'], unique=False)
    op.create_index('ix_incidents_period', 'incidents', ['period'], unique=False)
    op.create_index('ix_incidents_primary_flag', 'incidents', ['primary_flag'], unique=False)
    op.create_index('ix_incidents_action_type', 'incidents', ['action_type'], unique=False)
    op.create_index('ix_incidents_dedupe_bucket', 'incidents', ['dedupe_bucket'], unique=False)
    
    # Create unique constraint for dedupe
    # Note: SQLite doesn't support adding unique constraints after table creation
    # For SQLite, we create a unique index instead
    op.create_index(
        'ix_incidents_dedupe_unique',
        'incidents',
        ['tenant_id', 'dedupe_key', 'dedupe_bucket'],
        unique=True
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_incidents_dedupe_unique', table_name='incidents')
    op.drop_index('ix_incidents_dedupe_bucket', table_name='incidents')
    op.drop_index('ix_incidents_action_type', table_name='incidents')
    op.drop_index('ix_incidents_primary_flag', table_name='incidents')
    op.drop_index('ix_incidents_period', table_name='incidents')
    op.drop_index('ix_incidents_provider', table_name='incidents')
    
    # Drop columns
    op.drop_column('incidents', 'routed_payload')
    op.drop_column('incidents', 'deduction_total')
    op.drop_column('incidents', 'secondary_flags')
    op.drop_column('incidents', 'all_flags')
    op.drop_column('incidents', 'action_code')
    op.drop_column('incidents', 'action_owner')
    op.drop_column('incidents', 'action_type')
    op.drop_column('incidents', 'primary_flag')
    op.drop_column('incidents', 'dedupe_bucket')
    op.drop_column('incidents', 'period')
    op.drop_column('incidents', 'provider')
