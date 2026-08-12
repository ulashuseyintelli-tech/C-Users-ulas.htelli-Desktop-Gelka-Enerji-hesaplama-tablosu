"""merge ptf-sot legacy lineage into canonical chain

Revision ID: 351d314819d5
Revises: 9d4a2f6b18ce, 013_extend_ptf_drift_severity
Create Date: 2026-08-12

NEDEN (PDSMR-R1C / Asama 1 — migration graph repair):
Migration grafigi `011_market_prices_ptf_admin`'de CATALLANMISTI:

    011_market_prices_ptf_admin
    |-- a93beeaddf82 -> ... -> 9d4a2f6b18ce   [canonical / master]
    +-- 012_add_ptf_drift_log_table -> 013_extend_ptf_drift_severity
                                              [ptf-sot-unification, unmerged]

Gercek production veritabani `013_extend_ptf_drift_severity` ile
damgalidir — yani canonical grafikte TANINMAYAN bir bacakta. Bu yuzden
`alembic upgrade head` production DB'de "Can't locate revision" ile
oluyordu.

Bu merge revision iki bacagi TEK head altinda birlestirir; boylece
production DB'nin gercek migration soyu canonical graph icinde
TANINIR hale gelir.

BU BIR FEATURE MERGE DEGILDIR — lineage repair'dir:
012/013 dosyalari commit 6abe5ca provenance'indan, SHA-256 esitligi
dogrulanarak historical migration olarak alinmistir:
  012_add_ptf_drift_log_table
    d3afed06a680ba7605e3b15ba654e906b66b237889d3dc4eb0a4cf66e9b51b54
  013_extend_ptf_drift_severity
    fb8c5a7adb359738086282e76fa0a2ccbe1dc9cd1068838f86b6fc7ef90993d2
PTF feature KODU (servis/router/model) TASINMAMISTIR; yalniz iki
migration dosyasi ve bu merge noktasi eklenmistir.

Merge revision BILEREK BOSTUR (no-op): iki bacak da additive'dir ve
uzlastirilacak cakisan sema degisikligi YOKTUR.

ONEMLI — bu merge TEK BASINA production DB'yi upgrade EDILEBILIR
YAPMAZ. Production semasi `create_all()` ile kuruldugu icin canonical
bacaktaki migration'lar "table ... already exists" ile reddedilir
(deterministik olarak dogrulandi). Mevcut production DB icin ayri ve
fail-closed bir LEGACY ADOPTION araci gereklidir; o is bu revision'in
KAPSAMI DISINDADIR.

Cagrildigi yerler:
- alembic upgrade zinciri: (9d4a2f6b18ce, 013_extend_ptf_drift_severity)
  -> 351d314819d5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '351d314819d5'
down_revision: Union[str, None] = ('9d4a2f6b18ce', '013_extend_ptf_drift_severity')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
