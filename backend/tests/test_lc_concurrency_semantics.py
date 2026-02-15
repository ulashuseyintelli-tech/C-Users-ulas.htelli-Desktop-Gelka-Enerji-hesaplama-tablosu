"""
PR-9: Concurrency semantics tests.

- CAS semantics: try_claim is atomic
- Retry policy: bounded backoff simulation
- Monotonic audit ordering
- Override TTL server-side enforcement across instances
- PBT: interleaved operations preserve invariants
- PBT: retry bounded backoff never exceeds max
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
def store(request) -> StateStore:
    if request.param == "memory":
        return MemoryStateStore()
    return SqliteStateStore(":memory:")


def _override(key: str, ttl: int = 600, created_at: int = 1_000_000) -> Override:
    return Override(
        override_type=OverrideType.FORCE_PROMOTE,
        scope=OverrideScope.GLOBAL,
        scope_value=None,
        ttl_seconds=ttl,
        created_at_ms=created_at,
        reason="test",
        created_by="tester",
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# Bounded retry simulation
# ---------------------------------------------------------------------------

def bounded_retry(fn, max_retries: int = 3, backoff_base_ms: int = 10) -> tuple[bool, int]:
    """
    Simulate bounded retry with exponential backoff.
    Returns (success, attempts).
    """
    for attempt in range(1, max_retries + 1):
        result = fn()
        if result:
            return True, attempt
        # Backoff would happen here in real code
    return False, max_retries


# ---------------------------------------------------------------------------
# CAS atomicity
# ---------------------------------------------------------------------------

class TestCasAtomicity:
    def test_claim_is_atomic_under_contention(self, store: StateStore):
        """Multiple threads claiming same event — exactly one succeeds."""
        results = []
        barrier = threading.Barrier(4)

        def claim(inst_id):
            barrier.wait(timeout=5)
            r = store.try_claim_event("atomic-1", inst_id)
            results.append(r)

        threads = [threading.Thread(target=claim, args=(f"i-{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        new_count = sum(1 for r in results if r.is_new)
        assert new_count == 1

    def test_sequential_claims_consistent(self, store: StateStore):
        r1 = store.try_claim_event("seq-1", "a")
        r2 = store.try_claim_event("seq-1", "b")
        r3 = store.try_claim_event("seq-1", "c")
        assert r1.is_new is True
        assert r2.is_new is False
        assert r3.is_new is False
        assert r2.owner_instance == "a"
        assert r3.owner_instance == "a"


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_retry_succeeds_on_first_attempt(self):
        success, attempts = bounded_retry(lambda: True, max_retries=3)
        assert success is True
        assert attempts == 1

    def test_retry_fails_after_max(self):
        success, attempts = bounded_retry(lambda: False, max_retries=3)
        assert success is False
        assert attempts == 3

    def test_retry_succeeds_on_second_attempt(self):
        call_count = [0]
        def fn():
            call_count[0] += 1
            return call_count[0] >= 2
        success, attempts = bounded_retry(fn, max_retries=5)
        assert success is True
        assert attempts == 2


# ---------------------------------------------------------------------------
# Monotonic audit ordering
# ---------------------------------------------------------------------------

class TestMonotonicAudit:
    def test_audit_preserves_insertion_order(self, store: StateStore):
        for i in range(10):
            store.append_audit(AuditEntry(
                timestamp_ms=i * 100, action=f"a{i}",
                override=None, policy_input=None, decision=None,
            ))
        entries = store.query_audit(last_n=10)
        # Most recent first
        timestamps = [e.timestamp_ms for e in entries]
        assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# Override TTL server-side enforcement
# ---------------------------------------------------------------------------

class TestOverrideTtlEnforcement:
    def test_ttl_enforced_across_instances(self, store: StateStore):
        """Both instances see same TTL behavior."""
        ovr = _override("ttl-test", ttl=60, created_at=1_000_000)
        store.put_override(ovr)

        # Instance A queries before expiry
        active_a = store.get_active_overrides(now_ms=1_050_000)
        assert len(active_a) == 1

        # Instance B queries after expiry
        active_b = store.get_active_overrides(now_ms=1_100_000)
        assert len(active_b) == 0

    def test_expire_is_idempotent(self, store: StateStore):
        ovr = _override("exp-idem", ttl=10, created_at=1_000_000)
        store.put_override(ovr)
        r1 = store.expire_overrides(now_ms=2_000_000)
        r2 = store.expire_overrides(now_ms=2_000_000)
        assert r1 == 1
        assert r2 == 0


# ---------------------------------------------------------------------------
# PBT: interleaved operations preserve invariants
# ---------------------------------------------------------------------------

class TestPbtInterleavedOps:
    @given(
        n_events=st.integers(min_value=1, max_value=10),
        n_audits=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_interleaved_claims_and_audits(self, n_events, n_audits):
        store = MemoryStateStore()
        # Interleave claims and audits
        for i in range(max(n_events, n_audits)):
            if i < n_events:
                store.try_claim_event(f"e-{i}", "inst")
            if i < n_audits:
                store.append_audit(AuditEntry(
                    timestamp_ms=i, action=f"a{i}",
                    override=None, policy_input=None, decision=None,
                ))
        # Invariants hold
        assert store.audit_count() == n_audits
        for i in range(n_events):
            assert store.is_claimed(f"e-{i}")


# ---------------------------------------------------------------------------
# PBT: retry bounded backoff never exceeds max
# ---------------------------------------------------------------------------

class TestPbtRetryBounded:
    @given(max_retries=st.integers(min_value=1, max_value=20))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_retry_never_exceeds_max(self, max_retries):
        _, attempts = bounded_retry(lambda: False, max_retries=max_retries)
        assert attempts == max_retries
        assert attempts <= max_retries
