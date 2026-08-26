"""
Storage Factory - Returns appropriate backend based on config.

Usage:
    from app.services.storage import get_storage

    storage = get_storage()
    ref = storage.put_bytes("invoices/123/original.pdf", data, "application/pdf")
    data = storage.get_bytes(ref)
"""
import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.services.storage_backend import StorageBackend

logger = logging.getLogger(__name__)

# S5-R03B: `settings.storage_dir`'in KENDİ (kod) varsayılanı — Electron
# packaged modda STORAGE_DIR env'iyle bunu durable `userData/storage`'a
# EZER. Legacy-okuma kökü bu SABİT/CWD-göreli değeri (STORAGE_DIR ne olursa
# olsun) HER ZAMAN temsil eder — packaged'da CWD daima kurulum dizini
# (`resources/backend`) olduğundan (run_server.py frozen os.chdir + main.js
# spawn cwd:), bu tam olarak ESKİ (pre-R03B) kodun storage_dir hiç
# override edilmeden çözdüğü konumdur. `Settings` alan varsayılanıyla
# senkron tutulur (drift'i önlemek için tek kaynaktan okunur).
_LEGACY_STORAGE_DIR_VARSAYILAN = settings.model_fields["storage_dir"].default


def _legacy_storage_dir() -> str:
    """Legacy (kurulum-dizini-göreli) storage kökünü CWD'ye göre hesaplar."""
    return str(Path(_LEGACY_STORAGE_DIR_VARSAYILAN).resolve())


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    """
    Get storage backend singleton.

    Returns LocalStorage or S3Storage based on settings.storage_backend
    """
    if settings.is_s3_storage:
        from app.services.storage_s3 import S3Storage
        logger.info("Using S3 storage backend")
        return S3Storage()
    else:
        from app.services.storage_local import LocalStorage
        durable = str(Path(settings.storage_dir).resolve())
        legacy = _legacy_storage_dir()
        # legacy_base_dir yalnız DURABLE kökten GERÇEKTEN FARKLIYSA verilir
        # (STORAGE_DIR override edilmemişse ikisi zaten aynı yoldur — dev/test
        # varsayılanı; ayrı bir "legacy" kavramına gerek yok).
        legacy_arg = legacy if legacy != durable else None
        logger.info(
            f"Using local storage backend (durable={durable}, legacy={legacy_arg})"
        )
        return LocalStorage(base_dir=durable, legacy_base_dir=legacy_arg)


def clear_storage_cache():
    """Clear storage singleton (for testing)."""
    get_storage.cache_clear()
