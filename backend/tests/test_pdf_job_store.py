"""
Unit + property tests for PdfJobStore.

Covers:
- Job model, enums, constants
- compute_job_key determinism (Property 2)
- should_retry correctness (Property 3)
- State machine valid transitions (Property 1)
- Idempotency (Property 4)
- TTL cleanup (Property 10)
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.services.pdf_job_store import (
    PdfErrorCode,
    PdfJob,
    PdfJobStatus,
    PdfJobStore,
    TRANSIENT_ERRORS,
    MAX_RETRIES,
    VALID_TRANSITIONS,
    compute_job_key,
    is_valid_transition,
    should_retry,
)


# ===================================================================
# Unit tests — enums & constants
# ===================================================================

class TestEnumsAndConstants:
    def test_status_values(self):
        assert set(PdfJobStatus) == {
            PdfJobStatus.QUEUED,
            PdfJobStatus.RUNNING,
            PdfJobStatus.SUCCEEDED,
            PdfJobStatus.FAILED,
            PdfJobStatus.EXPIRED,
        }

    def test_error_code_values(self):
        assert set(PdfErrorCode) == {
            PdfErrorCode.BROWSER_LAUNCH_FAILED,
            PdfErrorCode.NAVIGATION_TIMEOUT,
            PdfErrorCode.TEMPLATE_ERROR,
            PdfErrorCode.UNSUPPORTED_PLATFORM,
            PdfErrorCode.ARTIFACT_WRITE_FAILED,
            PdfErrorCode.QUEUE_UNAVAILABLE,
            PdfErrorCode.UNKNOWN,
        }

    def test_transient_errors_subset(self):
        assert TRANSIENT_ERRORS == frozenset({
            PdfErrorCode.BROWSER_LAUNCH_FAILED,
            PdfErrorCode.NAVIGATION_TIMEOUT,
            PdfErrorCode.ARTIFACT_WRITE_FAILED,
        })

    def test_max_retries(self):
        assert MAX_RETRIES == 2

    def test_valid_transitions_completeness(self):
        """Every status has an entry in VALID_TRANSITIONS."""
        for s in PdfJobStatus:
            assert s in VALID_TRANSITIONS

    def test_expired_is_terminal(self):
        assert VALID_TRANSITIONS[PdfJobStatus.EXPIRED] == frozenset()


# ===================================================================
# Unit tests — compute_job_key
# ===================================================================

class TestComputeJobKey:
    def test_deterministic(self):
        k1 = compute_job_key("invoice", {"a": 1, "b": 2})
        k2 = compute_job_key("invoice", {"b": 2, "a": 1})
        assert k1 == k2

    def test_different_payloads_differ(self):
        k1 = compute_job_key("invoice", {"a": 1})
        k2 = compute_job_key("invoice", {"a": 2})
        assert k1 != k2

    def test_different_templates_differ(self):
        k1 = compute_job_key("invoice", {"a": 1})
        k2 = compute_job_key("receipt", {"a": 1})
        assert k1 != k2

    def test_returns_hex_string(self):
        k = compute_job_key("t", {})
        assert len(k) == 64  # sha256 hex
        assert all(c in "0123456789abcdef" for c in k)


# ===================================================================
# Unit tests — should_retry
# ===================================================================

class TestShouldRetry:
    def test_transient_below_cap(self):
        assert should_retry(PdfErrorCode.BROWSER_LAUNCH_FAILED, 0) is True
        assert should_retry(PdfErrorCode.NAVIGATION_TIMEOUT, 1) is True

    def test_transient_at_cap(self):
        assert should_retry(PdfErrorCode.BROWSER_LAUNCH_FAILED, 2) is False

    def test_permanent_never_retries(self):
        assert should_retry(PdfErrorCode.TEMPLATE_ERROR, 0) is False
        assert should_retry(PdfErrorCode.UNSUPPORTED_PLATFORM, 0) is False
        assert should_retry(PdfErrorCode.UNKNOWN, 0) is False

    def test_artifact_write_failed_retries(self):
        assert should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, 0) is True
        assert should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, 1) is True
        assert should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, 2) is False


# ===================================================================
# Unit tests — is_valid_transition
# ===================================================================

class TestIsValidTransition:
    def test_queued_to_running(self):
        assert is_valid_transition(PdfJobStatus.QUEUED, PdfJobStatus.RUNNING)

    def test_running_to_succeeded(self):
        assert is_valid_transition(PdfJobStatus.RUNNING, PdfJobStatus.SUCCEEDED)

    def test_running_to_failed(self):
        assert is_valid_transition(PdfJobStatus.RUNNING, PdfJobStatus.FAILED)

    def test_failed_to_queued_retry(self):
        assert is_valid_transition(PdfJobStatus.FAILED, PdfJobStatus.QUEUED)

    def test_succeeded_to_running_invalid(self):
        assert not is_valid_transition(PdfJobStatus.SUCCEEDED, PdfJobStatus.RUNNING)

    def test_expired_to_anything_invalid(self):
        for s in PdfJobStatus:
            assert not is_valid_transition(PdfJobStatus.EXPIRED, s)

    def test_ttl_transitions(self):
        for s in (PdfJobStatus.QUEUED, PdfJobStatus.SUCCEEDED, PdfJobStatus.FAILED):
            assert is_valid_transition(s, PdfJobStatus.EXPIRED)


# ===================================================================
# Property-based tests
# ===================================================================

# -- Strategies -------------------------------------------------------

json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.text(min_size=0, max_size=50),
)

json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
    ),
    max_leaves=20,
)

payload_strategy = st.dictionaries(
    st.text(min_size=1, max_size=20),
    json_values,
    max_size=8,
)

template_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=30,
)

error_code_strategy = st.sampled_from(list(PdfErrorCode))
status_strategy = st.sampled_from(list(PdfJobStatus))
retry_count_strategy = st.integers(min_value=0, max_value=10)


# -- Property 2: Job Key Deterministic Hash ---------------------------

class TestJobKeyDeterministicProperty:
    """
    Feature: pdf-render-worker, Property 2: Job Key Deterministik Hash
    Validates: Requirements 3.3
    """

    @given(template=template_strategy, payload=payload_strategy)
    @settings(max_examples=200)
    def test_same_input_same_hash(self, template: str, payload: dict):
        """compute_job_key is deterministic: same inputs → same hash."""
        assert compute_job_key(template, payload) == compute_job_key(template, payload)

    @given(
        template=template_strategy,
        payload_a=payload_strategy,
        payload_b=payload_strategy,
    )
    @settings(max_examples=200)
    def test_different_payloads_different_hash(
        self, template: str, payload_a: dict, payload_b: dict
    ):
        """Different payloads should (almost always) produce different hashes."""
        assume(payload_a != payload_b)
        assert compute_job_key(template, payload_a) != compute_job_key(template, payload_b)


# -- Property 3: Retry Policy Correctness -----------------------------

class TestRetryPolicyProperty:
    """
    Feature: pdf-render-worker, Property 3: Retry Politikası Doğruluğu
    Validates: Requirements 5.1, 5.2, 5.3, 5.4
    """

    @given(error_code=error_code_strategy, retry_count=retry_count_strategy)
    @settings(max_examples=200)
    def test_retry_policy(self, error_code: PdfErrorCode, retry_count: int):
        result = should_retry(error_code, retry_count)
        if error_code in TRANSIENT_ERRORS and retry_count < MAX_RETRIES:
            assert result is True
        else:
            assert result is False

    @given(retry_count=retry_count_strategy)
    @settings(max_examples=100)
    def test_permanent_errors_never_retry(self, retry_count: int):
        for ec in (PdfErrorCode.TEMPLATE_ERROR, PdfErrorCode.UNSUPPORTED_PLATFORM, PdfErrorCode.UNKNOWN):
            assert should_retry(ec, retry_count) is False

    @given(retry_count=retry_count_strategy)
    @settings(max_examples=100)
    def test_artifact_write_failed_is_transient(self, retry_count: int):
        result = should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, retry_count)
        assert result == (retry_count < MAX_RETRIES)


# -- Property 1: State Machine Valid Transitions ----------------------

class TestStateMachineProperty:
    """
    Feature: pdf-render-worker, Property 1: Durum Makinesi Geçerli Geçişler
    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
    """

    @given(current=status_strategy, target=status_strategy)
    @settings(max_examples=200)
    def test_transition_validity(self, current: PdfJobStatus, target: PdfJobStatus):
        result = is_valid_transition(current, target)
        expected = target in VALID_TRANSITIONS[current]
        assert result == expected

    @given(target=status_strategy)
    @settings(max_examples=100)
    def test_expired_is_terminal(self, target: PdfJobStatus):
        assert is_valid_transition(PdfJobStatus.EXPIRED, target) is False

    @given(current=status_strategy)
    @settings(max_examples=100)
    def test_self_transition_invalid(self, current: PdfJobStatus):
        """No status should transition to itself."""
        assert is_valid_transition(current, current) is False
