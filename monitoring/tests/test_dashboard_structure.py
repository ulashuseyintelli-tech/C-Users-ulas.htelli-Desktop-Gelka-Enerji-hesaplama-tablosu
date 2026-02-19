"""
Structural validation tests for Grafana dashboard JSON.

Feature: observability-pack
"""

import json
import re

import pytest

# ── Unit Tests ─────────────────────────────────────────────────────


class TestDashboardTopLevel:
    """Dashboard JSON top-level structure validation."""

    REQUIRED_KEYS = {"title", "uid", "panels", "templating", "time", "refresh"}

    def test_json_parses(self, dashboard):
        """Dashboard JSON is valid and parseable."""
        assert isinstance(dashboard, dict)

    def test_required_keys_present(self, dashboard):
        """All required top-level keys exist.
        Validates: Requirements 5.1"""
        missing = self.REQUIRED_KEYS - dashboard.keys()
        assert not missing, f"Missing keys: {missing}"

    def test_uid(self, dashboard):
        assert dashboard["uid"] == "ptf-admin-overview"

    def test_title(self, dashboard):
        assert dashboard["title"] == "PTF Admin Overview"

    def test_time_range(self, dashboard):
        """Default time range is now-1h → now.
        Validates: Requirements 5.4"""
        assert dashboard["time"] == {"from": "now-1h", "to": "now"}

    def test_refresh(self, dashboard):
        """Auto-refresh is 30s.
        Validates: Requirements 5.4"""
        assert dashboard["refresh"] == "30s"


class TestDashboardTemplating:
    """Datasource and job variable validation."""

    def test_datasource_variable(self, dashboard):
        """$datasource variable exists and is prometheus type.
        Validates: Requirements 5.2"""
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        assert "datasource" in variables
        ds = variables["datasource"]
        assert ds["type"] == "datasource"
        assert ds["query"] == "prometheus"

    def test_job_variable(self, dashboard):
        """$job variable exists and queries label_values.
        Validates: Requirements 5.2"""
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        assert "job" in variables
        job = variables["job"]
        assert "label_values" in job.get("query", "")


class TestDashboardRows:
    """Row structure and panel count validation."""

    EXPECTED_ROWS = {
        "API Traffic & Health": 4,
        "Import / Upsert Business Health": 3,
        "Lookup / History": 3,
        "Frontend Telemetry": 2,
        "Dependency Health": 4,
        "Guard Decision Layer": 5,
    }

    def _get_rows(self, dashboard):
        return [p for p in dashboard["panels"] if p.get("type") == "row"]

    def test_row_count(self, dashboard):
        """Dashboard has exactly 6 rows."""
        rows = self._get_rows(dashboard)
        assert len(rows) == 6

    def test_all_rows_collapsed(self, dashboard):
        """All rows are collapsible (collapsed=true).
        Validates: Requirements 5.3"""
        for row in self._get_rows(dashboard):
            assert row.get("collapsed") is True, f"Row '{row['title']}' not collapsed"

    @pytest.mark.parametrize("row_title,min_panels", list(EXPECTED_ROWS.items()))
    def test_row_panel_count(self, dashboard, row_title, min_panels):
        """Each row has at least the minimum required panels.
        Validates: Requirements 1.1, 2.1, 3.1, 4.1"""
        rows = self._get_rows(dashboard)
        row = next((r for r in rows if r["title"] == row_title), None)
        assert row is not None, f"Row '{row_title}' not found"
        actual = len(row.get("panels", []))
        assert actual >= min_panels, f"Row '{row_title}': expected >= {min_panels}, got {actual}"


