"""
Sprint 7.1 - Issue Integration

Incident tablosuna external issue tracking alanları ekler.

Revision ID: 006_issue_integration
Revises: 005_retry_executor
Create Date: 2025-01-17
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '006_issue_integration'
down_revision = '005_retry_executor'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Issue integration alanları ekle.
    
    Yeni alanlar:
    - external_issue_id: GitHub/Jira issue ID
    - external_issue_url: Issue URL
    - reported_at: Issue oluşturulma zamanı
    """
    # SQLite için batch mode
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        # External issue tracking
        batch_op.add_column(
            sa.Column('external_issue_id', sa.String(100), nullable=True)
        )
        batch_op.add_column(
            sa.Column('external_issue_url', sa.String(500), nullable=True)
        )
        batch_op.add_column(
            sa.Column('reported_at', sa.DateTime(), nullable=True)
        )
        
        # Index for finding unreported bugs
        batch_op.create_index(
            'ix_incidents_unreported_bugs',
            ['status', 'action_type', 'external_issue_id'],
            unique=False
        )


def downgrade() -> None:
    """Rollback: Issue integration alanlarını kaldır."""
    with op.batch_alter_table('incidents', schema=None) as batch_op:
        batch_op.drop_index('ix_incidents_unreported_bugs')
        batch_op.drop_column('reported_at')
        batch_op.drop_column('external_issue_url')
        batch_op.drop_column('external_issue_id')
