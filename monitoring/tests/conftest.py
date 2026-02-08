"""
Shared fixtures for observability-pack structural validation tests.

Feature: observability-pack
"""

import json
import pathlib

import pytest
import yaml

MONITORING_ROOT = pathlib.Path(__file__).resolve().parent.parent

DASHBOARD_PATH = MONITORING_ROOT / "grafana" / "ptf-admin-dashboard.json"
ALERTS_PATH = MONITORING_ROOT / "prometheus" / "ptf-admin-alerts.yml"
RUNBOOK_PATH = MONITORING_ROOT / "runbooks" / "ptf-admin-runbook.md"


@pytest.fixture(scope="session")
def dashboard():
    """Load and parse the Grafana dashboard JSON."""
    with open(DASHBOARD_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def alert_rules():
    """Load and parse the PrometheusRule YAML."""
    with open(ALERTS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def runbook_text():
    """Load the runbook markdown as text."""
    return RUNBOOK_PATH.read_text(encoding="utf-8")
