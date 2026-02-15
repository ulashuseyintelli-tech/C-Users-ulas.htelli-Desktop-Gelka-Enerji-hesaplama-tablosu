"""
PR-11: Release Report Generator — deterministic audit artifact.

Produces a canonical, byte-level deterministic report from
ReleasePolicyResult + ReleasePolicyInput.

Ordering contracts:
- Tier summaries: TestTier enum order (SMOKE < CORE < CONCURRENCY < SOAK)
- Slowest tests per tier: duration desc, name asc (max 10)
- Reason codes: enum definition order
- Required actions: same order as reason codes
- Numbers: 2 decimal places for seconds, 1 decimal for percentages
- Missing snapshots: explicit "N/A"

Supports JSON round-trip: from_dict(to_dict(report)) == report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.app.testing.perf_budget import TestTier, TierRunResult, TestTiming
from backend.app.testing.release_policy import (
    BlockReasonCode,
    ReleasePolicyInput,
    ReleasePolicyResult,
    ReleaseVerdict,
    RequiredAction,
)


# ---------------------------------------------------------------------------
# Report data models (all frozen for determinism)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TierSummary:
    tier: str
    total_seconds: float
    budget_seconds: float
    usage_percent: float
    passed: bool
    slowest_tests: list[str] = field(default_factory=list)  # max 10


@dataclass(frozen=True)
class DriftSummary:
    abort_rate: float
    override_rate: float
    alert: bool
    alert_reason: str


@dataclass(frozen=True)
class OverrideSummary:
    total_overrides: int
    active_overrides: int
    expired_overrides: int


@dataclass(frozen=True)
class GuardSummary:
    active_guards: list[str] = field(default_factory=list)
    violated_guards: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseReport:
    verdict: str                          # ReleaseVerdict value
    reasons: list[str]                    # BlockReasonCode values
    required_actions: list[dict[str, str]]  # [{code, description}]
    tier_summaries: list[TierSummary]
    flaky_tests: list[str]
    drift_summary: DriftSummary | None
    override_summary: OverrideSummary | None
    guard_summary: GuardSummary | None
    generated_at: str                     # ISO 8601, externally supplied


# ---------------------------------------------------------------------------
# Tier ordering (canonical)
# ---------------------------------------------------------------------------

_TIER_ORDER = {t.value: i for i, t in enumerate(TestTier)}


def _tier_sort_key(ts: TierSummary) -> int:
    return _TIER_ORDER.get(ts.tier, 999)


# ---------------------------------------------------------------------------
# ReleaseReportGenerator
# ---------------------------------------------------------------------------

class ReleaseReportGenerator:
    """Deterministic report generator. Same input → same output, byte-level."""

    # -- public API --

    def generate(
        self,
        policy_result: ReleasePolicyResult,
        policy_input: ReleasePolicyInput,
        override_summary: OverrideSummary | None = None,
        guard_summary: GuardSummary | None = None,
        generated_at: str = "",
    ) -> ReleaseReport:
        tier_summaries = self._build_tier_summaries(policy_input.tier_results)
        flaky = sorted(policy_input.flake_snapshot) if policy_input.flake_snapshot else []
        drift = self._build_drift_summary(policy_input.drift_snapshot)

        return ReleaseReport(
            verdict=policy_result.verdict.value,
            reasons=[r.value for r in policy_result.reasons],
            required_actions=[
                {"code": a.code.value, "description": a.description}
                for a in policy_result.required_actions
            ],
            tier_summaries=tier_summaries,
            flaky_tests=flaky,
            drift_summary=drift,
            override_summary=override_summary,
            guard_summary=guard_summary,
            generated_at=generated_at,
        )

    def format_text(self, report: ReleaseReport) -> str:
        lines: list[str] = []
        lines.append(f"=== Release Report ({report.generated_at or 'N/A'}) ===")
        lines.append(f"Verdict: {report.verdict.upper()}")
        lines.append("")

        # Reasons
        if report.reasons:
            lines.append("Block/Hold Reasons:")
            for r in report.reasons:
                lines.append(f"  - {r}")
        else:
            lines.append("Block/Hold Reasons: none")
        lines.append("")

        # Required actions
        if report.required_actions:
            lines.append("Required Actions:")
            for a in report.required_actions:
                lines.append(f"  [{a['code']}] {a['description']}")
        lines.append("")

        # Tier summaries
        lines.append("Tier Summaries:")
        for ts in report.tier_summaries:
            status = "PASS" if ts.passed else "FAIL"
            lines.append(
                f"  {ts.tier}: {ts.total_seconds:.2f}s / {ts.budget_seconds:.2f}s "
                f"({ts.usage_percent:.1f}%) [{status}]"
            )
            if ts.slowest_tests:
                for name in ts.slowest_tests:
                    lines.append(f"    - {name}")
        lines.append("")

        # Flaky tests
        if report.flaky_tests:
            lines.append("Flaky Tests:")
            for t in report.flaky_tests:
                lines.append(f"  - {t}")
        else:
            lines.append("Flaky Tests: none")
        lines.append("")

        # Drift
        if report.drift_summary:
            ds = report.drift_summary
            alert_str = "YES" if ds.alert else "NO"
            lines.append("Drift Summary:")
            lines.append(f"  Abort Rate: {ds.abort_rate:.2f}")
            lines.append(f"  Override Rate: {ds.override_rate:.2f}")
            lines.append(f"  Alert: {alert_str}")
            if ds.alert_reason:
                lines.append(f"  Reason: {ds.alert_reason}")
        else:
            lines.append("Drift Summary: N/A")
        lines.append("")

        # Override
        if report.override_summary:
            os_ = report.override_summary
            lines.append("Override Summary:")
            lines.append(f"  Total: {os_.total_overrides}")
            lines.append(f"  Active: {os_.active_overrides}")
            lines.append(f"  Expired: {os_.expired_overrides}")
        else:
            lines.append("Override Summary: N/A")
        lines.append("")

        # Guard
        if report.guard_summary:
            gs = report.guard_summary
            lines.append("Guard Summary:")
            lines.append(f"  Active: {', '.join(gs.active_guards) or 'none'}")
            lines.append(f"  Violated: {', '.join(gs.violated_guards) or 'none'}")
        else:
            lines.append("Guard Summary: N/A")

        return "\n".join(lines)

    def to_dict(self, report: ReleaseReport) -> dict[str, Any]:
        return {
            "verdict": report.verdict,
            "reasons": list(report.reasons),
            "required_actions": [dict(a) for a in report.required_actions],
            "tier_summaries": [
                {
                    "tier": ts.tier,
                    "total_seconds": ts.total_seconds,
                    "budget_seconds": ts.budget_seconds,
                    "usage_percent": ts.usage_percent,
                    "passed": ts.passed,
                    "slowest_tests": list(ts.slowest_tests),
                }
                for ts in report.tier_summaries
            ],
            "flaky_tests": list(report.flaky_tests),
            "drift_summary": (
                {
                    "abort_rate": report.drift_summary.abort_rate,
                    "override_rate": report.drift_summary.override_rate,
                    "alert": report.drift_summary.alert,
                    "alert_reason": report.drift_summary.alert_reason,
                }
                if report.drift_summary
                else None
            ),
            "override_summary": (
                {
                    "total_overrides": report.override_summary.total_overrides,
                    "active_overrides": report.override_summary.active_overrides,
                    "expired_overrides": report.override_summary.expired_overrides,
                }
                if report.override_summary
                else None
            ),
            "guard_summary": (
                {
                    "active_guards": list(report.guard_summary.active_guards),
                    "violated_guards": list(report.guard_summary.violated_guards),
                }
                if report.guard_summary
                else None
            ),
            "generated_at": report.generated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ReleaseReport:
        tier_summaries = [
            TierSummary(
                tier=ts["tier"],
                total_seconds=ts["total_seconds"],
                budget_seconds=ts["budget_seconds"],
                usage_percent=ts["usage_percent"],
                passed=ts["passed"],
                slowest_tests=ts.get("slowest_tests", []),
            )
            for ts in data.get("tier_summaries", [])
        ]
        drift = None
        if data.get("drift_summary"):
            d = data["drift_summary"]
            drift = DriftSummary(
                abort_rate=d["abort_rate"],
                override_rate=d["override_rate"],
                alert=d["alert"],
                alert_reason=d.get("alert_reason", ""),
            )
        override = None
        if data.get("override_summary"):
            o = data["override_summary"]
            override = OverrideSummary(
                total_overrides=o["total_overrides"],
                active_overrides=o["active_overrides"],
                expired_overrides=o["expired_overrides"],
            )
        guard = None
        if data.get("guard_summary"):
            g = data["guard_summary"]
            guard = GuardSummary(
                active_guards=g.get("active_guards", []),
                violated_guards=g.get("violated_guards", []),
            )
        return ReleaseReport(
            verdict=data["verdict"],
            reasons=data.get("reasons", []),
            required_actions=data.get("required_actions", []),
            tier_summaries=tier_summaries,
            flaky_tests=data.get("flaky_tests", []),
            drift_summary=drift,
            override_summary=override,
            guard_summary=guard,
            generated_at=data.get("generated_at", ""),
        )

    # -- private helpers --

    def _build_tier_summaries(self, tier_results: list[TierRunResult]) -> list[TierSummary]:
        summaries = []
        for tr in tier_results:
            usage = (tr.total_seconds / tr.budget_seconds * 100.0) if tr.budget_seconds > 0 else 0.0
            # Slowest: duration desc, name asc, max 10
            sorted_slow = sorted(
                tr.slowest,
                key=lambda t: (-t.duration_seconds, t.name),
            )[:10]
            summaries.append(TierSummary(
                tier=tr.tier.value,
                total_seconds=tr.total_seconds,
                budget_seconds=tr.budget_seconds,
                usage_percent=usage,
                passed=tr.passed,
                slowest_tests=[t.name for t in sorted_slow],
            ))
        # Canonical order: TestTier enum order
        summaries.sort(key=_tier_sort_key)
        return summaries

    def _build_drift_summary(self, drift_snapshot) -> DriftSummary | None:
        if drift_snapshot is None:
            return None
        return DriftSummary(
            abort_rate=drift_snapshot.abort_rate,
            override_rate=drift_snapshot.override_rate,
            alert=drift_snapshot.alert,
            alert_reason=drift_snapshot.alert_reason,
        )
