"""
Unit + Property-Based tests for endpoint normalization.

Feature: ops-guard, Task 3.2 + 3.3

Test matrix:
  - L1 route template passthrough
  - L2 sanitize: numeric → :id, UUID → :uuid, hex token → :token
  - L3 unmatched prefix
  - Determinism: same input → same output
  - Boundedness: random path → label bounded + regex compliant
  - No raw identifiers leak
  - Cardinality contract enforcement
  - High-risk endpoint classification
"""

import re
import string
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.endpoint_normalization import (
    NormalizationLevel,
    EndpointClass,
    NormalizedEndpoint,
    canonicalize_path,
    sanitize_path,
    validate_label,
    normalize_endpoint,
    normalize_endpoint_from_path,
    _LABEL_CHARSET_RE,
    _MAX_LABEL_LENGTH,
    _MAX_SEGMENTS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_request(path: str, method: str = "GET", route_path: str = None):
    """Create a mock Starlette Request."""
    req = MagicMock()
    req.method = method
    req.url.path = path
    if route_path:
        route = MagicMock()
        route.path = route_path
        req.scope = {"route": route}
    else:
        req.scope = {}
    return req


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ══════════════════════════════════════════════════════════════════════════════


class TestL1RouteTemplate:
    """L1: route template available → passthrough."""

    def test_simple_route_template(self):
        req = _make_request("/admin/market-prices", route_path="/admin/market-prices")
        result = normalize_endpoint(req, 200)
        assert result.template == "/admin/market-prices"
        assert result.level == NormalizationLevel.L1_ROUTE

    def test_parameterized_route_template(self):
        req = _make_request("/admin/market-prices/2024-01", route_path="/admin/market-prices/{period}")
        result = normalize_endpoint(req, 200)
        assert result.template == "/admin/market-prices/{period}"
        assert result.level == NormalizationLevel.L1_ROUTE

    def test_route_template_preserved_exactly(self):
        req = _make_request("/customers/42", route_path="/customers/{customer_id}")
        result = normalize_endpoint(req, 200)
        assert result.template == "/customers/{customer_id}"


class TestL2Sanitize:
    """L2: no route template → canonicalize dynamic segments."""

    def test_numeric_segment_replaced(self):
        result = canonicalize_path("/v1/items/123")
        assert result == "/v1/items/:id"

    def test_uuid_segment_replaced(self):
        result = canonicalize_path("/v1/items/550e8400-e29b-41d4-a716-446655440000")
        assert result == "/v1/items/:uuid"

    def test_hex_token_replaced(self):
        result = canonicalize_path("/v1/auth/abcdef0123456789abcdef")
        assert result == "/v1/auth/:token"

    def test_short_hex_not_replaced(self):
        """Hex strings < 16 chars are NOT tokens."""
        result = canonicalize_path("/v1/auth/abcdef01")
        assert result == "/v1/auth/abcdef01"

    def test_mixed_dynamic_segments(self):
        result = canonicalize_path("/admin/market-prices/42/lock")
        assert result == "/admin/market-prices/:id/lock"

    def test_static_path_unchanged(self):
        result = canonicalize_path("/health")
        assert result == "/health"

    def test_root_path(self):
        result = canonicalize_path("/")
        assert result == "/"

    def test_max_segments_enforced(self):
        path = "/a/b/c/d/e/f/g/h/i"
        result = canonicalize_path(path)
        segments = [s for s in result.split("/") if s]
        # 6 real segments + "..."
        assert len(segments) <= _MAX_SEGMENTS + 1
        assert segments[-1] == "..."


class TestL3Unmatched:
    """L3: 404 → unmatched:{sanitized}."""

    def test_404_produces_unmatched_prefix(self):
        req = _make_request("/weird/unknown/deep/path")
        result = normalize_endpoint(req, 404)
        assert result.template.startswith("unmatched:")
        assert result.level == NormalizationLevel.L3_UNMATCHED

    def test_404_sanitized_to_2_segments(self):
        req = _make_request("/a/b/c/d/e")
        result = normalize_endpoint(req, 404)
        assert result.template == "unmatched:/a/b/*"


class TestSanitizePath:
    """sanitize_path backward compat with MetricsMiddleware._sanitize_path."""

    def test_short_path_unchanged(self):
        assert sanitize_path("/health") == "/health"
        assert sanitize_path("/admin/prices") == "/admin/prices"

    def test_long_path_truncated(self):
        assert sanitize_path("/admin/prices/2024-01/lock") == "/admin/prices/*"


class TestValidateLabel:
    """Cardinality contract enforcement."""

    def test_valid_label_unchanged(self):
        assert validate_label("/admin/market-prices") == "/admin/market-prices"

    def test_long_label_truncated(self):
        long_label = "/a" * 100
        result = validate_label(long_label)
        assert len(result) <= _MAX_LABEL_LENGTH

    def test_invalid_chars_stripped(self):
        result = validate_label("/path with spaces?query=1")
        assert " " not in result
        assert "?" not in result
        assert "=" not in result

    def test_empty_after_strip_returns_unknown(self):
        result = validate_label("   ")
        assert result == "unknown"


class TestEndpointClassification:
    """High-risk vs standard classification (HD-1)."""

    def test_import_apply_is_high_risk(self):
        req = _make_request(
            "/admin/market-prices/import/apply",
            method="POST",
            route_path="/admin/market-prices/import/apply",
        )
        result = normalize_endpoint(req, 200)
        assert result.endpoint_class == EndpointClass.HIGH_RISK

    def test_import_preview_is_high_risk(self):
        req = _make_request(
            "/admin/market-prices/import/preview",
            method="POST",
            route_path="/admin/market-prices/import/preview",
        )
        result = normalize_endpoint(req, 200)
        assert result.endpoint_class == EndpointClass.HIGH_RISK

    def test_get_market_prices_is_standard(self):
        req = _make_request(
            "/admin/market-prices",
            method="GET",
            route_path="/admin/market-prices",
        )
        result = normalize_endpoint(req, 200)
        assert result.endpoint_class == EndpointClass.STANDARD

    def test_health_is_standard(self):
        req = _make_request("/health", route_path="/health")
        result = normalize_endpoint(req, 200)
        assert result.endpoint_class == EndpointClass.STANDARD


class TestNormalizeFromPath:
    """normalize_endpoint_from_path for config-time classification."""

    def test_import_apply_classified(self):
        result = normalize_endpoint_from_path("/admin/market-prices/import/apply", "POST")
        assert result.endpoint_class == EndpointClass.HIGH_RISK
        assert result.level == NormalizationLevel.L2_SANITIZED

    def test_standard_path(self):
        result = normalize_endpoint_from_path("/admin/market-prices", "GET")
        assert result.endpoint_class == EndpointClass.STANDARD


# ══════════════════════════════════════════════════════════════════════════════
# PROPERTY-BASED TESTS (Hypothesis)
# ══════════════════════════════════════════════════════════════════════════════

# Bounded generators for determinism
_path_segment = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-_",
    min_size=1, max_size=12,
)
_uuid_segment = st.tuples(
    st.text(alphabet="0123456789abcdef", min_size=8, max_size=8),
    st.text(alphabet="0123456789abcdef", min_size=4, max_size=4),
    st.text(alphabet="0123456789abcdef", min_size=4, max_size=4),
    st.text(alphabet="0123456789abcdef", min_size=4, max_size=4),
    st.text(alphabet="0123456789abcdef", min_size=12, max_size=12),
).map(lambda t: f"{t[0]}-{t[1]}-{t[2]}-{t[3]}-{t[4]}")
_numeric_segment = st.integers(min_value=0, max_value=999999).map(str)
_hex_token_segment = st.text(
    alphabet="0123456789abcdef", min_size=16, max_size=32,
)

