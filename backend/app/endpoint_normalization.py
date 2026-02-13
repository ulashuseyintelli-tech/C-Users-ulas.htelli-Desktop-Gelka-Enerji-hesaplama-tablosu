"""
Endpoint Normalization — single source of truth for endpoint labels.

Every component (MetricsMiddleware, OpsGuardMiddleware, audit log, kill-switch
scope matching) MUST use this module to produce endpoint labels.

3-level fallback (same as MetricsMiddleware, now centralized):
  L1: route.path template  (e.g. /admin/market-prices/{period})
  L2: sanitized bucket     (first 2 segments + /*)
  L3: unmatched:{sanitized} for 404s / unknown routes

Canonicalization rules:
  - Numeric segments → :id
  - UUID segments → :uuid
  - Hex tokens (≥16 chars) → :token
  - Max 6 segments; beyond → ...
  - Raw path NEVER enters a metric label

Cardinality contract (HD-5):
  - endpoint length ≤ 120 chars
  - charset: [a-zA-Z0-9_:/.{*}-]
  - segment count ≤ 6

Feature: ops-guard, Task 3.1
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from starlette.requests import Request


class NormalizationLevel(str, Enum):
    """Which fallback level produced the endpoint label."""
    L1_ROUTE = "L1_ROUTE"
    L2_SANITIZED = "L2_SANITIZED"
    L3_UNMATCHED = "L3_UNMATCHED"


class EndpointClass(str, Enum):
    """Endpoint risk classification for kill-switch failure semantics (HD-1)."""
    HIGH_RISK = "high_risk"
    STANDARD = "standard"


@dataclass(frozen=True)
class NormalizedEndpoint:
    """Immutable normalized endpoint result."""
    template: str
    level: NormalizationLevel
    method: str
    endpoint_class: EndpointClass


# ── Canonicalization patterns ─────────────────────────────────────────────────

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
_LABEL_CHARSET_RE = re.compile(r"^[a-zA-Z0-9_:/.{*}-]+$")
_MAX_LABEL_LENGTH = 120
_MAX_SEGMENTS = 6

# ── High-risk endpoint patterns (HD-1) ───────────────────────────────────────

_HIGH_RISK_TEMPLATES = frozenset({
    "/admin/market-prices/import/apply",
    "/admin/market-prices/import/preview",
})


def _is_high_risk_template(template: str, method: str) -> bool:
    """Check if endpoint is high-risk (bulk import / destructive write)."""
    if template in _HIGH_RISK_TEMPLATES:
        return True
    # POST to import-like paths
    if method.upper() == "POST" and "/import/" in template:
        return True
    return False


# ── Canonicalization ──────────────────────────────────────────────────────────

def _canonicalize_segment(segment: str) -> str:
    """Replace dynamic segments with bounded placeholders."""
    if _UUID_RE.match(segment):
        return ":uuid"
    if _NUMERIC_RE.match(segment):
        return ":id"
    if _HEX_TOKEN_RE.match(segment):
        return ":token"
    return segment


def sanitize_path(path: str) -> str:
    """
    Reduce path to first 2 segments + wildcard for low cardinality.

    This is the centralized version of MetricsMiddleware._sanitize_path().
    """
    segments = [s for s in path.split("/") if s]
    if len(segments) <= 2:
        return path
    return "/" + "/".join(segments[:2]) + "/*"


def canonicalize_path(path: str) -> str:
    """
    Full canonicalization: replace dynamic segments, enforce max depth.

    Used for L2 fallback when route template is unavailable.
    """
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "/"

    # Canonicalize each segment
    canonical = [_canonicalize_segment(s) for s in segments]

    # Enforce max segment limit
    if len(canonical) > _MAX_SEGMENTS:
        canonical = canonical[:_MAX_SEGMENTS] + ["..."]

    result = "/" + "/".join(canonical)

    # Enforce max length
    if len(result) > _MAX_LABEL_LENGTH:
        result = result[:_MAX_LABEL_LENGTH - 3] + "..."

    return result


def validate_label(label: str) -> str:
    """
    Validate and enforce cardinality contract on a label value.

    Returns the label if valid, or a safe fallback if not.
    Does NOT raise — fail-open for label validation (HD-5).
    """
    if len(label) > _MAX_LABEL_LENGTH:
        label = label[:_MAX_LABEL_LENGTH - 3] + "..."

    if not _LABEL_CHARSET_RE.match(label):
        # Strip invalid chars, keep safe subset
        label = re.sub(r"[^a-zA-Z0-9_:/.{*}-]", "", label)
        if not label:
            label = "unknown"

    return label


# ── Main normalization function ───────────────────────────────────────────────

def normalize_endpoint(request: Request, status_code: Optional[int] = None) -> NormalizedEndpoint:
    """
    Single source of truth for endpoint label normalization.

    Uses 3-level fallback:
      L1: route.path template from FastAPI scope
      L2: canonicalized path (dynamic segments replaced)
      L3: unmatched:{sanitized} for 404s

    Args:
        request: Starlette Request object
        status_code: HTTP response status code (None if no response yet)

    Returns:
        NormalizedEndpoint with template, level, method, endpoint_class
    """
    method = request.method.upper()
    route = request.scope.get("route")

    if route and hasattr(route, "path"):
        # L1: route template available
        template = validate_label(route.path)
        level = NormalizationLevel.L1_ROUTE
    elif status_code == 404:
        # L3: unmatched route (404)
        sanitized = sanitize_path(request.url.path)
        template = validate_label(f"unmatched:{sanitized}")
        level = NormalizationLevel.L3_UNMATCHED
    else:
        # L2: canonicalize the raw path
        canonical = canonicalize_path(request.url.path)
        template = validate_label(canonical)
        level = NormalizationLevel.L2_SANITIZED

    endpoint_class = (
        EndpointClass.HIGH_RISK
        if _is_high_risk_template(template, method)
        else EndpointClass.STANDARD
    )

    return NormalizedEndpoint(
        template=template,
        level=level,
        method=method,
        endpoint_class=endpoint_class,
    )


def normalize_endpoint_from_path(path: str, method: str = "GET") -> NormalizedEndpoint:
    """
    Normalize from raw path string (no request object needed).

    Useful for kill-switch scope matching and config-time classification.
    Always produces L2 level (no route template available).
    """
    canonical = canonicalize_path(path)
    template = validate_label(canonical)

    endpoint_class = (
        EndpointClass.HIGH_RISK
        if _is_high_risk_template(template, method)
        else EndpointClass.STANDARD
    )

    return NormalizedEndpoint(
        template=template,
        level=NormalizationLevel.L2_SANITIZED,
        method=method.upper(),
        endpoint_class=endpoint_class,
    )
