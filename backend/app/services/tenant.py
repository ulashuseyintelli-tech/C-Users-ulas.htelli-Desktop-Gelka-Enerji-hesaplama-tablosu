"""
Multi-tenant Support - Tenant isolation via header.

MVP: Optional tenant (X-Tenant-Id header)
Prod: Required tenant with validation

Usage:
    from app.services.tenant import get_tenant_id
    
    @app.get("/invoices")
    def list_invoices(tenant_id: str = Depends(get_tenant_id)):
        return db.query(Invoice).filter(Invoice.tenant_id == tenant_id).all()
"""
import logging
from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


def get_tenant_id(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")
) -> str:
    """
    Extract tenant ID from request header.
    
    Args:
        x_tenant_id: Tenant ID from X-Tenant-Id header
    
    Returns:
        Tenant ID string
    
    Raises:
        HTTPException 400: If tenant required but not provided
    """
    if x_tenant_id:
        # Sanitize tenant ID (alphanumeric + hyphen/underscore only)
        sanitized = "".join(c for c in x_tenant_id if c.isalnum() or c in "-_")[:64]
        if sanitized != x_tenant_id:
            logger.warning(f"Tenant ID sanitized: {x_tenant_id} -> {sanitized}")
        return sanitized
    
    if settings.tenant_required:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "tenant_required",
                "message": "X-Tenant-Id header is required"
            }
        )
    
    return settings.default_tenant


def get_optional_tenant_id(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")
) -> Optional[str]:
    """
    Extract optional tenant ID (for backwards compatibility).
    
    Returns None if no tenant header provided.
    """
    if not x_tenant_id:
        return None
    
    # Sanitize
    return "".join(c for c in x_tenant_id if c.isalnum() or c in "-_")[:64]
