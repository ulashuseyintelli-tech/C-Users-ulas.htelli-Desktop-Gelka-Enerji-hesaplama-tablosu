"""
PR-17: Preflight dashboard yapısal doğrulama testleri.

Validates: Requirements 5.1-5.5
"""
import json
import pytest
from pathlib import Path

_DASHBOARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "monitoring" / "grafana" / "preflight-dashboard.json"
)


@pytest.fixture(scope="module")
def dashboard():
    text = _DASHBOARD_PATH.read_text(encoding="utf-8")
    return json.loads(text)


class TestDashboardStructure:
    """Grafana dashboard JSON yapısal doğrulama."""

    def test_json_valid(self, dashboard):
        assert dashboard is not None

    def test_has_six_panels(self, dashboard):
        assert len(dashboard["panels"]) == 6

    def test_verdict_trend_panel(self, dashboard):
        panel = dashboard["panels"][0]
        assert panel["title"] == "Verdict Trend"
        assert panel["type"] == "timeseries"

    def test_top_reasons_panel(self, dashboard):
        panel = dashboard["panels"][1]
        assert panel["title"] == "Top Block Reasons"
        assert panel["type"] == "barchart"

    def test_override_attempts_panel(self, dashboard):
        panel = dashboard["panels"][2]
        assert panel["title"] == "Override Attempts"
        assert panel["type"] == "piechart"

    def test_block_ratio_panel(self, dashboard):
        panel = dashboard["panels"][3]
        assert panel["title"] == "Block Ratio"
        assert panel["type"] == "gauge"
        exprs = [t["expr"] for t in panel["targets"]]
        joined = " ".join(exprs)
        assert "release_preflight_verdict_total" in joined
        assert 'verdict="BLOCK"' in joined

    def test_override_applied_rate_panel(self, dashboard):
        panel = dashboard["panels"][4]
        assert panel["title"] == "Override Applied Rate"
        assert panel["type"] == "timeseries"
        exprs = [t["expr"] for t in panel["targets"]]
        joined = " ".join(exprs)
        assert "release_preflight_override_total" in joined
        assert 'kind="applied"' in joined

    def test_telemetry_health_panel(self, dashboard):
        panel = dashboard["panels"][5]
        assert panel["title"] == "Telemetry Health"
        assert panel["type"] == "stat"
        exprs = [t["expr"] for t in panel["targets"]]
        joined = " ".join(exprs)
        assert "release_preflight_telemetry_write_failures_total" in joined
        assert "release_preflight_store_generation" in joined

    def test_metric_names_in_queries(self, dashboard):
        """Sabit metrik isimleri query'lerde kullanılıyor."""
        all_exprs = []
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                all_exprs.append(target.get("expr", ""))
        joined = " ".join(all_exprs)
        assert "release_preflight_verdict_total" in joined
        assert "release_preflight_reason_total" in joined
        assert "release_preflight_override_total" in joined
        assert "release_preflight_telemetry_write_failures_total" in joined
        assert "release_preflight_store_generation" in joined

    def test_schema_version(self, dashboard):
        assert dashboard.get("schemaVersion", 0) >= 36

    def test_uid_set(self, dashboard):
        assert dashboard.get("uid") == "preflight-telemetry"

    def test_panel_ids_unique(self, dashboard):
        ids = [p["id"] for p in dashboard["panels"]]
        assert len(ids) == len(set(ids))
