"""
Structural validation for the PTF Migration Phase 2 Grafana dashboard.

Feature: ptf-sot-unification, Task T2.3

Mirrors the conventions from `test_dashboard_structure.py` (observability-pack)
without conflating the two dashboards in shared fixtures — the migration
dashboard is intentionally separate so a Phase 2 panel addition doesn't
need to touch the long-lived ptf-admin-overview.

Validates:
  - Top-level keys: title, uid, panels, templating, time, refresh.
  - UID is `ptf-migration-overview` (steady — referenced by Grafana URLs
    and runbook links). UID drift here would break operator bookmarks.
  - Templating defines a `period` variable bound to the new metric.
  - All four T2.3 panels exist with the correct expression strings —
    these are the strings Prometheus actually evaluates, so a typo would
    silently produce empty panels in production.
  - PromQL queries reference ONLY the new metrics introduced in T2.3
    (`ptf_drift_observed_total`, `ptf_canonical_monthly_avg`). The
    dashboard MUST NOT depend on ptf_admin_* metrics — those belong to
    a different dashboard concern.

This is a static check (no live Prometheus); it ensures the JSON loads,
parses, and contains the contract surface the runbook describes.
"""

from __future__ import annotations

import json
import pathlib

import pytest

DASHBOARD_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "grafana"
    / "ptf-migration-dashboard.json"
)


@pytest.fixture(scope="module")
def dashboard():
    """Load and parse the migration dashboard JSON."""
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Top-level structure
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardTopLevel:
    REQUIRED_KEYS = {"title", "uid", "panels", "templating", "time", "refresh"}

    def test_json_parses(self, dashboard):
        assert isinstance(dashboard, dict)

    def test_required_keys_present(self, dashboard):
        missing = self.REQUIRED_KEYS - dashboard.keys()
        assert not missing, f"Missing keys: {missing}"

    def test_uid(self, dashboard):
        # UID is referenced by Grafana URLs and operator bookmarks. Don't
        # change without updating the runbook.
        assert dashboard["uid"] == "ptf-migration-overview"

    def test_title(self, dashboard):
        assert "PTF Migration" in dashboard["title"]

    def test_phase_2_tag(self, dashboard):
        assert "phase-2" in dashboard.get("tags", [])

    def test_default_time_range_24h(self, dashboard):
        # Drift analysis windows are typically multi-hour; 24h gives a
        # full day of dual-read traffic at default zoom.
        assert dashboard["time"] == {"from": "now-24h", "to": "now"}

    def test_refresh(self, dashboard):
        assert dashboard["refresh"] == "30s"


# ─────────────────────────────────────────────────────────────────────────────
# Templating
# ─────────────────────────────────────────────────────────────────────────────


class TestDashboardTemplating:
    def test_datasource_variable(self, dashboard):
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        assert "datasource" in variables
        assert variables["datasource"]["type"] == "datasource"
        assert variables["datasource"]["query"] == "prometheus"

    def test_period_variable_bound_to_drift_metric(self, dashboard):
        """The period selector pulls labels from ptf_drift_observed_total."""
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        assert "period" in variables
        period_var = variables["period"]
        assert "label_values" in period_var.get("query", "")
        assert "ptf_drift_observed_total" in period_var.get("query", "")
        # period is multi-select so operators can compare adjacent periods
        assert period_var.get("multi") is True
        assert period_var.get("includeAll") is True


# ─────────────────────────────────────────────────────────────────────────────
# Panels
# ─────────────────────────────────────────────────────────────────────────────


def _flatten_panels(panels):
    """Walk row-collapsed panels too (Grafana nests them under `panels`)."""
    out = []
    for p in panels:
        out.append(p)
        if p.get("type") == "row" and "panels" in p:
            out.extend(p["panels"])
    return out


def _panel_expressions(panel):
    return [t.get("expr", "") for t in panel.get("targets", [])]


class TestDashboardPanels:
    def test_four_data_panels(self, dashboard):
        """Drift Rate, Drift Total, Canonical Avg, Severity Distribution."""
        panels = _flatten_panels(dashboard["panels"])
        non_row = [p for p in panels if p.get("type") != "row"]
        assert len(non_row) == 4, (
            f"Expected 4 data panels, got {len(non_row)}: "
            f"{[p.get('title') for p in non_row]}"
        )

    def test_drift_rate_panel(self, dashboard):
        panels = _flatten_panels(dashboard["panels"])
        rate_panels = [p for p in panels if p.get("title") == "Drift Rate by Severity"]
        assert len(rate_panels) == 1
        exprs = _panel_expressions(rate_panels[0])
        assert any("rate(ptf_drift_observed_total" in e for e in exprs)
        assert any("by (severity)" in e for e in exprs)

    def test_drift_total_panel(self, dashboard):
        panels = _flatten_panels(dashboard["panels"])
        total_panels = [
            p for p in panels if p.get("title") == "Drift Total by Period (24h)"
        ]
        assert len(total_panels) == 1
        exprs = _panel_expressions(total_panels[0])
        assert any("increase(ptf_drift_observed_total" in e for e in exprs)
        assert any("by (period, severity)" in e for e in exprs)

    def test_canonical_avg_panel(self, dashboard):
        panels = _flatten_panels(dashboard["panels"])
        avg_panels = [
            p for p in panels
            if p.get("title") == "Canonical Monthly Avg PTF (TL/MWh)"
        ]
        assert len(avg_panels) == 1
        exprs = _panel_expressions(avg_panels[0])
        assert any("ptf_canonical_monthly_avg" in e for e in exprs)

    def test_severity_distribution_panel(self, dashboard):
        panels = _flatten_panels(dashboard["panels"])
        sev_panels = [
            p for p in panels if p.get("title") == "Severity Distribution (last 24h)"
        ]
        assert len(sev_panels) == 1
        # Panel type should be a piechart (donut configuration uses options)
        assert sev_panels[0].get("type") == "piechart"


class TestDashboardMetricIsolation:
    """The migration dashboard should not depend on unrelated namespaces."""

    def test_no_ptf_admin_metric_dependency(self, dashboard):
        """All PromQL expressions reference only the T2.3 metrics.

        If a future panel reaches into ptf_admin_* it should live on the
        ptf-admin-overview dashboard instead.
        """
        panels = _flatten_panels(dashboard["panels"])
        all_exprs = []
        for p in panels:
            all_exprs.extend(_panel_expressions(p))
        # Templating queries also count.
        for v in dashboard["templating"]["list"]:
            q = v.get("query", "")
            if isinstance(q, str):
                all_exprs.append(q)
        for e in all_exprs:
            assert "ptf_admin_" not in e, (
                f"Migration dashboard should not depend on ptf_admin_* metric: {e}"
            )

    def test_only_t2_3_metrics_referenced(self, dashboard):
        """Every PromQL expression mentions one of the two T2.3 metrics."""
        allowed_metrics = {"ptf_drift_observed_total", "ptf_canonical_monthly_avg"}
        panels = _flatten_panels(dashboard["panels"])
        for p in panels:
            for e in _panel_expressions(p):
                if not e.strip():
                    continue
                assert any(m in e for m in allowed_metrics), (
                    f"Panel {p.get('title')!r} expression does not reference "
                    f"a T2.3 metric: {e}"
                )
