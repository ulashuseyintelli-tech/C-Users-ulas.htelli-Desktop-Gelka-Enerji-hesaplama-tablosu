"""
Structural validation tests for PrometheusRule YAML.

Feature: observability-pack
"""

import pytest


class TestAlertRulesCRD:
    """PrometheusRule CRD structure validation."""

    def test_yaml_parses(self, alert_rules):
        """YAML is valid and parseable."""
        assert isinstance(alert_rules, dict)

    def test_api_version(self, alert_rules):
        """Validates: Requirements 11.1"""
        assert alert_rules["apiVersion"] == "monitoring.coreos.com/v1"

    def test_kind(self, alert_rules):
        """Validates: Requirements 11.1"""
        assert alert_rules["kind"] == "PrometheusRule"

    def test_metadata_labels(self, alert_rules):
        labels = alert_rules["metadata"]["labels"]
        assert labels["app"] == "ptf-admin"
        assert labels["prometheus"] == "kube-prometheus"

    def test_group_name(self, alert_rules):
        """Validates: Requirements 11.2"""
        groups = alert_rules["spec"]["groups"]
        assert len(groups) >= 1
        assert groups[0]["name"] == "ptf-admin-alerts"


class TestAlertRulesCompleteness:
    """Verify all expected alerts exist with correct configuration."""

    EXPECTED_ALERTS = {
        "PTFAdminMetricsAbsent": {"severity": "critical", "for": "5m"},
        "PTFAdminTargetDown": {"severity": "critical", "for": "2m"},
        "PTFAdmin5xxSpike": {"severity": "warning", "for": "5m"},
        "PTFAdminExceptionPath": {"severity": "critical", "for": "5m"},
        "PTFAdminHighLatency": {"severity": "warning", "for": "5m"},
        "PTFAdminTelemetryLatency": {"severity": "warning", "for": "5m"},
        "PTFAdminImportLatency": {"severity": "warning", "for": "5m"},
        "PTFAdminTelemetryAbuse": {"severity": "warning", "for": "5m"},
        "PTFAdminImportRejectRatio": {"severity": "warning", "for": "15m"},
    }

    def _get_rules(self, alert_rules):
        return alert_rules["spec"]["groups"][0]["rules"]

    def _get_rule(self, alert_rules, name):
        for r in self._get_rules(alert_rules):
            if r["alert"] == name:
                return r
        return None

    def test_alert_count(self, alert_rules):
        """All 9 alerts are defined."""
        rules = self._get_rules(alert_rules)
        assert len(rules) == 9

    @pytest.mark.parametrize("alert_name,expected", list(EXPECTED_ALERTS.items()))
    def test_alert_severity_and_for(self, alert_rules, alert_name, expected):
        """Each alert has correct severity and for duration.
        Validates: Requirements 6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 8.3, 9.1, 10.1"""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None, f"Alert '{alert_name}' not found"
        assert rule["labels"]["severity"] == expected["severity"]
        assert rule["for"] == expected["for"]

    REQUIRED_LABELS = {"severity", "team", "service"}
    REQUIRED_ANNOTATIONS = {"summary", "description", "runbook_url"}

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_labels_complete(self, alert_rules, alert_name):
        """Each alert has required labels.
        Validates: Requirements 6.3, 11.3"""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        missing = self.REQUIRED_LABELS - rule.get("labels", {}).keys()
        assert not missing, f"{alert_name} missing labels: {missing}"

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_annotations_complete(self, alert_rules, alert_name):
        """Each alert has required annotations.
        Validates: Requirements 6.3, 11.4"""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        missing = self.REQUIRED_ANNOTATIONS - rule.get("annotations", {}).keys()
        assert not missing, f"{alert_name} missing annotations: {missing}"

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_service_label(self, alert_rules, alert_name):
        """All alerts have service=ptf-admin."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        assert rule["labels"]["service"] == "ptf-admin"


class TestAlertExpressions:
    """Verify specific PromQL expressions."""

    def _get_rule(self, alert_rules, name):
        for r in alert_rules["spec"]["groups"][0]["rules"]:
            if r["alert"] == name:
                return r
        return None

    def test_metrics_absent_expr(self, alert_rules):
        """Validates: Requirements 6.1"""
        rule = self._get_rule(alert_rules, "PTFAdminMetricsAbsent")
        assert "absent(ptf_admin_api_request_total)" in rule["expr"]

    def test_target_down_expr(self, alert_rules):
        """Validates: Requirements 6.2"""
        rule = self._get_rule(alert_rules, "PTFAdminTargetDown")
        assert "up{" in rule["expr"]
        assert "== 0" in rule["expr"]

    def test_5xx_spike_expr(self, alert_rules):
        """Validates: Requirements 7.1"""
        rule = self._get_rule(alert_rules, "PTFAdmin5xxSpike")
        assert 'status_class="5xx"' in rule["expr"]
        assert "0.05" in rule["expr"]

    def test_exception_path_expr(self, alert_rules):
        """Validates: Requirements 7.2"""
        rule = self._get_rule(alert_rules, "PTFAdminExceptionPath")
        assert 'status_class="0xx"' in rule["expr"]

    def test_telemetry_abuse_expr(self, alert_rules):
        """Validates: Requirements 9.1"""
        rule = self._get_rule(alert_rules, "PTFAdminTelemetryAbuse")
        assert "/admin/telemetry/events" in rule["expr"]
        assert 'status_class="4xx"' in rule["expr"]

    def test_import_reject_ratio_expr(self, alert_rules):
        """Validates: Requirements 10.1"""
        rule = self._get_rule(alert_rules, "PTFAdminImportRejectRatio")
        assert "ptf_admin_import_rows_total" in rule["expr"]
        assert "0.2" in rule["expr"]
