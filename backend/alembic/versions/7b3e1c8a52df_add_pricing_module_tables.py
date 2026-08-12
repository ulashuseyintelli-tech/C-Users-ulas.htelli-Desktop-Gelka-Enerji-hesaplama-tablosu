"""add pricing module tables (zincirin uretmedigi 8 tablo)

Revision ID: 7b3e1c8a52df
Revises: beda29569b0d
Create Date: 2026-08-12

NEDEN (R1-A / additive reconciliation):
app/pricing/schemas.py'deki sekiz tablo bir SQLAlchemy modeli olarak
vardi ve `pricing_router` app/main.py'de mount edilmis durumda (yani
gercek runtime consumer'lari var: 2-8 kaynak dosyada referans), ama
migration zincirinde onlari OLUSTURAN hicbir revision yazilmamisti.
Bugune kadar yalniz `Base.metadata.create_all()` uretti.

Uc yonlu envanter (alembic base->head / create_all / production kopyasi)
bu sekiz tabloyu REAL_MISSING_MIGRATION olarak siniflandirdi: zincirin
urettigi sema modeli karsilamiyordu.

STATIK ICERIK: asagidaki tanimlar YAZIM ANINDA model metadata'sindan
uretildi, ancak buraya SABIT metin olarak yazildi. Bu migration CALISMA
ANINDA app/, Base.metadata veya baska hicbir uygulama kodunu IMPORT
ETMEZ; yalnizca alembic, sqlalchemy ve typing import eder. Model
ileride degisse bile bu migration'in urettigi sema DEGISMEZ.

KAPSAM SINIRI (owner kilidi): bu revision YALNIZ eksik tablolari ekler.
Type/nullability/index drop-recreate/constraint rebuild gerektiren
drift'ler BU PR'A DAHIL DEGILDIR ve acik karar listesine tasinmistir.
Historical migration'lar degistirilmemistir.

Cagrildigi yerler:
- alembic upgrade zinciri: beda29569b0d -> 7b3e1c8a52df
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7b3e1c8a52df'
down_revision: Union[str, None] = 'beda29569b0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'analysis_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('customer_id', sa.String(length=100), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('params_hash', sa.String(length=64), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('hit_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cache_key'),
    )
    op.create_index('idx_cache_customer_period', 'analysis_cache', ['customer_id', 'period'], unique=False)
    op.create_index('idx_cache_expires', 'analysis_cache', ['expires_at'], unique=False)
    op.create_index('ix_analysis_cache_id', 'analysis_cache', ['id'], unique=False)

    op.create_table(
        'consumption_hourly_data',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('consumption_kwh', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['profile_id'], ['consumption_profiles.id']),
        sa.UniqueConstraint('profile_id', 'date', 'hour', name='uq_consumption_hourly'),
    )
    op.create_index('ix_consumption_hourly_data_id', 'consumption_hourly_data', ['id'], unique=False)
    op.create_index('ix_consumption_hourly_data_profile_id', 'consumption_hourly_data', ['profile_id'], unique=False)

    op.create_table(
        'consumption_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.String(length=100), nullable=False),
        sa.Column('customer_name', sa.String(length=255), nullable=True),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('profile_type', sa.String(length=20), nullable=False),
        sa.Column('template_name', sa.String(length=100), nullable=True),
        sa.Column('total_kwh', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('customer_id', 'period', 'version', name='uq_consumption_customer_period_version'),
    )
    op.create_index('idx_consumption_active', 'consumption_profiles', ['customer_id', 'period', 'is_active'], unique=False)
    op.create_index('ix_consumption_profiles_customer_id', 'consumption_profiles', ['customer_id'], unique=False)
    op.create_index('ix_consumption_profiles_id', 'consumption_profiles', ['id'], unique=False)
    op.create_index('ix_consumption_profiles_period', 'consumption_profiles', ['period'], unique=False)

    op.create_table(
        'data_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('data_type', sa.String(length=30), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('customer_id', sa.String(length=100), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('uploaded_by', sa.String(length=100), nullable=True),
        sa.Column('upload_filename', sa.String(length=255), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('quality_score', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('data_type', 'period', 'customer_id', 'version', name='uq_data_version'),
    )
    op.create_index('idx_data_versions_lookup', 'data_versions', ['data_type', 'period', 'customer_id'], unique=False)
    op.create_index('ix_data_versions_id', 'data_versions', ['id'], unique=False)

    op.create_table(
        'hourly_market_prices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('ptf_tl_per_mwh', sa.Float(), nullable=False),
        sa.Column('smf_tl_per_mwh', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('period', 'date', 'hour', 'version', name='uq_hourly_period_date_hour_version'),
    )
    op.create_index('idx_hourly_market_date_hour', 'hourly_market_prices', ['date', 'hour'], unique=False)
    op.create_index('idx_hourly_market_period_active', 'hourly_market_prices', ['period', 'is_active'], unique=False)
    op.create_index('ix_hourly_market_prices_id', 'hourly_market_prices', ['id'], unique=False)
    op.create_index('ix_hourly_market_prices_period', 'hourly_market_prices', ['period'], unique=False)

    op.create_table(
        'monthly_yekdem_prices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('yekdem_tl_per_mwh', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monthly_yekdem_prices_id', 'monthly_yekdem_prices', ['id'], unique=False)
    op.create_index('ix_monthly_yekdem_prices_period', 'monthly_yekdem_prices', ['period'], unique=True)

    op.create_table(
        'price_change_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('price_record_id', sa.Integer(), nullable=False),
        sa.Column('price_type', sa.String(length=20), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('action', sa.String(length=10), nullable=False),
        sa.Column('old_value', sa.Float(), nullable=True),
        sa.Column('new_value', sa.Float(), nullable=False),
        sa.Column('old_status', sa.String(length=20), nullable=True),
        sa.Column('new_status', sa.String(length=20), nullable=False),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['price_record_id'], ['market_reference_prices.id']),
    )
    op.create_index('ix_price_change_history_created_at', 'price_change_history', ['created_at'], unique=False)
    op.create_index('ix_price_change_history_id', 'price_change_history', ['id'], unique=False)
    op.create_index('ix_price_change_history_price_record_id', 'price_change_history', ['price_record_id'], unique=False)

    op.create_table(
        'profile_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hourly_weights', sa.Text(), nullable=False),
        sa.Column('is_builtin', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_profile_templates_id', 'profile_templates', ['id'], unique=False)


def downgrade() -> None:
    op.drop_table('profile_templates')
    op.drop_table('price_change_history')
    op.drop_table('monthly_yekdem_prices')
    op.drop_table('hourly_market_prices')
    op.drop_table('data_versions')
    op.drop_table('consumption_profiles')
    op.drop_table('consumption_hourly_data')
    op.drop_table('analysis_cache')
