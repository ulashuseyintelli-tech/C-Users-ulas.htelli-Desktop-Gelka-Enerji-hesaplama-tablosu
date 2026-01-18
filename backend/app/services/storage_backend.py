"""
Storage Backend - Abstract interface.

Implementations:
- LocalStorage: Filesystem (dev)
- S3Storage: S3/MinIO (prod)
"""
from abc import ABC, abstractmethod
from typing import BinaryIO, Optional


class StorageBackend(ABC):
    """Abstract storage interface."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """
        Store bytes and return reference.
        
        Args:
            key: Storage key (e.g., "invoices/uuid/original.pdf")
            data: File bytes
            content_type: MIME type
        
        Returns:
            Reference string (local path or s3://bucket/key)
        """
        ...

    @abstractmethod
    def get_bytes(self, ref: str) -> bytes:
        """
        Retrieve bytes by reference.
        
        Args:
            ref: Reference returned by put_bytes
        
        Returns:
            File bytes
        """
        ...

    @abstractmethod
    def exists(self, ref: str) -> bool:
        """Check if reference exists."""
        ...

    @abstractmethod
    def delete(self, ref: str) -> bool:
        """Delete by reference. Returns True if deleted."""
        ...

    def get_presigned_url(self, ref: str, expires_in: int = 300) -> Optional[str]:
        """
        Generate presigned URL for download (S3 only).
        
        Args:
            ref: Storage reference
            expires_in: URL validity in seconds
        
        Returns:
            Presigned URL or None if not supported
        """
        return None  # Default: not supported (local storage)
