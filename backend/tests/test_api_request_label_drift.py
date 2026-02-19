"""
Label cardinality drift guard for ptf_admin_api_request_total.

Spec contract: ptf_admin_api_request_total uses ONLY {endpoint, method, status_class}.
If anyone adds a `route` label (or any other label), this test fails — preventing
cardinality drift before it reaches production.

Feature: telemetry-unification, Requirement 3.3
Validates: Label set is exactly {endpoint, method, status_class} — no more, no less.
"""
from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from app.ptf_metrics import PTFMetrics


# ── Allowed label set (frozen contract) ──────────────────────────────────

ALLOWED_LABELS = frozenset({"endpoint", "method", "status_class"})


# ── Helpers ──────────────────────────────────────────────────────────────

def _extract_api_request_labels(prom_output: str) -> list[set[str]]:
    """Parse /metrics output and extract label names from ptf_admin_api_request_total lines."""
    label_sets: list[set[str]] = []
    for line in prom_output.split("\n"):
        if line.startswith("ptf_admin_api_request_total{"):
            # Extract label keys from {key="val",key2="val2",...}
            brace_content = line.split("{", 1)[1].split("}", 1)[0]
            keys = set(re.findall(r'(\w+)=', brace_content))
            label_sets.append(keys)
    return label_sets


# ── Unit tests ───────────────────────────────────────────────────────────

class TestApiRequestLabelContract:
    """ptf_admin_api_request_total label set must be exactly {endpoint, method, status_class}."""

    def test_label_set_exact_match(self):
        """After incrementing, label names in /metrics output are exactly the allowed set."""
        m = PTFMetrics()
        m.inc_api_request("/health", "GET", 200)
        output = m.generate_metrics().decode()

        label_sets = _extract_api_request_labels(output)
        assert len(label_sets) >= 1, "Expected at least one api_request_total sample"
        for labels in label_sets:
            assert labels == ALLOWED_LABELS, (
                f"Label drift detected! Expected {ALLOWED_LABELS}, got {labels}"
            )

    def test_route_label_absent(self):
        """The 'route' label must never appear in api_request_total output."""
        m = PTFMetrics()
        m.inc_api_request("/admin/market-prices/{period}", "GET", 200)
        m.inc_api_request("/health", "GET", 200)
        m.inc_api_request("/admin/market-prices", "POST", 201)
        output = m.generate_metrics().decode()

        # Strict check: 'route=' must not appear in any api_request_total line
        for line in output.split("\n"):
            if "ptf_admin_api_request_total" in line and not line.startswith("#"):
                assert "route=" not in line, (
                    f"Cardinality drift: 'route' label found in: {line}"
                )

    def test_no_extra_labels_across_status_classes(self):
        """All status classes produce the same label set — no hidden labels."""
        m = PTFMetrics()
        for code in [200, 301, 404, 500, 0]:
            m.inc_api_request("/test", "GET", code)
        output = m.generate_metrics().decode()

        label_sets = _extract_api_request_labels(output)
        assert len(label_sets) >= 3, "Expected samples for multiple status classes"
        for labels in label_sets:
            assert labels == ALLOWED_LABELS


# ── Property test: label set invariant under arbitrary traffic ───────────

@given(
    endpoint=st.sampled_from([
        "/health", "/admin/market-prices", "/admin/market-prices/{period}",
        "/admin/telemetry/events", "/metrics", "/admin/ops/kill-switches",
    ]),
    method=st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH"]),
    status_code=st.integers(min_value=0, max_value=599),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_api_request_label_set_invariant(endpoint, method, status_code):
    """
    Property: For ANY combination of endpoint, method, and status_code,
    ptf_admin_api_request_total label set is exactly {endpoint, method, status_class}.
    No route, path, tenant, user_id, or any other label ever appears.

    Feature: telemetry-unification, Label Cardinality Guard
    Validates: Requirements 3.3, ops-guard Label Policy
    """
    m = PTFMetrics()
    m.inc_api_request(endpoint, method, status_code)
    output = m.generate_metrics().decode()

    label_sets = _extract_api_request_labels(output)
    for labels in label_sets:
        assert labels == ALLOWED_LABELS, (
            f"Label drift! endpoint={endpoint}, method={method}, "
            f"status_code={status_code} → labels={labels}"
        )
