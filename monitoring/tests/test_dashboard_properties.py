"""
Property-based tests for Grafana dashboard structural invariants.

Feature: observability-pack
Properties: P1 (Row Panel Count), P2 (Self-Exclude), P3 (JSON Validity), P4 (Collapsible Rows)

Uses Hypothesis to iterate over all dashboard elements and verify universal properties.
"""

import json
import re

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from .conftest import DASHBOARD_PATH

# ── Helpers ────────────────────────────────────────────────────────

def _load_dashboard():
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_rows(dashboard):
    return [p for p in dashboard["panels"] if p.get("type") == "row"]


def _all_panels(dashboard):
    panels = []
    for item in dashboard["panels"]:
        if item.get("type") == "row":
            panels.extend(item.get("panels", []))
        else:
            panels.append(item)
    return panels


DASHBOARD = _load_dashboard()
ROWS = _get_rows(DASHBOARD)
ALL_PANELS = _all_panels(DASHBOARD)

# Row title → minimum panel count (from design)
ROW_MIN_PANELS = {
    "API Traffic & Health": 4,
    "Import / Upsert Business Health": 3,
    "Lookup / History": 3,
    "Frontend Telemetry": 2,
    "Dependency Health": 4,
}

REQUIRED_TOP_KEYS = {"title", "uid", "panels", "templating", "time", "refresh"}

METRICS_NEEDING_EXCLUDE = {
    "ptf_admin_api_request_total",
    "ptf_admin_api_request_duration_seconds_bucket",
}


# ── Property 1: Dashboard Row Panel Sayısı ─────────────────────────

class TestPropertyRowPanelCount:
    """P1: For any dashboard row, panel count >= minimum specified.
    Validates: Requirements 1.1, 2.1, 3.1, 4.1"""

    @given(row_idx=st.sampled_from(list(range(len(ROWS)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_row_has_minimum_panels(self, row_idx):
        row = ROWS[row_idx]
        title = row["title"]
        min_expected = ROW_MIN_PANELS.get(title)
        assert min_expected is not None, f"Unexpected row: {title}"
        actual = len(row.get("panels", []))
        assert actual >= min_expected, (
            f"Row '{title}': expected >= {min_expected} panels, got {actual}"
        )


# ── Property 2: Self-Exclude Doğrulaması ───────────────────────────

class TestPropertySelfExclude:
    """P2: For any PromQL referencing API request metrics (except self-exclude panel),
    the expression must filter out endpoint="/metrics".
    Validates: Requirements 1.5"""

    @given(panel_idx=st.sampled_from(list(range(len(ALL_PANELS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_api_metric_panels_exclude_metrics_endpoint(self, panel_idx):
        panel = ALL_PANELS[panel_idx]
        if "self-exclude" in panel.get("title", "").lower():
            return  # self-exclude check panel is exempt

        for target in panel.get("targets", []):
            expr = target.get("expr", "")
            for metric in METRICS_NEEDING_EXCLUDE:
                if metric in expr:
                    # Either has explicit exclusion or targets a specific endpoint
                    has_exclusion = 'endpoint!="/metrics"' in expr
                    has_specific_endpoint = 'endpoint="' in expr
                    assert has_exclusion or has_specific_endpoint, (
                        f"Panel '{panel['title']}': {metric} without /metrics exclusion"
                    )


# ── Property 3: Dashboard JSON Geçerliliği ─────────────────────────

class TestPropertyDashboardValidity:
    """P3: Dashboard JSON parses successfully and contains required top-level keys.
    Validates: Requirements 5.1"""

    def test_json_parses_and_has_required_keys(self):
        assert isinstance(DASHBOARD, dict)
        missing = REQUIRED_TOP_KEYS - DASHBOARD.keys()
        assert not missing, f"Missing top-level keys: {missing}"

    @given(key=st.sampled_from(sorted(REQUIRED_TOP_KEYS)))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_each_required_key_present(self, key):
        assert key in DASHBOARD, f"Required key '{key}' missing from dashboard"


# ── Property 4: Collapsible Row Yapısı ─────────────────────────────

class TestPropertyCollapsibleRows:
    """P4: For any row panel, collapsed == true.
    Validates: Requirements 5.3"""

    @given(row_idx=st.sampled_from(list(range(len(ROWS)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_rows_collapsed(self, row_idx):
        row = ROWS[row_idx]
        assert row.get("collapsed") is True, (
            f"Row '{row['title']}' is not collapsed"
        )
