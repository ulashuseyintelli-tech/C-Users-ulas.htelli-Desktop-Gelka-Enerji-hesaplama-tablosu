"""
Property-based tests for runbook structural invariants.

Feature: observability-pack
Properties: P7 (Runbook-Alert Coverage Matching), P8 (Runbook Section Completeness)

Uses Hypothesis to iterate over all alerts/sections and verify universal properties.
"""

import re

import pytest
import yaml
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from .conftest import ALERTS_PATH, RUNBOOK_PATH

# ── Helpers ────────────────────────────────────────────────────────

def _load_alert_names():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [r["alert"] for r in data["spec"]["groups"][0]["rules"]]


def _load_alert_rules():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["spec"]["groups"][0]["rules"]


def _load_runbook():
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def _parse_runbook_sections(text):
    """Parse runbook into {alert_name: section_text} dict."""
    sections = {}
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts[1:]:
        lines = part.strip().split("\n")
        name = lines[0].strip()
        body = "\n".join(lines[1:])
        sections[name] = body
    return sections


def _count_list_items(text, heading):
    """Count numbered list items under a ### heading."""
    pattern = rf"### {heading}\s*\n((?:\d+\..+\n?)+)"
    match = re.search(pattern, text)
    if not match:
        return 0
    return len(re.findall(r"^\d+\.", match.group(1), re.MULTILINE))


ALERT_NAMES = _load_alert_names()
ALERT_RULES = _load_alert_rules()
RUNBOOK_TEXT = _load_runbook()
RUNBOOK_SECTIONS = _parse_runbook_sections(RUNBOOK_TEXT)
RUNBOOK_HEADINGS = list(RUNBOOK_SECTIONS.keys())


# ── Property 7: Runbook-Alert Kapsam Eşleştirmesi ─────────────────

class TestPropertyRunbookAlertCoverage:
    """P7: For any alert in YAML, a corresponding runbook section SHALL exist,
    and the runbook_url annotation SHALL contain a matching anchor.
    Validates: Requirements 12.1, 12.5"""

    @given(alert_idx=st.sampled_from(list(range(len(ALERT_NAMES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_alert_has_runbook_section(self, alert_idx):
        name = ALERT_NAMES[alert_idx]
        assert name in RUNBOOK_SECTIONS, (
            f"Alert '{name}' has no corresponding runbook section"
        )

    @given(alert_idx=st.sampled_from(list(range(len(ALERT_RULES)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_runbook_url_contains_anchor(self, alert_idx):
        rule = ALERT_RULES[alert_idx]
        url = rule.get("annotations", {}).get("runbook_url", "")
        alert_name = rule["alert"]
        # Anchor should contain the alert name (case-insensitive)
        anchor = url.split("#")[-1] if "#" in url else ""
        assert alert_name.lower() in anchor.lower(), (
            f"Alert '{alert_name}' runbook_url anchor '{anchor}' "
            f"does not match alert name"
        )

    def test_no_orphan_runbook_sections(self):
        """No runbook sections without a matching alert."""
        alert_set = set(ALERT_NAMES)
        orphans = [h for h in RUNBOOK_HEADINGS if h not in alert_set]
        assert not orphans, f"Orphan runbook sections: {orphans}"


# ── Property 8: Runbook Bölüm Tamamlığı ───────────────────────────

class TestPropertyRunbookSectionCompleteness:
    """P8: For any alert section in the runbook, it SHALL contain
    >= 3 probable causes, >= 3 diagnostic checks, >= 1 mitigation step.
    Validates: Requirements 12.2, 12.3, 12.4"""

    @given(section_idx=st.sampled_from(list(range(len(RUNBOOK_HEADINGS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_section_has_minimum_causes(self, section_idx):
        name = RUNBOOK_HEADINGS[section_idx]
        body = RUNBOOK_SECTIONS[name]
        count = _count_list_items(body, "Olası Nedenler")
        assert count >= 3, (
            f"Section '{name}': expected >= 3 causes, got {count}"
        )

    @given(section_idx=st.sampled_from(list(range(len(RUNBOOK_HEADINGS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_section_has_minimum_checks(self, section_idx):
        name = RUNBOOK_HEADINGS[section_idx]
        body = RUNBOOK_SECTIONS[name]
        count = _count_list_items(body, "İlk 3 Kontrol")
        assert count >= 3, (
            f"Section '{name}': expected >= 3 checks, got {count}"
        )

    @given(section_idx=st.sampled_from(list(range(len(RUNBOOK_HEADINGS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_section_has_minimum_mitigation(self, section_idx):
        name = RUNBOOK_HEADINGS[section_idx]
        body = RUNBOOK_SECTIONS[name]
        count = _count_list_items(body, "Müdahale Adımları")
        assert count >= 1, (
            f"Section '{name}': expected >= 1 mitigation, got {count}"
        )

    @given(section_idx=st.sampled_from(list(range(len(RUNBOOK_HEADINGS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_section_has_severity(self, section_idx):
        name = RUNBOOK_HEADINGS[section_idx]
        body = RUNBOOK_SECTIONS[name]
        assert "**Severity:**" in body, (
            f"Section '{name}': missing severity declaration"
        )

    @given(section_idx=st.sampled_from(list(range(len(RUNBOOK_HEADINGS)))))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_section_has_promql(self, section_idx):
        name = RUNBOOK_HEADINGS[section_idx]
        body = RUNBOOK_SECTIONS[name]
        assert "**PromQL:**" in body, (
            f"Section '{name}': missing PromQL expression"
        )
