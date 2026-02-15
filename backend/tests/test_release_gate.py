"""
PR-11: ReleaseGate unit tests + property-based tests.

Unit tests (≥4): OK/BLOCK/HOLD flows, override TTL/scope validation,
absolute block override rejection.

PBT (3): gate verdict alignment, override validation, audit record.

Validates: Requirements 4.1-4.7, 6.3
"""
import pytest
from hypothesis import given, settings, HealthCheck, assume
from hypothesis import strategies as st

from backend.app.testing.perf_budget import TestTier, TierRunResult
from backend.app.testing.policy_engine import AuditLog, OpsGateStatus
from backend.app.testing.rollout_orchestrator import (
    DriftSnapshot,
    PolicyCanaryResult,
)
from backend.app.testing.release_policy import (
    ReleaseVerdict,
    BlockReasonCode,
    ABSOLUTE_BLOCK_REASONS,
    RequiredAction,
    ReleasePolicyInput,
    ReleasePolicyResult,
    ReleasePolicy,
)
from backend.app.testing.release_gate import (
    ReleaseOverride,
    GateDecision,
    ReleaseGate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

POLICY = ReleasePolicy()


def _clean_tier(tier: TestTier = TestTier.SMOKE) -> TierRunResult:
    return TierRunResult(
        tier=tier, total_seconds=1.0, test_count=5,
        budget_seconds=10.0, passed=True, slowest=[],
    )