class TestDashboardPromQL:
    """PromQL expression validation for each panel."""

    def _all_panels(self, dashboard):
        """Flatten all panels from all rows."""
        panels = []
        for item in dashboard["panels"]:
            if item.get("type") == "row":
                panels.extend(item.get("panels", []))
            else:
                panels.append(item)
        return panels

    def _find_panel(self, dashboard, title_substring):
        for p in self._all_panels(dashboard):
            if title_substring.lower() in p.get("title", "").lower():
                return p
        return None

    def _get_expr(self, panel):
        if not panel or "targets" not in panel:
            return ""
        return panel["targets"][0].get("expr", "")

    # Row 1: API Traffic & Health
    def test_request_rate_query(self, dashboard):
        """Validates: Requirements 1.2"""
        p = self._find_panel(dashboard, "Request Rate")
        expr = self._get_expr(p)
        assert "ptf_admin_api_request_total" in expr
        assert 'endpoint!="/metrics"' in expr

    def test_error_rate_query(self, dashboard):
        """Validates: Requirements 1.3"""
        p = self._find_panel(dashboard, "Error Rate")
        expr = self._get_expr(p)
        assert "status_class" in expr
        assert "4xx" in expr and "5xx" in expr and "0xx" in expr

    def test_p95_latency_query(self, dashboard):
        """Validates: Requirements 1.4"""
        p = self._find_panel(dashboard, "P95 Latency")
        expr = self._get_expr(p)
        assert "histogram_quantile" in expr
        assert "ptf_admin_api_request_duration_seconds_bucket" in expr

    def test_self_exclude_check(self, dashboard):
        """Validates: Requirements 1.5"""
        p = self._find_panel(dashboard, "Self-Exclude")
        expr = self._get_expr(p)
        assert 'endpoint="/metrics"' in expr

    # Row 2: Import/Upsert
    def test_upsert_rate_query(self, dashboard):
        """Validates: Requirements 2.2"""
        p = self._find_panel(dashboard, "Upsert Rate")
        expr = self._get_expr(p)
        assert "ptf_admin_upsert_total" in expr

    def test_import_rows_query(self, dashboard):
        """Validates: Requirements 2.3"""
        p = self._find_panel(dashboard, "Rows Accepted")
        expr = self._get_expr(p)
        assert "ptf_admin_import_rows_total" in expr
        assert "outcome" in expr

    def test_import_duration_query(self, dashboard):
        """Validates: Requirements 2.4"""
        p = self._find_panel(dashboard, "Apply Duration")
        expr = self._get_expr(p)
        assert "ptf_admin_import_apply_duration_seconds_bucket" in expr

    # Row 3: Lookup / History
    def test_lookup_hit_miss_query(self, dashboard):
        """Validates: Requirements 3.2"""
        p = self._find_panel(dashboard, "Hit / Miss")
        expr = self._get_expr(p)
        assert "ptf_admin_lookup_total" in expr

    def test_history_query_rate(self, dashboard):
        """Validates: Requirements 3.3"""
        p = self._find_panel(dashboard, "History Query Rate")
        expr = self._get_expr(p)
        assert "ptf_admin_history_query_total" in expr

    def test_history_query_p95(self, dashboard):
        """Validates: Requirements 3.4"""
        p = self._find_panel(dashboard, "History Query P95")
        expr = self._get_expr(p)
        assert "ptf_admin_history_query_duration_seconds_bucket" in expr

    # Row 4: Frontend Telemetry
    def test_top_events_query(self, dashboard):
        """Validates: Requirements 4.2"""
        p = self._find_panel(dashboard, "Top Events")
        expr = self._get_expr(p)
        assert "ptf_admin_frontend_events_total" in expr
        assert "topk" in expr

    def test_telemetry_endpoint_health_query(self, dashboard):
        """Validates: Requirements 4.3"""
        p = self._find_panel(dashboard, "Telemetry Endpoint Health")
        expr = self._get_expr(p)
        assert "ptf_admin_api_request_total" in expr
        assert "/admin/telemetry/events" in expr


class TestSelfExcludeFilter:
    """Verify /metrics self-exclude across all API panels.
    Validates: Requirements 1.5"""

    METRICS_NAMES = {"ptf_admin_api_request_total", "ptf_admin_api_request_duration_seconds_bucket"}

    def test_api_panels_exclude_metrics_endpoint(self, dashboard):
        """All panels referencing API request metrics (except self-exclude check)
        must filter out endpoint="/metrics"."""
        rows = [p for p in dashboard["panels"] if p.get("type") == "row"]
        violations = []
        for row in rows:
            for panel in row.get("panels", []):
                if "self-exclude" in panel.get("title", "").lower():
                    continue
                for target in panel.get("targets", []):
                    expr = target.get("expr", "")
                    for metric in self.METRICS_NAMES:
                        if metric in expr and 'endpoint!="/metrics"' not in expr:
                            # Panels that filter to a specific endpoint are OK
                            if 'endpoint="' in expr:
                                continue
                            violations.append(f"{panel['title']}: {metric} without /metrics exclusion")
        assert not violations, f"Self-exclude violations: {violations}"
