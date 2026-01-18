"""Add tenant_id and extra_items columns

Revision ID: 002
Revises: 001
Create Date: 2026-01-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tenant_id to invoices
    op.add_column('invoices', sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'))
    op.create_index('ix_invoices_tenant_id', 'invoices', ['tenant_id'])
    
    # Add tenant_id to offers
    op.add_column('offers', sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'))
    op.create_index('ix_offers_tenant_id', 'offers', ['tenant_id'])
    
    # Add extra_items columns to offers
    op.add_column('offers', sa.Column('extra_items_json', sa.JSON(), nullable=True))
    op.add_column('offers', sa.Column('extra_items_total_tl', sa.Float(), nullable=True, server_default='0'))
    
    # Add tenant_id to jobs
    op.add_column('jobs', sa.Column('tenant_id', sa.String(64), nullable=False, server_default='default'))
    op.create_index('ix_jobs_tenant_id', 'jobs', ['tenant_id'])


def downgrade() -> None:
    # Remove from jobs
    op.drop_index('ix_jobs_tenant_id', 'jobs')
    op.drop_column('jobs', 'tenant_id')
    
    # Remove from offers
    op.drop_column('offers', 'extra_items_total_tl')
    op.drop_column('offers', 'extra_items_json')
    op.drop_index('ix_offers_tenant_id', 'offers')
    op.drop_column('offers', 'tenant_id')
    
    # Remove from invoices
    op.drop_index('ix_invoices_tenant_id', 'invoices')
    op.drop_column('invoices', 'tenant_id')
