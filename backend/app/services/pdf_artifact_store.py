"""
PDF Artifact Store — StorageBackend wrapper for PDF artifacts.

Key format: pdf/{job_id}.pdf
Delegates to LocalStorage (dev) or S3Storage (prod) via StorageBackend.
"""
from __future__ import annotations

import logging
from typing import Optional

from .storage_backend import StorageBackend

logger = logging.getLogger(__name__)

PDF_KEY_PREFIX = "pdf"
PDF_CONTENT_TYPE = "application/pdf"


class PdfArtifactStore:
    """Thin wrapper over StorageBackend for PDF lifecycle."""

    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    # -- key generation ---------------------------------------------------

    @staticmethod
    def generate_key(job_id: str) -> str:
        """Deterministic artifact key: pdf/{job_id}.pdf"""
        return f"{PDF_KEY_PREFIX}/{job_id}.pdf"

    # -- CRUD -------------------------------------------------------------

    def store_pdf(self, job_id: str, pdf_bytes: bytes) -> str:
        """
        Store PDF bytes via StorageBackend.
        Returns the artifact_key (storage reference from put_bytes).
        """
        key = self.generate_key(job_id)
        ref = self._storage.put_bytes(key, pdf_bytes, PDF_CONTENT_TYPE)
        logger.info(f"Stored PDF artifact: key={key}, ref={ref}, size={len(pdf_bytes)}")
        return ref

    def get_pdf(self, artifact_key: str) -> bytes:
        """Retrieve PDF bytes by artifact_key (storage reference)."""
        return self._storage.get_bytes(artifact_key)

    def exists(self, artifact_key: str) -> bool:
        """Check if artifact exists in storage."""
        return self._storage.exists(artifact_key)

    def delete_pdf(self, artifact_key: str) -> bool:
        """Delete artifact from storage. Returns True if deleted."""
        result = self._storage.delete(artifact_key)
        if result:
            logger.info(f"Deleted PDF artifact: {artifact_key}")
        return result
