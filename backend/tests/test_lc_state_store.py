"""
PR-9: State Store contract tests.

Runs the same test suite against both MemoryStateStore and SqliteStateStore.
- Dedup: claim, duplicate, is_claimed
- Audit: append, query, count, idempotency key
- Overrides: put, get_active, expire
- PBT: dedup exactly-once
- PBT: audit count monotonic
- PBT: expired overrides never returned
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.policy_engine import (
    AuditEntry,
    Override,
    OverrideType,
    OverrideScope,
)
from backend.app.testing.state_store import StateStore
from backend.app.testing.store_memory import MemoryStateStore
from backend.app.testing.store_sqlite import SqliteStateStore


# ---------------------------------------------------------------------------
# Fixtures — parametrize over both implementations
# ---------------------------------------------------------------------------

@pytest.fixture(params=["memory", "sqlite"], ids=["memory", "sqlite"])
def store(request) -> StateStore:
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
        reason="test",
        created_by="tester",
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

class TestDedup:
    def test_first_claim_is_new(self, store: StateStore):
        r = store.try_claim_event("e1", "inst-a")
        assert r.is_new is True
        assert r.owner_instance == "inst-a"

    def test_second_claim_is_duplicate(self, store: StateStore):
        store.try_claim_event("e1", "inst-a")
        r = store.try_claim_event("e1", "inst-b")
        assert r.is_new is False
        assert r.owner_instance == "inst-a"

    def test_is_claimed(self, store: StateStore):
        assert store.is_claimed("e1") is False
        store.try_claim_event("e1", "inst-a")
        assert store.is_claimed("e1") is True

    def test_different_events_both_new(self, store: StateStore):
        r1 = store.try_claim_event("e1", "inst-a")
        r2 = store.try_claim_event("e2", "inst-b")
        assert r1.is_new is True
        assert r2.is_new is True


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestAudit:
    def test_append_and_count(self, store: StateStore):
        e = AuditEntry(timestamp_ms=1000, action="test", override=None,
                        policy_input=None, decision=None)
        assert store.append_audit(e) is True
        assert store.audit_count() == 1

    def test_query_returns_most_recent_first(self, store: StateStore):
        for i in range(5):
            store.append_audit(AuditEntry(
                timestamp_ms=i * 1000, action=f"a{i}",
                override=None, policy_input=None, decision=None,
            ))
        entries = store.query_audit(last_n=3)
        assert len(entries) == 3
        assert entries[0].timestamp_ms == 4000

    def test_idempotency_key_rejects_duplicate(self, store: StateStore):
        ovr = _override(key="dup-key")
        e1 = AuditEntry(timestamp_ms=1000, action="o1", override=ovr,
                         policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="o2", override=ovr,
                         policy_input=None, decision=None)
        assert store.append_audit(e1) is True
        assert store.append_audit(e2) is False
        assert store.audit_count() == 1

    def test_different_keys_both_accepted(self, store: StateStore):
        o1 = _override(key="a")
        o2 = _override(key="b")
        e1 = AuditEntry(timestamp_ms=1000, action="o1", override=o1,
                         policy_input=None, decision=None)
        e2 = AuditEntry(timestamp_ms=2000, action="o2", override=o2,
                         policy_input=None, decision=None)
        assert store.append_audit(e1) is True
        assert store.append_audit(e2) is True
        assert store.audit_count() == 2


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_put_and_get_active(self, store: StateStore):
        ovr = _override(ttl=600, created_at=1_000_000)
        assert store.put_override(ovr) is True
        active = store.get_active_overrides(now_ms=1_100_000)
        assert len(active) == 1
        assert active[0].idempotency_key == "k1"

    def test_put_duplicate_rejected(self, store: StateStore):
        ovr = _override(key="dup")
        assert store.put_override(ovr) is True
        assert store.put_override(ovr) is False

    def test_expired_not_in_active(self, store: StateStore):
        ovr = _override(ttl=60, created_at=1_000_000)  # expires at 1_060_000
        store.put_override(ovr)
        active = store.get_active_overrides(now_ms=2_000_000)
        assert len(active) == 0

    def test_expire_removes_old(self, store: StateStore):
        store.put_override(_override(key="old", ttl=10, created_at=1_000_000))
        store.put_override(_override(key="new", ttl=6000, created_at=1_000_000))
        removed = store.expire_overrides(now_ms=1_100_000)
        assert removed == 1
        active = store.get_active_overrides(now_ms=1_100_000)
        assert len(active) == 1
        assert active[0].idempotency_key == "new"


# ---------------------------------------------------------------------------
# PBT: dedup exactly-once
# ---------------------------------------------------------------------------

class TestPbtDedupExactlyOnce:
    @given(
        event_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        n_attempts=st.integers(min_value=2, max_value=10),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_exactly_one_new_claim(self, event_id, n_attempts):
        store = MemoryStateStore()
        new_count = 0
        for i in range(n_attempts):
            r = store.try_claim_event(event_id, f"inst-{i}")
            if r.is_new:
                new_count += 1
        assert new_count == 1


# ---------------------------------------------------------------------------
# PBT: audit count monotonic
# ---------------------------------------------------------------------------

class TestPbtAuditCountMonotonic:
    @given(n=st.integers(min_value=1, max_value=20))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_count_never_decreases(self, n):
        store = MemoryStateStore()
        prev = 0
        for i in range(n):
            store.append_audit(AuditEntry(
                timestamp_ms=i, action=f"a{i}",
                override=None, policy_input=None, decision=None,
            ))
            curr = store.audit_count()
            assert curr >= prev
            prev = curr


# ---------------------------------------------------------------------------
# PBT: expired overrides never returned
# ---------------------------------------------------------------------------

class TestPbtExpiredOverrides:
    @given(
        ttl=st.integers(min_value=1, max_value=1000),
        created_at=st.integers(min_value=0, max_value=1_000_000),
        query_offset=st.integers(min_value=0, max_value=2_000_000),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_expired_never_active(self, ttl, created_at, query_offset):
        store = MemoryStateStore()
        ovr = _override(ttl=ttl, created_at=created_at)
        store.put_override(ovr)
        now = created_at + query_offset
        active = store.get_active_overrides(now)
        if query_offset >= ttl * 1000:
            assert len(active) == 0
        else:
            assert len(active) == 1
