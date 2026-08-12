"""add ix_market_reference_prices_period (runtime-dogrulanmis eksik index)

Revision ID: 9d4a2f6b18ce
Revises: 7b3e1c8a52df
Create Date: 2026-08-12

NEDEN (R1-B / semantic drift closure, hedef: SQLITE-ONLY):
`market_reference_prices` uzerinde period-ONLY sorgu calistiran iki gercek
runtime consumer var:
  - app/main.py:359   (sample data seeding, startup)
  - app/main.py:4174  (_unlock())
Her ikisi de `MarketReferencePrice.period == period` ile filtreliyor.

Mevcut composite index `(price_type, period)` bu sorgulari KAPSAMIYOR,
cunku `period` leading column degil. EXPLAIN QUERY PLAN kaniti (alembic
base->head ile kurulmus DB uzerinde):

  WHERE period=?                  -> SCAN market_reference_prices
  WHERE price_type=?              -> SEARCH ... USING INDEX
                                     ix_market_reference_prices_price_type_period
  WHERE price_type=? AND period=? -> SEARCH ... USING INDEX (ayni composite)

Yani yalniz `period` icin ayri bir index GEREKLI; `price_type` icin DEGIL
(composite'in leading column'u zaten karsiliyor).

BILINCLI OLARAK EKLENMEYENLER (gereksiz olduklari EXPLAIN ile kanitlandi):
- ix_market_reference_prices_price_type: composite leading column kapsiyor.
- ix_audit_logs_id / ix_customers_id / ix_offers_id / ix_webhook_configs_id
  / ix_webhook_deliveries_id: SQLite'ta INTEGER PRIMARY KEY zaten rowid'dir;
  `WHERE id=?` sorgulari "SEARCH ... USING INTEGER PRIMARY KEY (rowid=?)"
  ile cozuluyor. Ayri bir id index'i okuma kazanci saglamaz, yalniz yazma
  maliyeti (write amplification) ekler.
- ix_webhook_deliveries_webhook_config_id: mevcut ix_webhook_deliveries_config
  ile AYNI kolon imzasina sahip; yalniz ad farkli (NAMING_ONLY).
- sqlite_autoindex_market_reference_prices_1: mevcut
  ix_market_reference_prices_price_type_period ile esdeger (NAMING_ONLY).

Bu revision statik bir Alembic tanimidir; calisma aninda app/ veya
Base.metadata IMPORT ETMEZ.

Cagrildigi yerler:
- alembic upgrade zinciri: 7b3e1c8a52df -> 9d4a2f6b18ce
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9d4a2f6b18ce'
down_revision: Union[str, None] = '7b3e1c8a52df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_market_reference_prices_period',
        'market_reference_prices',
        ['period'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        'ix_market_reference_prices_period',
        table_name='market_reference_prices',
    )