_dynamic_segment = st.one_of(_path_segment, _uuid_segment, _numeric_segment, _hex_token_segment)
_path_strategy = st.lists(_dynamic_segment, min_size=0, max_size=10).map(
    lambda segs: "/" + "/".join(segs) if segs else "/"
)
_method_strategy = st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH"])


class TestNormalizationProperties:
    """Property-based tests for endpoint normalization correctness."""

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_determinism(self, path: str):
        """Same input → same output (Property: determinism)."""
        r1 = canonicalize_path(path)
        r2 = canonicalize_path(path)
        assert r1 == r2

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_boundedness_length(self, path: str):
        """Output label length ≤ MAX_LABEL_LENGTH (Property: boundedness)."""
        result = validate_label(canonicalize_path(path))
        assert len(result) <= _MAX_LABEL_LENGTH

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_boundedness_charset(self, path: str):
        """Output label matches allowed charset (Property: cardinality contract)."""
        result = validate_label(canonicalize_path(path))
        assert _LABEL_CHARSET_RE.match(result), f"Invalid label: {result}"

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_boundedness_segments(self, path: str):
        """Output has ≤ MAX_SEGMENTS real segments (Property: bounded cardinality)."""
        result = canonicalize_path(path)
        segments = [s for s in result.split("/") if s and s != "..."]
        assert len(segments) <= _MAX_SEGMENTS

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_no_raw_numeric_ids(self, path: str):
        """No raw numeric IDs in output (Property: no raw identifiers)."""
        result = canonicalize_path(path)
        segments = [s for s in result.split("/") if s]
        for seg in segments:
            if seg not in (":id", ":uuid", ":token", "...", "*"):
                assert not re.match(r"^\d+$", seg), f"Raw numeric ID leaked: {seg}"

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_no_raw_uuids(self, path: str):
        """No raw UUIDs in output (Property: no raw identifiers)."""
        result = canonicalize_path(path)
        uuid_pattern = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        assert not uuid_pattern.search(result), f"Raw UUID leaked: {result}"

    @settings(max_examples=200)
    @given(path=_path_strategy)
    def test_no_raw_hex_tokens(self, path: str):
        """No raw hex tokens (≥16 chars) in output (Property: no raw identifiers)."""
        result = canonicalize_path(path)
        segments = [s for s in result.split("/") if s]
        for seg in segments:
            if seg not in (":id", ":uuid", ":token", "...", "*"):
                assert not re.match(r"^[0-9a-fA-F]{16,}$", seg), f"Raw hex token leaked: {seg}"

    @settings(max_examples=100)
    @given(path=_path_strategy, method=_method_strategy)
    def test_normalize_from_path_determinism(self, path: str, method: str):
        """normalize_endpoint_from_path is deterministic."""
        r1 = normalize_endpoint_from_path(path, method)
        r2 = normalize_endpoint_from_path(path, method)
        assert r1 == r2

    @settings(max_examples=100)
    @given(path=_path_strategy, method=_method_strategy)
    def test_endpoint_class_is_valid_enum(self, path: str, method: str):
        """endpoint_class is always a valid EndpointClass value."""
        result = normalize_endpoint_from_path(path, method)
        assert result.endpoint_class in (EndpointClass.HIGH_RISK, EndpointClass.STANDARD)

    @settings(max_examples=100)
    @given(path=_path_strategy, method=_method_strategy)
    def test_method_is_uppercased(self, path: str, method: str):
        """Method is always uppercased in output."""
        result = normalize_endpoint_from_path(path, method)
        assert result.method == method.upper()
