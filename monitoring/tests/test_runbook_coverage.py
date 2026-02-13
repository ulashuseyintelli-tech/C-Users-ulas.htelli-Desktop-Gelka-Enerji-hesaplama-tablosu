"""
Structural validation tests for runbook coverage.

Feature: observability-pack
"""

import re

import pytest
import yaml

from .conftest import ALERTS_PATH


class TestRunbookAlertCoverage:
    """Verify runbook has a section for every alert.
    Validates: Requirements 12.1, 12.5"""

    def _get_alert_names(self):
        with open(ALERTS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return [r["alert"] for r in data["spec"]["groups"][0]["rules"]]

    def _get_runbook_headings(self, runbook_text):
        return re.findall(r"^## (\S+)", runbook_text, re.MULTILINE)

    def test_every_alert_has_runbook_section(self, runbook_text):
        """Each alert in YAML has a corresponding ## heading in runbook."""
        alert_names = self._get_alert_names()
        headings = self._get_runbook_headings(runbook_text)
        for name in alert_names:
            assert name in headings, f"Runbook missing section for alert: {name}"

    def test_no_orphan_runbook_sections(self, runbook_text):
        """No runbook sections without a matching alert (catches typos)."""
        alert_names = set(self._get_alert_names())
        headings = self._get_runbook_headings(runbook_text)
        orphans = [h for h in headings if h not in alert_names]
        assert not orphans, f"Orphan runbook sections (no matching alert): {orphans}"

    class TestRunbookUrlAnchors:
        """Verify alert runbook_url annotations point to valid runbook anchors.
        Feature: deploy-integration
        Validates: Requirements 3.2, 4.1"""

        def _get_rules(self):
            with open(ALERTS_PATH, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data["spec"]["groups"][0]["rules"]

        def _get_runbook_headings(self, runbook_text):
            return re.findall(r"^## (\S+)", runbook_text, re.MULTILINE)

        def test_every_runbook_url_has_anchor(self, runbook_text):
            """Each alert's runbook_url contains a # anchor."""
            for rule in self._get_rules():
                url = rule.get("annotations", {}).get("runbook_url", "")
                assert "#" in url, (
                    f"Alert '{rule['alert']}' runbook_url has no anchor: {url}"
                )

        def test_runbook_url_anchors_match_headings(self, runbook_text):
            """Each alert's runbook_url anchor matches a real runbook heading."""
            headings = self._get_runbook_headings(runbook_text)
            headings_lower = {h.lower() for h in headings}
            for rule in self._get_rules():
                url = rule.get("annotations", {}).get("runbook_url", "")
                if "#" not in url:
                    continue
                anchor = url.split("#")[-1]
                assert anchor.lower() in headings_lower, (
                    f"Alert '{rule['alert']}' anchor '{anchor}' not found in runbook headings"
                )


class TestRunbookSectionCompleteness:
    """Verify each runbook section has required subsections.
    Validates: Requirements 12.2, 12.3, 12.4"""

    def _parse_sections(self, runbook_text):
        """Parse runbook into {alert_name: section_text} dict."""
        sections = {}
        parts = re.split(r"^## ", runbook_text, flags=re.MULTILINE)
        for part in parts[1:]:  # skip preamble
            lines = part.strip().split("\n")
            name = lines[0].strip()
            body = "\n".join(lines[1:])
            sections[name] = body
        return sections

    def _count_list_items(self, text, heading):
        """Count numbered list items under a ### heading."""
        pattern = rf"### {heading}\s*\n((?:\d+\..+\n?)+)"
        match = re.search(pattern, text)
        if not match:
            return 0
        return len(re.findall(r"^\d+\.", match.group(1), re.MULTILINE))

    def test_each_section_has_causes(self, runbook_text):
        """Each alert section has >= 3 probable causes."""
        sections = self._parse_sections(runbook_text)
        for name, body in sections.items():
            count = self._count_list_items(body, "Olası Nedenler")
            assert count >= 3, f"{name}: expected >= 3 causes, got {count}"

    def test_each_section_has_checks(self, runbook_text):
        """Each alert section has >= 3 diagnostic checks."""
        sections = self._parse_sections(runbook_text)
        for name, body in sections.items():
            count = self._count_list_items(body, "İlk 3 Kontrol")
            assert count >= 3, f"{name}: expected >= 3 checks, got {count}"

    def test_each_section_has_mitigation(self, runbook_text):
        """Each alert section has >= 1 mitigation step."""
        sections = self._parse_sections(runbook_text)
        for name, body in sections.items():
            count = self._count_list_items(body, "Müdahale Adımları")
            assert count >= 1, f"{name}: expected >= 1 mitigation, got {count}"

    def test_each_section_has_severity(self, runbook_text):
        """Each alert section declares severity."""
        sections = self._parse_sections(runbook_text)
        for name, body in sections.items():
            assert "**Severity:**" in body, f"{name}: missing severity declaration"

    def test_each_section_has_promql(self, runbook_text):
        """Each alert section includes the PromQL expression."""
        sections = self._parse_sections(runbook_text)
        for name, body in sections.items():
            assert "**PromQL:**" in body, f"{name}: missing PromQL expression"

class TestRunbookUrlAnchors:
    """Verify alert runbook_url annotations point to valid runbook anchors.
    Feature: deploy-integration
    Validates: Requirements 3.2, 4.1"""

    def _get_rules(self):
        with open(ALERTS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data["spec"]["groups"][0]["rules"]

    def _get_runbook_headings(self, runbook_text):
        return re.findall(r"^## (\S+)", runbook_text, re.MULTILINE)

    def test_every_runbook_url_has_anchor(self, runbook_text):
        """Each alert's runbook_url contains a # anchor."""
        for rule in self._get_rules():
            url = rule.get("annotations", {}).get("runbook_url", "")
            assert "#" in url, (
                f"Alert '{rule['alert']}' runbook_url has no anchor: {url}"
            )

    def test_runbook_url_anchors_match_headings(self, runbook_text):
        """Each alert's runbook_url anchor matches a real runbook heading."""
        headings = self._get_runbook_headings(runbook_text)
        headings_lower = {h.lower() for h in headings}
        for rule in self._get_rules():
            url = rule.get("annotations", {}).get("runbook_url", "")
            if "#" not in url:
                continue
            anchor = url.split("#")[-1]
            assert anchor.lower() in headings_lower, (
                f"Alert '{rule['alert']}' anchor '{anchor}' not found in runbook headings"
            )


class TestRunbookUrlAnchors:
    """Verify alert runbook_url annotations point to valid runbook anchors.
    Feature: deploy-integration
    Validates: Requirements 3.2, 4.1"""

    def _get_rules(self):
        with open(ALERTS_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data["spec"]["groups"][0]["rules"]

    def _get_runbook_headings(self, runbook_text):
        return re.findall(r"^## (\S+)", runbook_text, re.MULTILINE)

    def test_every_runbook_url_has_anchor(self, runbook_text):
        """Each alert's runbook_url contains a # anchor."""
        for rule in self._get_rules():
            url = rule.get("annotations", {}).get("runbook_url", "")
            assert "#" in url, (
                f"Alert '{rule['alert']}' runbook_url has no anchor: {url}"
            )

    def test_runbook_url_anchors_match_headings(self, runbook_text):
        """Each alert's runbook_url anchor matches a real runbook heading."""
        headings = self._get_runbook_headings(runbook_text)
        headings_lower = {h.lower() for h in headings}
        for rule in self._get_rules():
            url = rule.get("annotations", {}).get("runbook_url", "")
            if "#" not in url:
                continue
            anchor = url.split("#")[-1]
            assert anchor.lower() in headings_lower, (
                f"Alert '{rule['alert']}' anchor '{anchor}' not found in runbook headings"
            )
