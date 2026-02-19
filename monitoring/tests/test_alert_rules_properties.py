"""
Property-based tests for PrometheusRule YAML structural invariants.

Feature: observability-pack
Properties: P5 (Alert Rule Completeness), P6 (CRD Validity)

Uses Hypothesis to iterate over all alert rules and verify universal properties.
"""

import re

import pytest
import yaml
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from .conftest import ALERTS_PATH

# ── Helpers ────────────────────────────────────────────────────────

def _load_alerts():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


ALERT_DATA = _load_alerts()
RULES = []
for _group in ALERT_DATA["spec"]["groups"]:
    RULES.extend(_group["rules"])

REQUIRED_LABELS = {"severity", "team", "service"}
REQUIRED_ANNOTATIONS = {"summary", "description", "runbook_url"}
VALID_SEVERITIES = {"critical", "warning"}
FOR_DURATION_RE = re.compile(r"^\d+[smh]$")


# ── Property 5: Alert Rule Tamamlığı ──────────────────────────────

class TestPropertyAlertRuleCompleteness:
    """P5: For any alert rule, it SHALL contain required labels and annotations.
    Validates: Requirements 6.3, 7.3, 8.4, 9.2, 10.2, 11.3, 11.4"""

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_has_required_labels(self, rule_idx):
        rule = RULES[rule_idx]
        labels = rule.get("labels", {})
        missing = REQUIRED_LABELS - labels.keys()
        assert not missing, (
            f"Alert '{rule['alert']}' missing labels: {missing}"
        )

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_has_required_annotations(self, rule_idx):
        rule = RULES[rule_idx]
        annotations = rule.get("annotations", {})
        missing = REQUIRED_ANNOTATIONS - annotations.keys()
        assert not missing, (
            f"Alert '{rule['alert']}' missing annotations: {missing}"
        )

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_severity_is_valid(self, rule_idx):
        rule = RULES[rule_idx]
        severity = rule.get("labels", {}).get("severity")
        assert severity in VALID_SEVERITIES, (
            f"Alert '{rule['alert']}' has invalid severity: {severity}"
        )

    VALID_SERVICES = {"ptf-admin", "release-gate", "release-preflight"}

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_service_is_valid(self, rule_idx):
        rule = RULES[rule_idx]
        assert rule["labels"]["service"] in self.VALID_SERVICES, (
            f"Alert '{rule['alert']}' service '{rule['labels']['service']}' not in {self.VALID_SERVICES}"
        )

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_for_duration_format(self, rule_idx):
        rule = RULES[rule_idx]
        duration = rule.get("for", "")
        assert FOR_DURATION_RE.match(duration), (
            f"Alert '{rule['alert']}' has invalid 'for' duration: {duration}"
        )

    @given(rule_idx=st.sampled_from(list(range(len(RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_rule_has_nonempty_expr(self, rule_idx):
        rule = RULES[rule_idx]
        assert rule.get("expr", "").strip(), (
            f"Alert '{rule['alert']}' has empty expression"
        )


# ── Property 6: PrometheusRule CRD Geçerliliği ────────────────────

class TestPropertyCRDValidity:
    """P6: PrometheusRule YAML has correct apiVersion and kind.
    Validates: Requirements 11.1"""

    def test_api_version(self):
        assert ALERT_DATA["apiVersion"] == "monitoring.coreos.com/v1"

    def test_kind(self):
        assert ALERT_DATA["kind"] == "PrometheusRule"

    def test_has_spec_groups(self):
        assert "spec" in ALERT_DATA
        assert "groups" in ALERT_DATA["spec"]
        assert len(ALERT_DATA["spec"]["groups"]) >= 1

    def test_metadata_present(self):
        assert "metadata" in ALERT_DATA
        assert "name" in ALERT_DATA["metadata"]
        assert "labels" in ALERT_DATA["metadata"]
