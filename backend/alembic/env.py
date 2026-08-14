"""
Alembic Environment Configuration.

Reads database URL from app settings.

PDSMR-R3A: FROZEN in-process cagrilar (bkz.
app/legacy_adoption/alembic_runner.py::_frozen_config) hedef DB dosyasini
`config.attributes["pdsmr_target_db_path"]` UZERINDEN GECER - surecin
DATABASE_URL ortam degiskeni bu akis icin KONTROL KANALI DEGILDIR (owner
karari). KOK NEDEN (duzeltilen): bu dosya ONCEDEN, attribute'un varligindan
BAGIMSIZ, KOSULSUZ settings.database_url'e DUSUYORDU - bu, frozen in-process
alembic cagrilarinin calisma-kopyasi/atomik-yayimlama guvenlik modelini
SESSIZCE BOZUYORDU (canli frozen exe testinde KANITLANDI, bkz.
alembic_runner.py modul dokstring'i). Attribute YOKSA (CLI `alembic upgrade
head`, dev-subprocess `_run_alembic_subprocess`) davranis DEGISMEDEN
settings.database_url kullanilir - byte-semantik olarak ONCEKI ile AYNI.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy.engine import URL

from alembic import context

# Import app models and settings
from app.core.config import settings
from app.database import Base

# Import all models to ensure they're registered with Base.metadata
from app.database import Customer, Offer, Invoice, Job

# this is the Alembic Config object
config = context.config

# ── PDSMR-R3A: hedef DB URL'ini belirle ──────────────────────────────────
_pdsmr_hedef_db_yolu = config.attributes.get("pdsmr_target_db_path")
if _pdsmr_hedef_db_yolu:
    # Ham string birlestirme YOK - SQLAlchemy'nin kendi URL insasi
    # kullanilir (bosluk/Turkce/'%' iceren Windows yollari icin ampirik
    # DOGRULANDI - PRAGMA database_list ile fiziksel dosya eslesmesi,
    # bkz. PDSMR-R3A kapanis raporu).
    _pdsmr_hedef_url = URL.create("sqlite", database=_pdsmr_hedef_db_yolu).render_as_string(
        hide_password=False
    )
    # ConfigParser degeri INTERPOLATE eder ('%(...)s' sozdizimi) - ham '%'
    # kacisi GEREKLI (bkz. alembic.config.Config.set_main_option docstring'i).
    config.set_main_option("sqlalchemy.url", _pdsmr_hedef_url.replace("%", "%%"))
else:
    # DEGISMEDEN - CLI/dev-subprocess davranisi byte-semantik AYNI kalir.
    config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()
    # Ad-hoc engine - acikca dispose et (NullPool zaten baglantiyi kapatir,
    # ama engine'in kendisini birakmak SQLAlchemy'nin onerdigi hijyendir -
    # PDSMR-R3A tani sirasinda EKLENDI, zarasiz/faydali savunma katmani).
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
