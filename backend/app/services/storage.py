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

from app.core.config import settings
from app.services.storage_backend import StorageBackend

logger = logging.getLogger(__name__)


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
        logger.info("Using local storage backend")
        return LocalStorage()


def clear_storage_cache():
    """Clear storage singleton (for testing)."""
    get_storage.cache_clear()
