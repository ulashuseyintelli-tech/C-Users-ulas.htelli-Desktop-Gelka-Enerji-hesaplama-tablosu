"""
PR-6: Pipeline Gate / Ops Contract Enforcement.

- Ops contract tests marked as "required" gate
- Runbook actionability guard (every alert has first-action)
- SLO evaluator + canary as pipeline gate integration
"""
import yaml
import pytest

from backend.app.testing.slo_evaluator import (
    SloEvaluator,
    SloTarget,
    SliKind,
    MetricSample,
    CanaryComparator,
    CanaryDecision,
    CanaryThresholds,
)


ALERTS_PATH = "monitoring/prometheus/ptf-admin-alerts.yml"
RUNBOOK_PATH = "monitoring/runbooks/ptf-admin-runbook.md"


def _load_alert_rules():
    with open(ALERTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = []
    for group in data["spec"]["groups"]:
        rules.extend(group["rules"])
    return rules


ALL_RULES = _load_alert_rules()


# ---------------------------------------------------------------------------
# GATE-1: Ops contract — every alert has required labels (pipeline gate)
# ---------------------------------------------------------------------------

class TestOpsContractGate:
    """These tests are 'required gate' — pipeline blocks on failure."""

    REQUIRED_LABELS = {"severity", "team", "service"}

    @pytest.mark.parametrize("rule", ALL_RULES, ids=[r["alert"] for r in ALL_RULES])
    def test_every_alert_has_required_labels(self, rule):
        labels = rule.get("labels", {})
        missing = self.REQUIRED_LABELS - labels.keys()
        assert not missing, f"{rule['alert']} missing labels: {missing}"

    @pytest.mark.parametrize("rule", ALL_RULES, ids=[r["alert"] for r in ALL_RULES])
    def test_every_alert_has_runbook_url(self, rule):
        url = rule.get("annotations", {}).get("runbook_url", "")
        assert url, f"{rule['alert']} missing runbook_url"


# ---------------------------------------------------------------------------
# GATE-2: Runbook actionability — every alert anchor exists in runbook
# ---------------------------------------------------------------------------

class TestRunbookActionability:
    """Every alert's runbook anchor must exist in the actual runbook file."""

    @pytest.fixture(autouse=True)
    def _load_runbook(self):
        with open(RUNBOOK_PATH, encoding="utf-8") as f:
            self._runbook_content = f.read().lower()

    @pytest.mark.parametrize("rule", ALL_RULES, ids=[r["alert"] for r in ALL_RULES])
    def test_runbook_anchor_exists(self, rule):
        url = rule["annotations"]["runbook_url"]
        if "#" not in url:
            pytest.skip(f"{rule['alert']} has no anchor")
        anchor = url.split("#")[-1].lower()
        # Markdown heading anchors: ## PTFAdminCircuitBreakerOpen → #ptfadmincircuitbreakeropen
        assert anchor in self._runbook_content, (
            f"{rule['alert']}: anchor '{anchor}' not found in runbook"
        )


# ---------------------------------------------------------------------------
# GATE-3: SLO + Canary pipeline integration
# ---------------------------------------------------------------------------

class TestPipelineGateIntegration:
    """SLO evaluator + canary comparator as pipeline gate."""

    def _make_samples(self, count, total=1000, success=999, p99=0.5, fp=0):
        return [
            MetricSample(
                timestamp_ms=i * 60_000,
                total_requests=total,
                successful_requests=success,
                latency_p99_seconds=p99,
                false_positive_alerts=fp,
            )
            for i in range(count)
        ]

    def test_pipeline_passes_when_slo_met_and_canary_promotes(self):
        """Full pipeline gate: SLO met + canary PROMOTE → gate passes."""
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, 2_592_000)
        samples = self._make_samples(10, total=1000, success=1000)
        window_end = 2_592_000 * 1000

        slo_result = SloEvaluator().evaluate(samples, window_end, slo)
        assert slo_result.met is True

        baseline = self._make_samples(15)
        canary = self._make_samples(15)
        canary_result = CanaryComparator().compare(baseline, canary)
        assert canary_result.decision == CanaryDecision.PROMOTE

    def test_pipeline_blocks_when_slo_not_met(self):
        """Pipeline gate blocks when SLO is not met."""
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, 2_592_000)
        samples = self._make_samples(10, total=1000, success=900)
        window_end = 2_592_000 * 1000

        slo_result = SloEvaluator().evaluate(samples, window_end, slo)
        assert slo_result.met is False
        # Gate should block — error budget exhausted
        assert slo_result.error_budget_remaining == 0.0

    def test_pipeline_blocks_when_canary_aborts(self):
        """Pipeline gate blocks when canary decision is ABORT."""
        baseline = self._make_samples(15, total=1000, success=999, p99=0.5)
        canary = self._make_samples(15, total=1000, success=950, p99=0.5)
        result = CanaryComparator(
            CanaryThresholds(max_error_rate_delta=0.01, min_samples=10)
        ).compare(baseline, canary)
        assert result.decision == CanaryDecision.ABORT
