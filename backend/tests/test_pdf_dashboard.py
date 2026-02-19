"""
Tests for PDF Worker Dashboard (Task 5).

PD1) JSON structure — uid, title, panels, schemaVersion
PD2) Required panel types — timeseries, barchart, stat
PD3) Panel targets — each panel has expr with correct metric names
PD4) Panel descriptions — each panel has description with runbook link
PD5) Stacking mode — Jobs by Status panel uses stacked mode
PD6) Metric names — all expected metrics referenced
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DASHBOARD_PATH = Path(__file__).resolve().parent.parent.parent / "monitoring" / "grafana" / "pdf-worker-dashboard.json"


@pytest.fixture(scope="module")
def dashboard():
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════════════════════
# PD1) JSON structure
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardStructure:
    def test_has_uid(self, dashboard):
        assert dashboard["uid"] == "pdf-worker-telemetry"

    def test_has_title(self, dashboard):
        assert "PDF" in dashboard["title"]

    def test_has_panels(self, dashboard):
        assert isinstance(dashboard["panels"], list)
        assert len(dashboard["panels"]) == 4

    def test_has_schema_version(self, dashboard):
        assert dashboard["schemaVersion"] >= 36


# ═══════════════════════════════════════════════════════════════════════════════
# PD2) Required panel types
# ═══════════════════════════════════════════════════════════════════════════════


class TestPanelTypes:
    def test_has_timeseries(self, dashboard):
        types = {p["type"] for p in dashboard["panels"]}
        assert "timeseries" in types

    def test_has_barchart(self, dashboard):
        types = {p["type"] for p in dashboard["panels"]}
        assert "barchart" in types

    def test_has_stat(self, dashboard):
        types = {p["type"] for p in dashboard["panels"]}
        assert "stat" in types


# ═══════════════════════════════════════════════════════════════════════════════
# PD3) Panel targets — correct metric names
# ═══════════════════════════════════════════════════════════════════════════════


class TestPanelTargets:
    def test_each_panel_has_targets(self, dashboard):
        for panel in dashboard["panels"]:
            assert "targets" in panel, f"Panel '{panel['title']}' missing targets"
            assert len(panel["targets"]) > 0

    def test_jobs_by_status_uses_pdf_jobs_total(self, dashboard):
        panel = dashboard["panels"][0]
        assert panel["title"] == "Jobs by Status"
        expr = panel["targets"][0]["expr"]
        assert "ptf_admin_pdf_jobs_total" in expr

    def test_failures_uses_pdf_job_failures_total(self, dashboard):
        panel = dashboard["panels"][1]
        assert panel["title"] == "Failures by Error Code"
        expr = panel["targets"][0]["expr"]
        assert "ptf_admin_pdf_job_failures_total" in expr

    def test_duration_uses_pdf_job_duration_seconds(self, dashboard):
        panel = dashboard["panels"][2]
        assert panel["title"] == "Render Duration (p50 / p95)"
        assert any("ptf_admin_pdf_job_duration_seconds" in t["expr"] for t in panel["targets"])

    def test_queue_depth_uses_pdf_queue_depth(self, dashboard):
        panel = dashboard["panels"][3]
        assert panel["title"] == "Queue Depth"
        expr = panel["targets"][0]["expr"]
        assert "ptf_admin_pdf_queue_depth" in expr


# ═══════════════════════════════════════════════════════════════════════════════
# PD4) Panel descriptions with runbook link
# ═══════════════════════════════════════════════════════════════════════════════


class TestPanelDescriptions:
    def test_each_panel_has_description(self, dashboard):
        for panel in dashboard["panels"]:
            assert "description" in panel, f"Panel '{panel['title']}' missing description"
            assert len(panel["description"]) > 0

    def test_each_panel_has_runbook_link(self, dashboard):
        for panel in dashboard["panels"]:
            assert "runbook" in panel["description"].lower(), (
                f"Panel '{panel['title']}' missing runbook link in description"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PD5) Stacking mode
# ═══════════════════════════════════════════════════════════════════════════════


class TestStackingMode:
    def test_jobs_by_status_stacked(self, dashboard):
        panel = dashboard["panels"][0]
        stacking = panel["fieldConfig"]["defaults"]["custom"]["stacking"]["mode"]
        assert stacking == "normal"


# ═══════════════════════════════════════════════════════════════════════════════
# PD6) All expected metrics referenced
# ═══════════════════════════════════════════════════════════════════════════════


class TestMetricCoverage:
    def test_all_metrics_referenced(self, dashboard):
        all_exprs = " ".join(
            t["expr"] for p in dashboard["panels"] for t in p["targets"]
        )
        expected = [
            "ptf_admin_pdf_jobs_total",
            "ptf_admin_pdf_job_failures_total",
            "ptf_admin_pdf_job_duration_seconds",
            "ptf_admin_pdf_queue_depth",
        ]
        for metric in expected:
            assert metric in all_exprs, f"Metric {metric} not found in dashboard"
