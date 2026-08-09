"""add activity and task tables (S2)

Revision ID: 8b9a332a3680
Revises: dc8343278cfa
Create Date: 2026-08-09 21:36:15.151703

NOT (S2 — Activity & Task Engine, owner kararı): Bu migration ELLE yazıldı,
`alembic revision --autogenerate` KULLANILMADI. Gerekçe: bu branch'in dev
DB'si (backend/gelka_enerji.db), master'a hiç merge edilmemiş ayrı bir
branch'in (ptf-sot-unification-*) migration'larıyla (012/013,
ptf_drift_log tablosu) stamp edilmiş halde bulunmuştu — autogenerate'in
mevcut DB state'ine güvenmesi yanıltıcı bir diff üretebilirdi. DB dosyası
S2 kapsamı dışı bir sorun olduğu için (yalnız dev/test verisi içeriyordu,
production DB'sine dokunulmadı) silinip alembic zinciriyle yeniden
kuruldu; bu süreçte ayrıca S2'yle ilgisiz, önceden var olan bir ikinci
migration zinciri hatası bulundu: `004_incident_v2_sprint6`, henüz hiçbir
migration'da oluşturulmamış `incidents` tablosuna ALTER COLUMN uygulamaya
çalışıyor (sıfırdan `alembic upgrade head` bu yüzden patlıyor). Bu da S2
kapsamı dışı bırakıldı, PR açıklamasında residual risk olarak kaydedildi.

Şema kaynağı: app/database.py Activity/Task sınıfları (bkz. oradaki
docstring'ler — subject modeli, FK/ondelete/CASCADE gerekçesi, created_by/
assignee'nin bilinçli olarak eklenmemesi).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b9a332a3680'
down_revision: Union[str, None] = 'dc8343278cfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('activities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('offer_id', sa.Integer(), nullable=True),
    sa.Column('contract_id', sa.Integer(), nullable=True),
    sa.Column('activity_type', sa.String(length=30), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_activities_id'), 'activities', ['id'], unique=False)
    op.create_index(op.f('ix_activities_tenant_id'), 'activities', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_activities_customer_id'), 'activities', ['customer_id'], unique=False)
    op.create_index(op.f('ix_activities_offer_id'), 'activities', ['offer_id'], unique=False)
    op.create_index(op.f('ix_activities_contract_id'), 'activities', ['contract_id'], unique=False)
    op.create_index(op.f('ix_activities_activity_type'), 'activities', ['activity_type'], unique=False)

    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('offer_id', sa.Integer(), nullable=True),
    sa.Column('contract_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('due_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
    sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tasks_id'), 'tasks', ['id'], unique=False)
    op.create_index(op.f('ix_tasks_tenant_id'), 'tasks', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_tasks_customer_id'), 'tasks', ['customer_id'], unique=False)
    op.create_index(op.f('ix_tasks_offer_id'), 'tasks', ['offer_id'], unique=False)
    op.create_index(op.f('ix_tasks_contract_id'), 'tasks', ['contract_id'], unique=False)
    op.create_index(op.f('ix_tasks_due_at'), 'tasks', ['due_at'], unique=False)
    op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_tasks_status'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_due_at'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_contract_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_offer_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_customer_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_tenant_id'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_id'), table_name='tasks')
    op.drop_table('tasks')

    op.drop_index(op.f('ix_activities_activity_type'), table_name='activities')
    op.drop_index(op.f('ix_activities_contract_id'), table_name='activities')
    op.drop_index(op.f('ix_activities_offer_id'), table_name='activities')
    op.drop_index(op.f('ix_activities_customer_id'), table_name='activities')
    op.drop_index(op.f('ix_activities_tenant_id'), table_name='activities')
    op.drop_index(op.f('ix_activities_id'), table_name='activities')
    op.drop_table('activities')
