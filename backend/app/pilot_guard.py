"""
Pilot Guard - Sprint 8.9.1

Kill switch ve pilot tenant izolasyonu için guard fonksiyonları.

ENV VARIABLES:
- PILOT_ENABLED: true/false (default: true)
- PILOT_TENANT_ID: pilot tenant identifier (default: "pilot")
- PILOT_MAX_INVOICES_PER_HOUR: rate limit (default: 50)

KULLANIM:
    from .pilot_guard import is_pilot_enabled, is_pilot_tenant, check_pilot_rate_limit

    if not is_pilot_enabled():
        logger.info("Pilot disabled, skipping job")
        return

    if is_pilot_tenant(tenant_id):
        check_pilot_rate_limit()  # Raises if exceeded
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

def _get_pilot_enabled() -> bool:
    """Get PILOT_ENABLED from env (default: true)."""
    return os.getenv("PILOT_ENABLED", "true").lower() == "true"

def _get_pilot_tenant_id() -> str:
    """Get PILOT_TENANT_ID from env (default: 'pilot')."""
    return os.getenv("PILOT_TENANT_ID", "pilot")

def _get_pilot_max_invoices_per_hour() -> int:
    """Get PILOT_MAX_INVOICES_PER_HOUR from env (default: 50)."""
    try:
        return int(os.getenv("PILOT_MAX_INVOICES_PER_HOUR", "50"))
    except ValueError:
        return 50


# ═══════════════════════════════════════════════════════════════════════════════
# GUARD FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def is_pilot_enabled() -> bool:
    """
    Check if pilot mode is enabled.
    
    Returns:
        True if PILOT_ENABLED=true (default), False otherwise
    
    Usage:
        if not is_pilot_enabled():
            logger.info("Pilot disabled, skipping")
            return
    """
    enabled = _get_pilot_enabled()
    if not enabled:
        logger.debug("Pilot mode disabled via PILOT_ENABLED=false")
    return enabled


def is_pilot_tenant(tenant_id: str | None) -> bool:
    """
    Check if given tenant_id is the pilot tenant.
    
    Args:
        tenant_id: Tenant identifier to check
    
    Returns:
        True if tenant_id matches PILOT_TENANT_ID
    """
    if tenant_id is None:
        return False
    return tenant_id == _get_pilot_tenant_id()


def get_pilot_tenant_id() -> str:
    """Get the configured pilot tenant ID."""
    return _get_pilot_tenant_id()


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING (In-Memory, Simple)
# ═══════════════════════════════════════════════════════════════════════════════

# Simple in-memory counter (resets on restart - OK for pilot)
_pilot_invoice_timestamps: list[datetime] = []


class PilotRateLimitExceeded(Exception):
    """Raised when pilot rate limit is exceeded."""
    def __init__(self, limit: int, window_seconds: int = 3600):
        self.limit = limit
        self.window_seconds = window_seconds
        super().__init__(f"Pilot rate limit exceeded: {limit} invoices per {window_seconds}s")


def check_pilot_rate_limit() -> None:
    """
    Check and update pilot rate limit counter.
    
    Raises:
        PilotRateLimitExceeded: If limit exceeded
    
    Note:
        Simple in-memory implementation. Resets on app restart.
        For production, consider Redis-based implementation.
    """
    global _pilot_invoice_timestamps
    
    max_invoices = _get_pilot_max_invoices_per_hour()
    window = timedelta(hours=1)
    now = datetime.now(timezone.utc)
    cutoff = now - window
    
    # Clean old timestamps
    _pilot_invoice_timestamps = [ts for ts in _pilot_invoice_timestamps if ts > cutoff]
    
    # Check limit
    if len(_pilot_invoice_timestamps) >= max_invoices:
        logger.warning(f"Pilot rate limit exceeded: {len(_pilot_invoice_timestamps)}/{max_invoices} per hour")
        raise PilotRateLimitExceeded(max_invoices)
    
    # Record this request
    _pilot_invoice_timestamps.append(now)
    logger.debug(f"Pilot rate: {len(_pilot_invoice_timestamps)}/{max_invoices} per hour")


def get_pilot_rate_status() -> dict:
    """
    Get current pilot rate limit status.
    
    Returns:
        Dict with current count, limit, and remaining
    """
    global _pilot_invoice_timestamps
    
    max_invoices = _get_pilot_max_invoices_per_hour()
    window = timedelta(hours=1)
    cutoff = datetime.now(timezone.utc) - window
    
    # Clean and count
    _pilot_invoice_timestamps = [ts for ts in _pilot_invoice_timestamps if ts > cutoff]
    current = len(_pilot_invoice_timestamps)
    
    return {
        "current": current,
        "limit": max_invoices,
        "remaining": max(0, max_invoices - current),
        "window_seconds": 3600,
    }


def reset_pilot_rate_limit() -> None:
    """Reset pilot rate limit counter (for testing)."""
    global _pilot_invoice_timestamps
    _pilot_invoice_timestamps = []


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP LOG
# ═══════════════════════════════════════════════════════════════════════════════

def log_pilot_config() -> None:
    """
    Log pilot configuration at startup.
    
    Call this in startup_event() after config validation.
    """
    enabled = _get_pilot_enabled()
    tenant_id = _get_pilot_tenant_id()
    max_invoices = _get_pilot_max_invoices_per_hour()
    
    if enabled:
        logger.info(
            f"Pilot mode ENABLED: tenant_id={tenant_id}, "
            f"max_invoices_per_hour={max_invoices}"
        )
    else:
        logger.warning("Pilot mode DISABLED via PILOT_ENABLED=false")


# ═══════════════════════════════════════════════════════════════════════════════
# GUARD DECORATOR (Optional)
# ═══════════════════════════════════════════════════════════════════════════════

def pilot_guard(func):
    """
    Decorator to guard pilot-only functions.
    
    If pilot is disabled, returns None without executing.
    
    Usage:
        @pilot_guard
        def process_pilot_invoice(tenant_id, ...):
            ...
    """
    from functools import wraps
    
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_pilot_enabled():
            logger.info(f"Pilot disabled, skipping {func.__name__}")
            return None
        return func(*args, **kwargs)
    
    return wrapper
