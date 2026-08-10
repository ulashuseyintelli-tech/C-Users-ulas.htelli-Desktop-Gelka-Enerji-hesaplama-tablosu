"""add prospecting tables s4

Revision ID: e340ce40c05c
Revises: 8b9a332a3680
Create Date: 2026-08-10 10:20:35.837775

NOT (S4 — Prospecting, owner kararı): Bu migration ELLE yazıldı,
`alembic revision --autogenerate` KULLANILMADI — S2'nin 8b9a332a3680
migration'ıyla aynı gerekçe: bu proje autogenerate'in mevcut DB state'ine
güvenmesini riskli buluyor (bkz. 8b9a332a3680 docstring'i, ptf-sot-
unification-* residual'ı). Hash-ID isimlendirme kullanıldı (sıralı numara
DEĞİL) çünkü unmerged `ptf-sot-unification-*` branch ailesi 012/013
numaralarını zaten claim etmiş durumda.

Şema kaynağı: app/database.py ProspectCompany/ProspectContact/
ProspectSource sınıfları (bkz. oradaki docstring'ler — PROSPECT ≠
CUSTOMER ayrımı, tenant_id konvansiyonu, FK/cascade gerekçesi).

Bu migration additive'dir — S1/S2/S3'ün hiçbir tablosuna dokunmaz,
004_incident_v2_sprint6 residual'ına karışmaz (down_revision zinciri
yalnız 8b9a332a3680 → dc8343278cfa → ... üzerinden ilerler).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e340ce40c05c'
down_revision: Union[str, None] = '8b9a332a3680'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('prospect_companies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('legal_name', sa.String(length=255), nullable=True),
    sa.Column('trade_name', sa.String(length=255), nullable=True),
    sa.Column('normalized_name', sa.String(length=255), nullable=True),
    sa.Column('website', sa.String(length=500), nullable=True),
    sa.Column('normalized_domain', sa.String(length=255), nullable=True),
    sa.Column('sector', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('district', sa.String(length=100), nullable=True),
    sa.Column('industrial_zone', sa.String(length=255), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('qualification_reason', sa.String(length=50), nullable=True),
    sa.Column('qualification_note', sa.Text(), nullable=True),
    sa.Column('duplicate_of_id', sa.Integer(), nullable=True),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('discovered_at', sa.DateTime(), nullable=True),
    sa.Column('last_verified_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['duplicate_of_id'], ['prospect_companies.id'], ),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_prospect_companies_id'), 'prospect_companies', ['id'], unique=False)
    op.create_index(op.f('ix_prospect_companies_tenant_id'), 'prospect_companies', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_prospect_companies_normalized_name'), 'prospect_companies', ['normalized_name'], unique=False)
    op.create_index(op.f('ix_prospect_companies_normalized_domain'), 'prospect_companies', ['normalized_domain'], unique=False)
    op.create_index(op.f('ix_prospect_companies_city'), 'prospect_companies', ['city'], unique=False)
    op.create_index(op.f('ix_prospect_companies_status'), 'prospect_companies', ['status'], unique=False)
    op.create_index(op.f('ix_prospect_companies_customer_id'), 'prospect_companies', ['customer_id'], unique=False)

    op.create_table('prospect_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('prospect_company_id', sa.Integer(), nullable=False),
    sa.Column('source_url', sa.String(length=1000), nullable=False),
    sa.Column('source_type', sa.String(length=30), nullable=False),
    sa.Column('source_title', sa.String(length=500), nullable=True),
    sa.Column('evidence_text', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('fetch_status', sa.String(length=30), nullable=False),
    sa.Column('discovered_at', sa.DateTime(), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['prospect_company_id'], ['prospect_companies.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_prospect_sources_id'), 'prospect_sources', ['id'], unique=False)
    op.create_index(op.f('ix_prospect_sources_tenant_id'), 'prospect_sources', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_prospect_sources_prospect_company_id'), 'prospect_sources', ['prospect_company_id'], unique=False)
    op.create_index(op.f('ix_prospect_sources_content_hash'), 'prospect_sources', ['content_hash'], unique=False)

    op.create_table('prospect_contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('prospect_company_id', sa.Integer(), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('job_title', sa.String(length=255), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('contact_type', sa.String(length=30), nullable=False),
    sa.Column('verification_status', sa.String(length=30), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['prospect_company_id'], ['prospect_companies.id'], ),
    sa.ForeignKeyConstraint(['source_id'], ['prospect_sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_prospect_contacts_id'), 'prospect_contacts', ['id'], unique=False)
    op.create_index(op.f('ix_prospect_contacts_tenant_id'), 'prospect_contacts', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_prospect_contacts_prospect_company_id'), 'prospect_contacts', ['prospect_company_id'], unique=False)
    op.create_index(op.f('ix_prospect_contacts_email'), 'prospect_contacts', ['email'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_prospect_contacts_email'), table_name='prospect_contacts')
    op.drop_index(op.f('ix_prospect_contacts_prospect_company_id'), table_name='prospect_contacts')
    op.drop_index(op.f('ix_prospect_contacts_tenant_id'), table_name='prospect_contacts')
    op.drop_index(op.f('ix_prospect_contacts_id'), table_name='prospect_contacts')
    op.drop_table('prospect_contacts')

    op.drop_index(op.f('ix_prospect_sources_content_hash'), table_name='prospect_sources')
    op.drop_index(op.f('ix_prospect_sources_prospect_company_id'), table_name='prospect_sources')
    op.drop_index(op.f('ix_prospect_sources_tenant_id'), table_name='prospect_sources')
    op.drop_index(op.f('ix_prospect_sources_id'), table_name='prospect_sources')
    op.drop_table('prospect_sources')

    op.drop_index(op.f('ix_prospect_companies_customer_id'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_status'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_city'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_normalized_domain'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_normalized_name'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_tenant_id'), table_name='prospect_companies')
    op.drop_index(op.f('ix_prospect_companies_id'), table_name='prospect_companies')
    op.drop_table('prospect_companies')
