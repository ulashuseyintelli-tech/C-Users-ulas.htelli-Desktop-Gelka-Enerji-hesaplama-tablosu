"""
PTF Admin Management: Extend market_reference_prices table

Revision ID: 011_market_prices_ptf_admin
Revises: 010_feedback_loop
Create Date: 2026-02-07

New columns:
- price_type: VARCHAR(20), default "PTF", for future SMF/YEKDEM support
- status: VARCHAR(20), "provisional" | "final"
- captured_at: DATETIME, when data was retrieved from EPİAŞ
- change_reason: TEXT, optional audit field
- source: VARCHAR(30), epias_manual | epias_api | migration | seed

Schema changes:
- Drop unique index on period (will be replaced with composite)
- Add unique constraint on (price_type, period)

Backfill strategy (Task 1.2):
- Existing records: status="final", source="migration", captured_at=updated_at
- price_type="PTF" for all existing records
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from datetime import datetime


# revision identifiers, used by Alembic.
revision = '011_market_prices_ptf_admin'
down_revision = '010_feedback_loop'
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name, index_name):
    """Check if an index exists on a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """Add new columns and constraints to market_reference_prices using batch mode for SQLite."""
    
    # Check which columns already exist
    has_price_type = column_exists('market_reference_prices', 'price_type')
    has_status = column_exists('market_reference_prices', 'status')
    has_captured_at = column_exists('market_reference_prices', 'captured_at')
    has_change_reason = column_exists('market_reference_prices', 'change_reason')
    has_source = column_exists('market_reference_prices', 'source')
    
    # Check which indexes exist
    has_old_period_index = index_exists('market_reference_prices', 'ix_market_reference_prices_period')
    has_new_composite_index = index_exists('market_reference_prices', 'ix_market_reference_prices_price_type_period')
    has_status_index = index_exists('market_reference_prices', 'ix_market_reference_prices_status')
    
    # 1. Add missing columns
    if not has_price_type:
        op.add_column('market_reference_prices',
            sa.Column('price_type', sa.String(length=20), nullable=False, server_default='PTF')
        )
    
    if not has_status:
        op.add_column('market_reference_prices',
            sa.Column('status', sa.String(length=20), nullable=False, server_default='final')
        )
    
    if not has_captured_at:
        op.add_column('market_reference_prices',
            sa.Column('captured_at', sa.DateTime(), nullable=True)
        )
    
    if not has_change_reason:
        op.add_column('market_reference_prices',
            sa.Column('change_reason', sa.Text(), nullable=True)
        )
    
    if not has_source:
        op.add_column('market_reference_prices',
            sa.Column('source', sa.String(length=30), nullable=False, server_default='migration')
        )
    
    # 2. Backfill captured_at from updated_at for existing records
    op.execute("""
        UPDATE market_reference_prices 
        SET captured_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        WHERE captured_at IS NULL
    """)
    
    # 2b. Set updated_by for existing records without it
    op.execute("""
        UPDATE market_reference_prices 
        SET updated_by = 'system_migration'
        WHERE updated_by IS NULL
    """)
    
    # 3. Drop old unique index on period if it exists
    if has_old_period_index:
        op.drop_index('ix_market_reference_prices_period', table_name='market_reference_prices')
    
    # 4. Create new indexes if they don't exist
    if not has_new_composite_index:
        op.create_index(
            'ix_market_reference_prices_price_type_period',
            'market_reference_prices',
            ['price_type', 'period'],
            unique=True  # This serves as the unique constraint
        )
    
    if not has_status_index:
        op.create_index(
            'ix_market_reference_prices_status',
            'market_reference_prices',
            ['status'],
            unique=False
        )


def downgrade() -> None:
    """Revert changes to market_reference_prices."""
    
    # Check which indexes exist
    has_new_composite_index = index_exists('market_reference_prices', 'ix_market_reference_prices_price_type_period')
    has_status_index = index_exists('market_reference_prices', 'ix_market_reference_prices_status')
    has_old_period_index = index_exists('market_reference_prices', 'ix_market_reference_prices_period')
    
    # Drop new indexes
    if has_status_index:
        op.drop_index('ix_market_reference_prices_status', table_name='market_reference_prices')
    
    if has_new_composite_index:
        op.drop_index('ix_market_reference_prices_price_type_period', table_name='market_reference_prices')
    
    # Recreate old unique index on period
    if not has_old_period_index:
        op.create_index(
            'ix_market_reference_prices_period',
            'market_reference_prices',
            ['period'],
            unique=True
        )
    
    # Drop new columns using batch mode for SQLite
    with op.batch_alter_table('market_reference_prices', schema=None) as batch_op:
        if column_exists('market_reference_prices', 'source'):
            batch_op.drop_column('source')
        if column_exists('market_reference_prices', 'change_reason'):
            batch_op.drop_column('change_reason')
        if column_exists('market_reference_prices', 'captured_at'):
            batch_op.drop_column('captured_at')
        if column_exists('market_reference_prices', 'status'):
            batch_op.drop_column('status')
        if column_exists('market_reference_prices', 'price_type'):
            batch_op.drop_column('price_type')
