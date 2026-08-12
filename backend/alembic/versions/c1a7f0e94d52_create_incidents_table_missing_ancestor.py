"""create incidents table (004 icin eksik atalar migration'i)

Revision ID: c1a7f0e94d52
Revises: 18100a648086
Create Date: 2026-08-12

NEDEN (PDSMR-R1 / Asama 1 — 004 migration repair):
`incidents` tablosu bir SQLAlchemy modeli olarak HER ZAMAN vardi
(app/database.py::Incident) ama migration zincirinde onu OLUSTURAN hicbir
revision YAZILMAMISTI. Migration 004-010 arasindaki alti revision bu
tabloya add_column/create_index uyguluyor; dolayisiyla `alembic upgrade
head` TAZE/BOS bir veritabaninda tam olarak burada oluyordu:

    Running upgrade 18100a648086 -> 004, Incident v2 - Sprint 6.1
    sqlite3.OperationalError: no such table: incidents

Tabloyu bugune kadar YALNIZ `Base.metadata.create_all()` olusturdu — yani
migration zinciri hicbir zaman kendi kendine yeterli olmadi. Bu revision o
yapisal bosluğu kapatir.

SEMA NASIL TURETILDI (tahmin YOK — uc bagimsiz kaynakla ucgenleme):
  1. app/database.py::Incident modeli            -> 45 kolon
  2. 004-010 migration'larinin ekledigi kolonlar -> 26 kolon
  3. 45 - 26 = 19 kolon  (aritmetik TAM kapaniyor, artik/eksik yok)
  4. Capraz dogrulama: gercek production DB'de `incidents` 45 kolon ve bu
     19 base kolonun HEPSI mevcut.

STATIK ICERIK (owner kapsam kilidi): asagidaki kolon tanimlari YAZIM
ANINDA modelin metadata'sindan uretildi, ama buraya SABIT metin olarak
yazildi. Bu migration CALISMA ANINDA app/database.py'yi, Incident
modelini, Base.metadata'yi veya baska hicbir uygulama kodunu IMPORT
ETMEZ — yalnizca `alembic`, `sqlalchemy` ve `typing` import eder.
Dolayisiyla model ileride degisse bile bu migration'in urettigi sema
DEGISMEZ; tarihsel migration'larin tasimasi gereken ozellik tam olarak
budur.

GERIYE DONUK UYUMLULUK:
- 004'un down_revision'i 18100a648086 -> c1a7f0e94d52 olarak degistirildi.
  Bu, alembic'te bir revision'i zincire SOKMANIN standart yoludur.
- 004 veya sonrasinda olan bir DB (orn. production, 013'te) ILERI yonde
  etkilenmez: alembic yalnizca mevcut pointer'dan HEAD'e giden yolu
  hesaplar, gecmis atalari yeniden calistirmaz.
- Bu revision BILINCLI olarak "IF NOT EXISTS" savunmasi ICERMEZ. Zaten
  `incidents` tablosuna sahip legacy (create_all ile kurulmus) veritabanlari
  bu migration'in degil, ayri ve fail-closed LEGACY_SCHEMA_ADOPTION
  araciinin konusudur (owner karari: "historical migration'lari genel
  olarak IF EXISTS/IF NOT EXISTS bicimine cevirip problemi gizlemek YASAK").

Cagrildigi yerler:
- alembic upgrade zinciri: 18100a648086 -> c1a7f0e94d52 -> 004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a7f0e94d52'
down_revision: Union[str, None] = '18100a648086'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'incidents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('trace_id', sa.String(length=50), nullable=False),
        sa.Column('tenant_id', sa.String(length=50), nullable=False),
        sa.Column('invoice_id', sa.String(length=50), nullable=True),
        sa.Column('offer_id', sa.Integer(), nullable=True),
        sa.Column('severity', sa.String(length=5), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=False),
        sa.Column('message', sa.String(length=1000), nullable=False),
        sa.Column('details_json', sa.JSON(), nullable=True),
        sa.Column('dedupe_key', sa.String(length=64), nullable=True),
        sa.Column('occurrence_count', sa.Integer(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('resolution_note', sa.String(length=1000), nullable=True),
        sa.Column('resolved_by', sa.String(length=100), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_incidents_id'), 'incidents', ['id'], unique=False)
    op.create_index(op.f('ix_incidents_trace_id'), 'incidents', ['trace_id'], unique=False)
    op.create_index(op.f('ix_incidents_tenant_id'), 'incidents', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_incidents_invoice_id'), 'incidents', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_incidents_severity'), 'incidents', ['severity'], unique=False)
    op.create_index(op.f('ix_incidents_category'), 'incidents', ['category'], unique=False)
    op.create_index(op.f('ix_incidents_dedupe_key'), 'incidents', ['dedupe_key'], unique=False)
    op.create_index(op.f('ix_incidents_status'), 'incidents', ['status'], unique=False)
    op.create_index(op.f('ix_incidents_created_at'), 'incidents', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_incidents_created_at'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_status'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_dedupe_key'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_category'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_severity'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_invoice_id'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_tenant_id'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_trace_id'), table_name='incidents')
    op.drop_index(op.f('ix_incidents_id'), table_name='incidents')
    op.drop_table('incidents')
