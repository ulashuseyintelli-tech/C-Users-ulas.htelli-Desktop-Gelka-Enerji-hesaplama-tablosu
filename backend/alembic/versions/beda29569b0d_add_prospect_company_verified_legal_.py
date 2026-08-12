"""add prospect company verified legal type s5

Revision ID: beda29569b0d
Revises: f4e7efc70c80
Create Date: 2026-08-10 19:06:19.571365

NOT (S5 — Outreach, owner'ın 10.08 düzeltme talimatı): S2/S4/f4e7efc70c80
ile AYNI gerekçeyle ELLE yazıldı (autogenerate KULLANILMADI).

Şema kaynağı: app/database.py ProspectCompany.verified_legal_type* alanları
(bkz. oradaki docstring + app/outreach/compliance.py modül docstring'i —
"contact_type ile recipient_legal_type ayrı eksenlerdir, biri diğerinden
otomatik türetilmez").

Additive migration — prospect_companies tablosuna yalnız 3 nullable kolon
ekler, MEVCUT hiçbir satırı/kolonu değiştirmez. S4'ün paketlenmiş/kurulu
üründeki gerçek prospect_companies verisi (v1.0.6) ETKİLENMEZ.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'beda29569b0d'
down_revision: Union[str, None] = 'f4e7efc70c80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prospect_companies', sa.Column('verified_legal_type', sa.String(length=20), nullable=True))
    op.add_column('prospect_companies', sa.Column('verified_legal_type_note', sa.Text(), nullable=True))
    op.add_column('prospect_companies', sa.Column('verified_legal_type_set_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('prospect_companies', 'verified_legal_type_set_at')
    op.drop_column('prospect_companies', 'verified_legal_type_note')
    op.drop_column('prospect_companies', 'verified_legal_type')
