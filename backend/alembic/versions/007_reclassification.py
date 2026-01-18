"""
Sprint 7.1.2 - Reclassification Support

Retry sonrası primary flag değişikliği takibi.

Revision ID: 007_reclassification
Revises: 006_issue_integration
Create Date: 2025-01-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '007_reclassification'
down_revision = '006_issue_integration'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Reclassification alanları ekle.
    
    Yeni alanlar:
    - reclassified_at: Primary flag değişiklik zamanı
    - previous_primary_flag: Önceki primary flag
    - recompute_count: Kaç kez recompute yapıldı
    """
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('reclassified_at', sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('previous_primary_flag', sa.String(50), nullable=True)
        )
        batch_op.add_column(
            sa.Column('recompute_count', sa.Integer(), nullable=True, default=0)
        )


def downgrade() -> None:
    """Rollback: Reclassification alanlarını kaldır."""
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_column('recompute_count')
        batch_op.drop_column('previous_primary_flag')
        batch_op.drop_column('reclassified_at')
