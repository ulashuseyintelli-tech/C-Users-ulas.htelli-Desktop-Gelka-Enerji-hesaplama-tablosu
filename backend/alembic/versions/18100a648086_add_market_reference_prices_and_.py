"""Add market_reference_prices and distribution_tariffs tables

Revision ID: 18100a648086
Revises: 003
Create Date: 2026-01-16 22:20:16.136391

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18100a648086'
down_revision: Union[str, None] = '003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Market Reference Prices tablosu (PTF/YEKDEM)
    op.create_table(
        'market_reference_prices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('ptf_tl_per_mwh', sa.Float(), nullable=False),
        sa.Column('yekdem_tl_per_mwh', sa.Float(), nullable=False),
        sa.Column('source_note', sa.String(length=500), nullable=True),
        sa.Column('is_locked', sa.Integer(), nullable=True, default=0),
        sa.Column('updated_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_market_reference_prices_id'), 'market_reference_prices', ['id'], unique=False)
    op.create_index(op.f('ix_market_reference_prices_period'), 'market_reference_prices', ['period'], unique=True)
    
    # Distribution Tariffs tablosu (EPDK)
    op.create_table(
        'distribution_tariffs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('valid_from', sa.String(length=10), nullable=False),
        sa.Column('valid_to', sa.String(length=10), nullable=True),
        sa.Column('tariff_group', sa.String(length=20), nullable=False),
        sa.Column('voltage_level', sa.String(length=5), nullable=False),
        sa.Column('term_type', sa.String(length=20), nullable=False),
        sa.Column('unit_price_tl_per_kwh', sa.Float(), nullable=False),
        sa.Column('source_note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_distribution_tariffs_id'), 'distribution_tariffs', ['id'], unique=False)
    op.create_index(op.f('ix_distribution_tariffs_valid_from'), 'distribution_tariffs', ['valid_from'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_distribution_tariffs_valid_from'), table_name='distribution_tariffs')
    op.drop_index(op.f('ix_distribution_tariffs_id'), table_name='distribution_tariffs')
    op.drop_table('distribution_tariffs')
    
    op.drop_index(op.f('ix_market_reference_prices_period'), table_name='market_reference_prices')
    op.drop_index(op.f('ix_market_reference_prices_id'), table_name='market_reference_prices')
    op.drop_table('market_reference_prices')
