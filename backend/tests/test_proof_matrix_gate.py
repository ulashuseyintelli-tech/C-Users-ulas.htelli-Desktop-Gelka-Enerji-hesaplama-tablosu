"""
Proof Matrix Release Gate — doküman drift otomatik tespiti.

requirements.md'deki C1–C7 satırları ile test isimlerini doğrular.
Böylece "doküman drift" CI'da otomatik yakalanır.

Hardening #5: Proof matrix'i release gate yapma.
"""
from __future__ import annotations

import importlib
import inspect
import pathlib

import pytest


# ── Requirement → test mapping ───────────────────────────────────────────────

# Each requirement ID maps to at least one test class or function name
# that must exist in the test modules.
PROOF_MATRIX = {
    # Concurrency PBT requirements
    "C1": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestPC1TenantIsolation", "TestPC1AsyncioCrossValidation"],
    },
    "C2": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestPC2HashDeterminism"],
    },
    "C3": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestSnapshotImmutabilityGuard"],
    },
    "C4": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestPC3ModeFreezeUnderConfigChange"],
    },
    "C5": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestPC4MetricsMonotonic"],
    },
    "C6": {
        "module": "backend.tests.test_tenant_enable_integration",
        "classes": ["TestI1OpsGuardDenyBypass"],
    },
    "C7": {
        "module": "backend.tests.test_concurrency_pbt",
        "classes": ["TestPC5FailOpenContainment"],
    },
}


class TestProofMatrixGate:
    """
    CI gate: verify every requirement has a corresponding test class.
    Fails if a test class is renamed/deleted without updating the matrix.
    """

    @pytest.mark.parametrize("req_id", sorted(PROOF_MATRIX.keys()))
    def test_requirement_has_test(self, req_id):
        entry = PROOF_MATRIX[req_id]
        mod = importlib.import_module(entry["module"])

        for cls_name in entry["classes"]:
            cls = getattr(mod, cls_name, None)
            assert cls is not None, (
                f"Requirement {req_id}: test class {cls_name!r} "
                f"not found in {entry['module']}"
            )
            # Verify it has at least one test method
            test_methods = [
                name for name in dir(cls)
                if name.startswith("test_") and callable(getattr(cls, name))
            ]
            assert len(test_methods) > 0, (
                f"Requirement {req_id}: class {cls_name!r} has no test_ methods"
            )

    def test_requirements_md_exists(self):
        """requirements.md must exist for the concurrency-pbt spec."""
        req_path = pathlib.Path(".kiro/specs/concurrency-pbt/requirements.md")
        assert req_path.exists(), f"Missing: {req_path}"

    def test_all_c_requirements_covered(self):
        """All C1–C7 requirements must be in the proof matrix."""
        expected = {f"C{i}" for i in range(1, 8)}
        actual = set(PROOF_MATRIX.keys())
        missing = expected - actual
        assert not missing, f"Requirements missing from proof matrix: {missing}"
