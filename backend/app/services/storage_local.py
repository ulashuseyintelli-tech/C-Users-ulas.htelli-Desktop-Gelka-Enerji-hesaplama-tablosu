"""
Local Filesystem Storage Backend.

For development and simple deployments.
"""
import os
import logging
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.storage_backend import StorageBackend

logger = logging.getLogger(__name__)


class LocalStorage(StorageBackend):
    """Local filesystem storage."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.storage_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized: {self.base_dir}")

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes to local filesystem."""
        path = self.base_dir / key
        
        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "wb") as f:
            f.write(data)
        
        logger.debug(f"Stored {len(data)} bytes to {path}")
        return str(path)  # Local path as reference

    def get_bytes(self, ref: str) -> bytes:
        """Read bytes from local filesystem."""
        path = self.resolve_local_path(ref)
        with open(path, "rb") as f:
            return f.read()

    def exists(self, ref: str) -> bool:
        """Check if file exists."""
        try:
            path = self.resolve_local_path(ref)
            return os.path.exists(path)
        except ValueError:
            return False

    def delete(self, ref: str) -> bool:
        """Delete file."""
        try:
            path = self.resolve_local_path(ref)
            if os.path.exists(path):
                os.remove(path)
                return True
            return False
        except Exception as e:
            logger.error(f"Delete failed for {ref}: {e}")
            return False

    def resolve_local_path(self, ref: str) -> str:
        """
        Resolve and validate local path.
        
        Security: Prevents path traversal attacks by ensuring
        the resolved path is within storage_dir.
        
        Args:
            ref: Local file reference (path)
        
        Returns:
            Validated absolute path
        
        Raises:
            ValueError: If path is outside storage_dir (path traversal attempt)
        """
        resolved = Path(ref).resolve()
        
        # Security check: path must be within base_dir
        try:
            resolved.relative_to(self.base_dir)
        except ValueError:
            raise ValueError(f"Invalid local ref: path traversal detected ({ref})")
        
        return str(resolved)

    def get_local_path(self, ref: str) -> Optional[str]:
        """
        Get validated local path for streaming.
        
        Returns:
            Local path string or None if invalid
        """
        try:
            return self.resolve_local_path(ref)
        except ValueError:
            return None
