"""add outreach tables s5

Revision ID: f4e7efc70c80
Revises: e340ce40c05c
Create Date: 2026-08-10 18:45:27.387819

NOT (S5 — Outreach, owner kararı): S2/S4'teki aynı gerekçeyle ELLE
yazıldı (`alembic revision --autogenerate` KULLANILMADI). Hash-ID
isimlendirme kullanıldı (sıralı numara DEĞİL) — unmerged
`ptf-sot-unification-*` branch ailesi 012/013 numaralarını claim etmiş.

Şema kaynağı: app/database.py OutreachMessage/SuppressionEntry/
OutreachTemplate sınıfları (bkz. oradaki docstring'ler — immutable send
snapshot, TEST_RECIPIENT/PROSPECT_RECIPIENT ayrımı, suppression hard-gate
gerekçesi).

Additive migration — S1-S4'ün hiçbir tablosuna dokunmaz (down_revision
zinciri yalnız e340ce40c05c → ... üzerinden ilerler).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4e7efc70c80'
down_revision: Union[str, None] = 'e340ce40c05c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('outreach_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('prospect_company_id', sa.Integer(), nullable=True),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('contact_id', sa.Integer(), nullable=True),
    sa.Column('recipient_email_snapshot', sa.String(length=255), nullable=False),
    sa.Column('recipient_legal_type', sa.String(length=20), nullable=True),
    sa.Column('recipient_category', sa.String(length=30), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=500), nullable=False),
    sa.Column('body_snapshot', sa.Text(), nullable=False),
    sa.Column('system_footer_snapshot', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('provider', sa.String(length=30), nullable=True),
    sa.Column('provider_message_id', sa.String(length=255), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('replied_at', sa.DateTime(), nullable=True),
    sa.Column('bounced_at', sa.DateTime(), nullable=True),
    sa.Column('failed_at', sa.DateTime(), nullable=True),
    sa.Column('failure_code', sa.String(length=100), nullable=True),
    sa.Column('source_snapshot_json', sa.JSON(), nullable=True),
    sa.Column('compliance_snapshot_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['prospect_company_id'], ['prospect_companies.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['contact_id'], ['prospect_contacts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outreach_messages_id'), 'outreach_messages', ['id'], unique=False)
    op.create_index(op.f('ix_outreach_messages_tenant_id'), 'outreach_messages', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_outreach_messages_prospect_company_id'), 'outreach_messages', ['prospect_company_id'], unique=False)
    op.create_index(op.f('ix_outreach_messages_customer_id'), 'outreach_messages', ['customer_id'], unique=False)
    op.create_index(op.f('ix_outreach_messages_contact_id'), 'outreach_messages', ['contact_id'], unique=False)
    op.create_index(op.f('ix_outreach_messages_recipient_email_snapshot'), 'outreach_messages', ['recipient_email_snapshot'], unique=False)
    op.create_index(op.f('ix_outreach_messages_status'), 'outreach_messages', ['status'], unique=False)
    op.create_index(op.f('ix_outreach_messages_provider_message_id'), 'outreach_messages', ['provider_message_id'], unique=False)

    op.create_table('suppression_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('email_normalized', sa.String(length=255), nullable=False),
    sa.Column('reason', sa.String(length=30), nullable=False),
    sa.Column('source', sa.String(length=255), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('effective_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_suppression_entries_id'), 'suppression_entries', ['id'], unique=False)
    op.create_index(op.f('ix_suppression_entries_tenant_id'), 'suppression_entries', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_suppression_entries_email_normalized'), 'suppression_entries', ['email_normalized'], unique=False)

    op.create_table('outreach_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('subject_template', sa.String(length=500), nullable=False),
    sa.Column('body_template', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outreach_templates_id'), 'outreach_templates', ['id'], unique=False)
    op.create_index(op.f('ix_outreach_templates_tenant_id'), 'outreach_templates', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_outreach_templates_tenant_id'), table_name='outreach_templates')
    op.drop_index(op.f('ix_outreach_templates_id'), table_name='outreach_templates')
    op.drop_table('outreach_templates')

    op.drop_index(op.f('ix_suppression_entries_email_normalized'), table_name='suppression_entries')
    op.drop_index(op.f('ix_suppression_entries_tenant_id'), table_name='suppression_entries')
    op.drop_index(op.f('ix_suppression_entries_id'), table_name='suppression_entries')
    op.drop_table('suppression_entries')

    op.drop_index(op.f('ix_outreach_messages_provider_message_id'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_status'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_recipient_email_snapshot'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_contact_id'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_customer_id'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_prospect_company_id'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_tenant_id'), table_name='outreach_messages')
    op.drop_index(op.f('ix_outreach_messages_id'), table_name='outreach_messages')
    op.drop_table('outreach_messages')
