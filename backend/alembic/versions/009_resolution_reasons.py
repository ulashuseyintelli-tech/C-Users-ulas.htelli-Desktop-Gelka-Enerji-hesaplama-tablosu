"""
Sprint 8.1 - Resolution Reasons

Resolution reason enum ve resolved_at timestamp.

Revision ID: 009_resolution_reasons
Revises: 008_retry_orchestrator
Create Date: 2025-01-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '009_resolution_reasons'
down_revision = '008_retry_orchestrator'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Resolution reason alanları ekle.
    
    Yeni alanlar:
    - resolution_reason: Enum string (ResolutionReason değerleri)
    
    NOT: resolved_at zaten var (Sprint 6.1'de eklendi)
    """
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        # Resolution reason (enum string)
        batch_op.add_column(
            sa.Column('resolution_reason', sa.String(50), nullable=True)
        )
        
        # Index for KPI queries
        batch_op.create_index(
            'ix_incidents_resolution_reason',
            ['tenant_id', 'resolution_reason'],
            unique=False
        )


def downgrade() -> None:
    """Rollback: Resolution reason alanlarını kaldır."""
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index('ix_incidents_resolution_reason')
        batch_op.drop_column('resolution_reason')
