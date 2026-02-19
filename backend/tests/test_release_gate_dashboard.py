"""
Release Gate Telemetry dashboard yapısal doğrulama testleri.

Feature: release-gate-telemetry, Task 5.1 / 5.2
Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5
"""
import json
import pytest
from pathlib import Path

_DASHBOARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "monitoring" / "grafana" / "release-gate-dashboard.json"
)


@pytest.fixture(scope="module")
def dashboard():
    text = _DASHBOARD_PATH.read_text(encoding="utf-8")
    return json.loads(text)


class TestDashboardStructure:
    """Grafana dashboard JSON yapısal doğrulama."""

    def test_json_valid(self, dashboard):
        assert dashboard is not None

    def test_has_four_panels(self, dashboard):
        assert len(dashboard["panels"]) == 4

    def test_uid_set(self, dashboard):
        assert dashboard.get("uid") == "release-gate-telemetry"

    def test_title(self, dashboard):
        assert dashboard["title"] == "Release Gate Telemetry"

    def test_schema_version(self, dashboard):
        assert dashboard.get("schemaVersion", 0) >= 36

    def test_panel_ids_unique(self, dashboard):
        ids = [p["id"] for p in dashboard["panels"]]
        assert len(ids) == len(set(ids))

    def test_tags_include_release_gate(self, dashboard):
        assert "release-gate" in dashboard.get("tags", [])


class TestAllowDenyRatePanel:
    """Panel 1: Allow vs Deny Rate (timeseries, stacked)."""

    def test_panel_type(self, dashboard):
        panel = dashboard["panels"][0]
        assert panel["title"] == "Allow vs Deny Rate"
        assert panel["type"] == "timeseries"

    def test_query_uses_decision_total(self, dashboard):
        panel = dashboard["panels"][0]
        expr = panel["targets"][0]["expr"]
        assert "release_gate_decision_total" in expr
        assert "increase" in expr
        assert "sum by (decision)" in expr

    def test_stacking_enabled(self, dashboard):
        panel = dashboard["panels"][0]
        stacking = panel["fieldConfig"]["defaults"]["custom"].get("stacking", {})
        assert stacking.get("mode") == "normal"


class TestTopDenyReasonsPanel:
    """Panel 2: Top Deny Reasons (barchart, topk)."""

    def test_panel_type(self, dashboard):
        panel = dashboard["panels"][1]
        assert panel["title"] == "Top Deny Reasons"
        assert panel["type"] == "barchart"

    def test_query_uses_topk(self, dashboard):
        panel = dashboard["panels"][1]
        expr = panel["targets"][0]["expr"]
        assert "topk(10" in expr
        assert "release_gate_decision_total" in expr
        assert 'decision="DENY"' in expr


class TestContractBreachPanel:
    """Panel 3: Contract Breach Count (stat)."""

    def test_panel_type(self, dashboard):
        panel = dashboard["panels"][2]
        assert panel["title"] == "Contract Breach Count"
        assert panel["type"] == "stat"

    def test_query_uses_breach_total(self, dashboard):
        panel = dashboard["panels"][2]
        expr = panel["targets"][0]["expr"]
        assert "release_gate_contract_breach_total" in expr
        assert "increase" in expr


class TestAuditWriteFailuresPanel:
    """Panel 4: Audit Write Failures (stat)."""

    def test_panel_type(self, dashboard):
        panel = dashboard["panels"][3]
        assert panel["title"] == "Audit Write Failures"
        assert panel["type"] == "stat"

    def test_query_uses_audit_failures_total(self, dashboard):
        panel = dashboard["panels"][3]
        expr = panel["targets"][0]["expr"]
        assert "release_gate_audit_write_failures_total" in expr
        assert "increase" in expr


class TestPanelDescriptions:
    """Her panelde description ve runbook link bulunmalı."""

    def test_all_panels_have_description(self, dashboard):
        for panel in dashboard["panels"]:
            assert panel.get("description"), f"Panel '{panel['title']}' has no description"

    def test_all_panels_have_runbook_link(self, dashboard):
        for panel in dashboard["panels"]:
            desc = panel.get("description", "")
            assert "runbook" in desc.lower() or "Runbook" in desc, (
                f"Panel '{panel['title']}' description missing runbook reference"
            )


class TestMetricNamesInQueries:
    """Tüm beklenen metrik isimleri query'lerde kullanılıyor."""

    EXPECTED_METRICS = [
        "release_gate_decision_total",
        "release_gate_contract_breach_total",
        "release_gate_audit_write_failures_total",
    ]

    def test_all_expected_metrics_present(self, dashboard):
        all_exprs = []
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))
        joined = " ".join(all_exprs)
        for metric in self.EXPECTED_METRICS:
            assert metric in joined, f"Metric '{metric}' not found in any query"
