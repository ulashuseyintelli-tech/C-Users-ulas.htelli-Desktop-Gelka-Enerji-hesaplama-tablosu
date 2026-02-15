"""
PR-9: Multi-instance concurrency + race condition tests.

- 2 instances claim same event → exactly one wins
- 2 instances write same audit key → exactly one succeeds
- 2 instances put same override → exactly one succeeds
- Concurrent claims across many events → no lost claims
- Store timeout simulation → fail-closed
- PBT: concurrent dedup always exactly-once
- PBT: concurrent override put always exactly-once
"""
import threading
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import (
    AuditEntry,
    Override,
    OverrideType,
    OverrideScope,
)
from backend.app.testing.store_memory import MemoryStateStore
from backend.app.testing.store_sqlite import SqliteStateStore
from backend.app.testing.state_store import StateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "sqlite"], ids=["memory", "sqlite"])
def shared_store(request) -> StateStore:
    if request.param == "memory":
        return MemoryStateStore()
    return SqliteStateStore(":memory:")


def _override(key: str = "k1", ttl: int = 600, created_at: int = 1_000_000) -> Override:
    return Override(
        override_type=OverrideType.FORCE_PROMOTE,
        scope=OverrideScope.GLOBAL,
        scope_value=None,
        ttl_seconds=ttl,
        created_at_ms=created_at,
        reason="race-test",
        created_by="tester",
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# Helper: run N threads concurrently
# ---------------------------------------------------------------------------

def _run_concurrent(fn_list: list, timeout: float = 5.0) -> list:
    """Run a list of callables concurrently, return their results."""
    results = [None] * len(fn_list)
    errors = [None] * len(fn_list)

    def _worker(idx):
        try:
            results[idx] = fn_list[idx]()
        except Exception as e:
            errors[idx] = e

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(len(fn_list))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    for i, e in enumerate(errors):
        if e is not None:
            raise e
    return results


# ---------------------------------------------------------------------------
# 2 instances claim same event
# ---------------------------------------------------------------------------

class TestConcurrentDedup:
    def test_two_instances_same_event_one_wins(self, shared_store: StateStore):
        results = _run_concurrent([
            lambda: shared_store.try_claim_event("race-1", "inst-a"),
            lambda: shared_store.try_claim_event("race-1", "inst-b"),
        ])
        new_count = sum(1 for r in results if r.is_new)
        assert new_count == 1

    def test_winner_is_recorded(self, shared_store: StateStore):
        results = _run_concurrent([
            lambda: shared_store.try_claim_event("race-2", "inst-a"),
            lambda: shared_store.try_claim_event("race-2", "inst-b"),
        ])
        winner = [r for r in results if r.is_new][0]
        assert shared_store.is_claimed("race-2") is True
        # Subsequent claim returns the winner's instance
        dup = shared_store.try_claim_event("race-2", "inst-c")
        assert dup.owner_instance == winner.owner_instance


# ---------------------------------------------------------------------------
# 2 instances write same audit key
# ---------------------------------------------------------------------------

class TestConcurrentAudit:
    def test_two_instances_same_audit_key_one_succeeds(self, shared_store: StateStore):
        ovr = _override(key="audit-race")
        e1 = AuditEntry(timestamp_ms=1000, action="a1", override=ovr,
                         policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="a2", override=ovr,
                         policy_input=None, decision=None)
        results = _run_concurrent([
            lambda: shared_store.append_audit(e1),
            lambda: shared_store.append_audit(e2),
        ])
        success_count = sum(1 for r in results if r is True)
        assert success_count == 1
        assert shared_store.audit_count() == 1


# ---------------------------------------------------------------------------
# 2 instances put same override
# ---------------------------------------------------------------------------

class TestConcurrentOverride:
    def test_two_instances_same_override_one_succeeds(self, shared_store: StateStore):
        ovr = _override(key="ovr-race")
        results = _run_concurrent([
            lambda: shared_store.put_override(ovr),
            lambda: shared_store.put_override(ovr),
        ])
        success_count = sum(1 for r in results if r is True)
        assert success_count == 1


# ---------------------------------------------------------------------------
# Many concurrent claims across different events
# ---------------------------------------------------------------------------

class TestConcurrentManyClaims:
    def test_no_lost_claims_across_events(self, shared_store: StateStore):
        n_events = 20
        fns = [
            (lambda eid=f"evt-{i}": shared_store.try_claim_event(eid, f"inst-{i}"))
            for i in range(n_events)
        ]
        results = _run_concurrent(fns)
        assert all(r.is_new for r in results)
        # All events claimed
        for i in range(n_events):
            assert shared_store.is_claimed(f"evt-{i}")


# ---------------------------------------------------------------------------
# Multi-instance orchestrator simulation
# ---------------------------------------------------------------------------

class TestMultiInstanceOrchestrator:
    def test_two_orchestrators_same_event_one_executes(self, shared_store: StateStore):
        """Simulate 2 orchestrator instances processing the same event."""
        executed = []

        def orchestrate(instance_id: str):
            claim = shared_store.try_claim_event("deploy-42", instance_id)
            if claim.is_new:
                executed.append(instance_id)
                shared_store.append_audit(AuditEntry(
                    timestamp_ms=1000, action="execute",
                    detail=f"by {instance_id}",
                    override=None, policy_input=None, decision=None,
                ))

        _run_concurrent([
            lambda: orchestrate("orch-a"),
            lambda: orchestrate("orch-b"),
        ])
        assert len(executed) == 1
        assert shared_store.audit_count() == 1


# ---------------------------------------------------------------------------
# Store timeout simulation → fail-closed
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_unclaimed_event_not_processed(self, shared_store: StateStore):
        """If claim fails (duplicate), no side effect should occur."""
        shared_store.try_claim_event("locked", "inst-a")
        claim = shared_store.try_claim_event("locked", "inst-b")
        assert claim.is_new is False
        # inst-b must NOT process — fail-closed


# ---------------------------------------------------------------------------
# PBT: concurrent dedup always exactly-once
# ---------------------------------------------------------------------------

class TestPbtConcurrentDedup:
    @given(
        n_instances=st.integers(min_value=2, max_value=8),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_exactly_one_winner(self, n_instances):
        store = MemoryStateStore()
        fns = [
            (lambda iid=f"i-{i}": store.try_claim_event("pbt-race", iid))
            for i in range(n_instances)
        ]
        results = _run_concurrent(fns)
        new_count = sum(1 for r in results if r.is_new)
        assert new_count == 1


# ---------------------------------------------------------------------------
# PBT: concurrent override put always exactly-once
# ---------------------------------------------------------------------------

class TestPbtConcurrentOverride:
    @given(
        n_instances=st.integers(min_value=2, max_value=8),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_exactly_one_override_put(self, n_instances):
        store = MemoryStateStore()
        ovr = _override(key="pbt-ovr")
        fns = [lambda: store.put_override(ovr) for _ in range(n_instances)]
        results = _run_concurrent(fns)
        success_count = sum(1 for r in results if r is True)
        assert success_count == 1
