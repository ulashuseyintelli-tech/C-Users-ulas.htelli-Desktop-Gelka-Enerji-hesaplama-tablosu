"""
Tests for PDF Worker Alert Rules (Task 5).

PA1) Alert group exists — ptf-admin-pdf-worker
PA2) Rule count — 3 rules
PA3) Each rule has required fields — alert, expr, labels.severity, annotations
PA4) Runbook URL anchors match alert names
PA5) Severity values valid
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ALERTS_PATH = Path(__file__).resolve().parent.parent.parent / "monitoring" / "prometheus" / "ptf-admin-alerts.yml"


@pytest.fixture(scope="module")
def alerts_spec():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def pdf_group(alerts_spec):
    groups = alerts_spec["spec"]["groups"]
    for g in groups:
        if g["name"] == "ptf-admin-pdf-worker":
            return g
    pytest.fail("Alert group 'ptf-admin-pdf-worker' not found")


# ═══════════════════════════════════════════════════════════════════════════════
# PA1) Alert group exists
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlertGroupExists:
    def test_group_present(self, pdf_group):
        assert pdf_group["name"] == "ptf-admin-pdf-worker"


# ═══════════════════════════════════════════════════════════════════════════════
# PA2) Rule count
# ═══════════════════════════════════════════════════════════════════════════════


class TestRuleCount:
    def test_three_rules(self, pdf_group):
        assert len(pdf_group["rules"]) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# PA3) Each rule has required fields
# ═══════════════════════════════════════════════════════════════════════════════


class TestRuleFields:
    def test_each_rule_has_alert_name(self, pdf_group):
        for rule in pdf_group["rules"]:
            assert "alert" in rule
            assert rule["alert"].startswith("PTFAdminPdf")

    def test_each_rule_has_expr(self, pdf_group):
        for rule in pdf_group["rules"]:
            assert "expr" in rule
            assert len(rule["expr"]) > 0

    def test_each_rule_has_severity(self, pdf_group):
        for rule in pdf_group["rules"]:
            assert "severity" in rule["labels"]

    def test_each_rule_has_annotations(self, pdf_group):
        for rule in pdf_group["rules"]:
            assert "summary" in rule["annotations"]
            assert "description" in rule["annotations"]
            assert "runbook_url" in rule["annotations"]


# ═══════════════════════════════════════════════════════════════════════════════
# PA4) Runbook URL anchors
# ═══════════════════════════════════════════════════════════════════════════════


class TestRunbookAnchors:
    def test_runbook_url_contains_alert_name(self, pdf_group):
        for rule in pdf_group["rules"]:
            alert_name = rule["alert"]
            runbook_url = rule["annotations"]["runbook_url"]
            assert alert_name in runbook_url, (
                f"Runbook URL for {alert_name} should contain the alert name as anchor"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# PA5) Severity values valid
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeverityValues:
    def test_valid_severity(self, pdf_group):
        valid = {"critical", "warning", "info"}
        for rule in pdf_group["rules"]:
            assert rule["labels"]["severity"] in valid


# ═══════════════════════════════════════════════════════════════════════════════
# PA6) Alert names match expected set
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlertNames:
    def test_expected_alerts(self, pdf_group):
        names = {r["alert"] for r in pdf_group["rules"]}
        expected = {
            "PTFAdminPdfQueueUnavailable",
            "PTFAdminPdfFailureSpike",
            "PTFAdminPdfQueueBacklog",
        }
        assert names == expected
