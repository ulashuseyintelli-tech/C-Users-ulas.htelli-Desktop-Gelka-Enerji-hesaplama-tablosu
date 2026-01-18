"""Add audit logs and webhook tables

Revision ID: 003
Revises: 002
Create Date: 2026-01-08

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Audit logs table
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, index=True),
        sa.Column('actor_type', sa.String(50), nullable=False, server_default='system'),
        sa.Column('actor_id', sa.String(100), nullable=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('target_type', sa.String(50), nullable=True),
        sa.Column('target_id', sa.String(100), nullable=True),
        sa.Column('details_json', sa.JSON(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_tenant_action', 'audit_logs', ['tenant_id', 'action'])
    op.create_index('ix_audit_logs_target', 'audit_logs', ['target_type', 'target_id'])
    
    # Webhook configs table
    op.create_table(
        'webhook_configs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=False, index=True),
        sa.Column('url', sa.String(2000), nullable=False),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('secret', sa.String(255), nullable=True),
        sa.Column('headers_json', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=True, server_default='1'),
        sa.Column('last_triggered_at', sa.DateTime(), nullable=True),
        sa.Column('success_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('failure_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Webhook deliveries table
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('webhook_config_id', sa.Integer(), nullable=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('payload_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('response_status_code', sa.Integer(), nullable=True),
        sa.Column('response_body', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('next_retry_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['webhook_config_id'], ['webhook_configs.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_webhook_deliveries_config', 'webhook_deliveries', ['webhook_config_id'])
    op.create_index('ix_webhook_deliveries_status', 'webhook_deliveries', ['status'])


def downgrade() -> None:
    op.drop_table('webhook_deliveries')
    op.drop_table('webhook_configs')
    op.drop_table('audit_logs')
