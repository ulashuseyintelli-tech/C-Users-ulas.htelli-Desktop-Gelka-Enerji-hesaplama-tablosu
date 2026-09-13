"""fiyat kimligi ve yetkili onay revizyonlari (PTF + segment basina YEKDEM)

Revision ID: b7e4c2d91a60
Revises: 351d314819d5
Create Date: 2026-09-13

NEDEN (OWNER-KARARI-01/02, 2026-09-13):
- Kesin teklifte PTF, resmi veriden dogrulanmis ve yetkili tarafindan onaylanmis
  AYLIK ARITMETIK PTF'dir. Onay revizyonu kaydin onay anindaki parmak izini tasir;
  kayda sonradan yapilan her deger/durum/kaynak degisikligi onayi gecersiz kilar.
- YEKDEM onayi segment basinadir (st | gts). `market_reference_prices` donem basina
  TEK YEKDEM kolonu ve benzersiz (price_type, period) tasidigi icin iki segment o
  tabloya eklenemez; bu yuzden ekle-yalniz ayri tablo.

KAYNAK KIMLIGI: `version` resmi yanittaki metnin KISALTILMAMIS halidir (ayni ay icindeki
farkli versiyonlar ayirt edilir); `kaynak_kanit_json` onay ekraninda incelenebilen kanittir,
`kaynak_kanit_sha256` bu JSON'un kanonik ozetidir.

ONAYLAYAN: `onaylayan_beyan` formdaki ad (DOGRULANMAMIS beyan); `dogrulanan_yetki` sunucunun
dogruladigi yetki turu (or. paylasilan yonetici anahtari). Kisi kimligi dogrulanmaz.

SADECE KATKISAL: iki YENI tablo. Mevcut tablolara ALTER, geri doldurma, veri
tasima ya da finallestirme YOKTUR. Eski kayitlar kimliksiz kalir.

BASLANGIC KAPISI: app/legacy_adoption/startup_gate.py `351d314819d5` canonical DB'yi
calisma kopyasinda bu revizyona tasir (ileri_migration_uygula), veri ozetlerini karsilastirir
ve atomik yayimlar; hata durumunda canonical dosya DEGISMEZ (GateRefused 54).

Tablo tanimlari app/price_approval.py (onay_metadata) ile BIREBIR aynidir
(tests/test_price_approval_migration.py dogrular).

Cagrildigi yerler:
- alembic upgrade zinciri: 351d314819d5 -> b7e4c2d91a60
- tests/test_price_approval_migration.py (izole gecici DB)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7e4c2d91a60"
down_revision: Union[str, None] = "351d314819d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ptf_onay_revizyonlari",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("price_record_id", sa.Integer,
                  sa.ForeignKey("market_reference_prices.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("basis", sa.String(20), nullable=False),
        sa.Column("kaynak_kanit_sha256", sa.String(64), nullable=False),
        sa.Column("kaynak_kanit_json", sa.Text, nullable=False),
        sa.Column("kayit_parmak_izi", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=False),
        sa.Column("onaylayan_beyan", sa.String(100), nullable=False),
        sa.Column("dogrulanan_yetki", sa.String(64), nullable=False),
        sa.Column("approved_at", sa.DateTime, nullable=False),
        sa.Column("change_reason", sa.Text, nullable=False),
        sa.UniqueConstraint("period", "revision", name="uq_ptf_onay_period_revision"),
        sa.CheckConstraint("basis = 'mcp_avg'", name="ck_ptf_onay_basis"),
    )
    op.create_table(
        "yekdem_onay_revizyonlari",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("segment", sa.String(10), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("kaynak_kanit_sha256", sa.String(64), nullable=False),
        sa.Column("kaynak_kanit_json", sa.Text, nullable=False),
        sa.Column("captured_at", sa.DateTime, nullable=False),
        sa.Column("onaylayan_beyan", sa.String(100), nullable=False),
        sa.Column("dogrulanan_yetki", sa.String(64), nullable=False),
        sa.Column("approved_at", sa.DateTime, nullable=False),
        sa.Column("change_reason", sa.Text, nullable=False),
        sa.UniqueConstraint("period", "segment", "revision", name="uq_yekdem_onay_period_segment_revision"),
        sa.CheckConstraint("segment IN ('st', 'gts')", name="ck_yekdem_onay_segment"),
    )


def downgrade() -> None:
    op.drop_table("yekdem_onay_revizyonlari")
    op.drop_table("ptf_onay_revizyonlari")
