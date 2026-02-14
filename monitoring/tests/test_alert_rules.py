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


# ════════════════════════════════════════════════════════════════
# Ops-Guard Alert Group Tests — Feature: ops-guard, Task 8
# ════════════════════════════════════════════════════════════════


class TestOpsGuardAlertGroup:
    """Verify the ops-guard alert group exists alongside the original group."""

    def _get_groups(self, alert_rules):
        return alert_rules["spec"]["groups"]

    def _get_ops_guard_group(self, alert_rules):
        for g in self._get_groups(alert_rules):
            if g["name"] == "ptf-admin-ops-guard":
                return g
        return None

    def test_ops_guard_group_exists(self, alert_rules):
        """New ops-guard group is present."""
        group = self._get_ops_guard_group(alert_rules)
        assert group is not None, "ptf-admin-ops-guard group not found"

    def test_original_group_preserved(self, alert_rules):
        """Original ptf-admin-alerts group still has exactly 9 rules."""
        groups = self._get_groups(alert_rules)
        original = [g for g in groups if g["name"] == "ptf-admin-alerts"]
        assert len(original) == 1
        assert len(original[0]["rules"]) == 9

    def test_two_groups_total(self, alert_rules):
        """Exactly two rule groups exist."""
        assert len(self._get_groups(alert_rules)) == 2

    def test_ops_guard_alert_count(self, alert_rules):
        """Ops-guard group has exactly 7 alerts."""
        group = self._get_ops_guard_group(alert_rules)
        assert len(group["rules"]) == 7


class TestOpsGuardAlertCompleteness:
    """Verify all ops-guard alerts have correct configuration."""

    EXPECTED_ALERTS = {
        "PTFAdminKillSwitchActivated": {"severity": "critical", "for": "0m"},
        "PTFAdminCircuitBreakerOpen": {"severity": "critical", "for": "5m"},
        "PTFAdminRateLimitSpike": {"severity": "warning", "for": "2m"},
        "PTFAdminGuardConfigInvalid": {"severity": "warning", "for": "5m"},
        "PTFAdminGuardInternalError": {"severity": "critical", "for": "5m"},
        "PTFAdminSLOBurnRateFast": {"severity": "critical", "for": "5m"},
        "PTFAdminSLOBurnRateSlow": {"severity": "warning", "for": "30m"},
    }

    def _get_ops_guard_rules(self, alert_rules):
        for g in alert_rules["spec"]["groups"]:
            if g["name"] == "ptf-admin-ops-guard":
                return g["rules"]
        return []

    def _get_rule(self, alert_rules, name):
        for r in self._get_ops_guard_rules(alert_rules):
            if r["alert"] == name:
                return r
        return None

    REQUIRED_LABELS = {"severity", "team", "service"}
    REQUIRED_ANNOTATIONS = {"summary", "description", "runbook_url"}

    @pytest.mark.parametrize("alert_name,expected", list(EXPECTED_ALERTS.items()))
    def test_alert_severity_and_for(self, alert_rules, alert_name, expected):
        """Each ops-guard alert has correct severity and for duration."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None, f"Alert '{alert_name}' not found in ops-guard group"
        assert rule["labels"]["severity"] == expected["severity"]
        assert rule["for"] == expected["for"]

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_labels_complete(self, alert_rules, alert_name):
        """Each ops-guard alert has required labels."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        missing = self.REQUIRED_LABELS - rule.get("labels", {}).keys()
        assert not missing, f"{alert_name} missing labels: {missing}"

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_annotations_complete(self, alert_rules, alert_name):
        """Each ops-guard alert has required annotations (summary, description, runbook_url)."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        missing = self.REQUIRED_ANNOTATIONS - rule.get("annotations", {}).keys()
        assert not missing, f"{alert_name} missing annotations: {missing}"

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_service_label(self, alert_rules, alert_name):
        """All ops-guard alerts have service=ptf-admin."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        assert rule["labels"]["service"] == "ptf-admin"

    @pytest.mark.parametrize("alert_name", list(EXPECTED_ALERTS.keys()))
    def test_alert_runbook_url_has_anchor(self, alert_rules, alert_name):
        """Each ops-guard alert runbook_url has a # anchor."""
        rule = self._get_rule(alert_rules, alert_name)
        assert rule is not None
        url = rule["annotations"]["runbook_url"]
        assert "#" in url, f"{alert_name} runbook_url has no anchor: {url}"


class TestOpsGuardAlertExpressions:
    """Verify specific PromQL expressions for ops-guard alerts."""

    def _get_rule(self, alert_rules, name):
        for g in alert_rules["spec"]["groups"]:
            if g["name"] == "ptf-admin-ops-guard":
                for r in g["rules"]:
                    if r["alert"] == name:
                        return r
        return None

    def test_killswitch_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminKillSwitchActivated")
        assert "ptf_admin_killswitch_state" in rule["expr"]
        assert "== 1" in rule["expr"]

    def test_circuit_breaker_open_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminCircuitBreakerOpen")
        assert "ptf_admin_circuit_breaker_state" in rule["expr"]
        assert "== 2" in rule["expr"]

    def test_rate_limit_spike_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminRateLimitSpike")
        assert "ptf_admin_rate_limit_total" in rule["expr"]
        assert 'decision="deny"' in rule["expr"]

    def test_guard_config_invalid_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminGuardConfigInvalid")
        assert "ptf_admin_guard_config_fallback_total" in rule["expr"]

    def test_guard_internal_error_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminGuardInternalError")
        assert "ptf_admin_killswitch_error_total" in rule["expr"]
        assert "ptf_admin_killswitch_fallback_open_total" in rule["expr"]

    def test_slo_burn_rate_fast_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminSLOBurnRateFast")
        assert "ptf_admin_api_request_total" in rule["expr"]
        assert "1h" in rule["expr"]
        assert "0.01" in rule["expr"]

    def test_slo_burn_rate_slow_expr(self, alert_rules):
        rule = self._get_rule(alert_rules, "PTFAdminSLOBurnRateSlow")
        assert "ptf_admin_api_request_total" in rule["expr"]
        assert "6h" in rule["expr"]
        assert "0.005" in rule["expr"]
