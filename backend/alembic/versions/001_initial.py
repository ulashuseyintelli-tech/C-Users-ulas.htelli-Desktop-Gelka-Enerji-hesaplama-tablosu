"""Initial migration - all tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-01-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ═══════════════════════════════════════════════════════════════════════════
    # Invoices Table
    # ═══════════════════════════════════════════════════════════════════════════
    op.create_table(
        'invoices',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_filename', sa.String(255), nullable=False),
        sa.Column('content_type', sa.String(100), nullable=False),
        sa.Column('storage_original_ref', sa.String(700), nullable=False),
        sa.Column('storage_page1_ref', sa.String(700), nullable=True),
        sa.Column('file_hash', sa.String(64), nullable=True, index=True),
        sa.Column('vendor_guess', sa.String(50), nullable=True),
        sa.Column('invoice_period', sa.String(10), nullable=True),
        sa.Column('extraction_json', sa.JSON, nullable=True),
        sa.Column('validation_json', sa.JSON, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='UPLOADED'),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Customers Table
    # ═══════════════════════════════════════════════════════════════════════════
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False, index=True),
        sa.Column('company', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('address', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Offers Table
    # ═══════════════════════════════════════════════════════════════════════════
    op.create_table(
        'offers',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('customer_id', sa.Integer, sa.ForeignKey('customers.id'), nullable=True),
        sa.Column('vendor', sa.String(50), nullable=True),
        sa.Column('invoice_period', sa.String(10), nullable=True),
        sa.Column('consumption_kwh', sa.Float, nullable=False),
        sa.Column('current_unit_price', sa.Float, nullable=False),
        sa.Column('distribution_unit_price', sa.Float, nullable=True),
        sa.Column('demand_qty', sa.Float, nullable=True),
        sa.Column('demand_unit_price', sa.Float, nullable=True),
        sa.Column('weighted_ptf', sa.Float, nullable=False),
        sa.Column('yekdem', sa.Float, nullable=False),
        sa.Column('agreement_multiplier', sa.Float, nullable=False),
        sa.Column('current_total', sa.Float, nullable=False),
        sa.Column('offer_total', sa.Float, nullable=False),
        sa.Column('savings_amount', sa.Float, nullable=False),
        sa.Column('savings_ratio', sa.Float, nullable=False),
        sa.Column('calculation_result', sa.JSON, nullable=True),
        sa.Column('extraction_result', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('pdf_ref', sa.String(700), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='draft'),
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Jobs Table
    # ═══════════════════════════════════════════════════════════════════════════
    op.create_table(
        'jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('invoice_id', sa.String(36), sa.ForeignKey('invoices.id'), nullable=False, index=True),
        sa.Column('job_type', sa.String(30), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='QUEUED'),
        sa.Column('payload_json', sa.JSON, nullable=True),
        sa.Column('result_json', sa.JSON, nullable=True),
        sa.Column('error', sa.String(2000), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('finished_at', sa.DateTime, nullable=True),
    )

    # ═══════════════════════════════════════════════════════════════════════════
    # Indexes for Job Queue Performance
    # ═══════════════════════════════════════════════════════════════════════════
    op.create_index('ix_jobs_status_created', 'jobs', ['status', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_jobs_status_created', 'jobs')
    op.drop_table('jobs')
    op.drop_table('offers')
    op.drop_table('customers')
    op.drop_table('invoices')
