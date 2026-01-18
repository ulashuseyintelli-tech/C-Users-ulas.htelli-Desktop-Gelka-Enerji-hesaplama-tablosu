"""
Sprint 8.0 - Retry Orchestrator Support

Tek otorite RESOLVED için gerekli alanlar.

Revision ID: 008_retry_orchestrator
Revises: 007_reclassification
Create Date: 2025-01-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '008_retry_orchestrator'
down_revision = '007_reclassification'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Retry orchestrator alanları ekle.
    
    Yeni alanlar:
    - retry_success: Retry lookup başarılı mı (RESOLVED kararı değil!)
    
    Yeni status değeri:
    - PENDING_RECOMPUTE: Retry success, recompute bekliyor
    
    NOT: status zaten VARCHAR, enum değil. Yeni değer için migration gerekmez.
    """
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        # Retry success flag (lookup başarılı mı)
        batch_op.add_column(
            sa.Column('retry_success', sa.Boolean(), nullable=True)
        )
        
        # Index for PENDING_RECOMPUTE status (stuck detection)
        batch_op.create_index(
            'ix_incidents_pending_recompute',
            ['tenant_id', 'status', 'updated_at'],
            unique=False
        )


def downgrade() -> None:
    """Rollback: Retry orchestrator alanlarını kaldır."""
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index('ix_incidents_pending_recompute')
        batch_op.drop_column('retry_success')
