"""
PR-13/15: Release Governance Pack — smoke tests + PBT + drift guard.

Validates that all release-governance modules are importable,
public classes are instantiable, spec_hash is deterministic,
reason code table covers all BlockReasonCode entries,
and README reason code table matches generator output (drift guard).

Unit tests + PBT (P15, P16) + drift guard.
"""
import pytest
from pathlib import Path
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st


# ===================================================================
# Unit Tests — Import Smoke
# ===================================================================

class TestImportSmoke:
    """Req 5.2: all modules importable"""

    def test_import_release_policy(self):
        from backend.app.testing.release_policy import (
            ReleasePolicy,
            ReleaseVerdict,
            BlockReasonCode,
            ABSOLUTE_BLOCK_REASONS,
            ReleasePolicyInput,
            ReleasePolicyResult,
        )
        assert ReleasePolicy is not None
        assert len(BlockReasonCode) == 10

    def test_import_release_report(self):
        from backend.app.testing.release_report import (
            ReleaseReportGenerator,
            ReleaseReport,
            TierSummary,
            DriftSummary,
        )
        assert ReleaseReportGenerator is not None

    def test_import_release_gate(self):
        from backend.app.testing.release_gate import (
            ReleaseGate,
            GateDecision,
            ReleaseOverride,
        )
        assert ReleaseGate is not None

    def test_import_release_version(self):
        from backend.app.testing.release_version import (
            spec_hash,
            generate_reason_code_table,
            VERSION,
        )
        assert callable(spec_hash)
        assert callable(generate_reason_code_table)
        assert VERSION == "1.0.0"


# ===================================================================
# Unit Tests — Instantiation
# ===================================================================

class TestInstantiation:
    """Req 5.3: public classes instantiable"""

    def test_release_policy_instantiable(self):
        from backend.app.testing.release_policy import ReleasePolicy
        p = ReleasePolicy()
        assert p is not None

    def test_release_report_generator_instantiable(self):
        from backend.app.testing.release_report import ReleaseReportGenerator
        g = ReleaseReportGenerator()
        assert g is not None

    def test_release_gate_instantiable(self):
        from backend.app.testing.release_gate import ReleaseGate
        g = ReleaseGate()
        assert g is not None


# ===================================================================
# Unit Tests — spec_hash
# ===================================================================

class TestSpecHash:
    """Req 5.4: spec_hash callable and deterministic"""

    def test_spec_hash_not_empty(self):
        from backend.app.testing.release_version import spec_hash
        h = spec_hash()
        assert h is not None
        assert len(h) == 64  # SHA-256 hex

    def test_spec_hash_deterministic(self):
        from backend.app.testing.release_version import spec_hash
        h1 = spec_hash()
        h2 = spec_hash()
        assert h1 == h2

    def test_spec_hash_is_hex(self):
        from backend.app.testing.release_version import spec_hash
        h = spec_hash()
        int(h, 16)  # raises ValueError if not valid hex


# ===================================================================
# Unit Tests — Reason Code Table
# ===================================================================

class TestReasonCodeTable:
    """Req 5.5: reason code table producible and complete"""

    def test_table_not_empty(self):
        from backend.app.testing.release_version import generate_reason_code_table
        table = generate_reason_code_table()
        assert len(table) > 0

    def test_table_contains_all_codes(self):
        from backend.app.testing.release_policy import BlockReasonCode
        from backend.app.testing.release_version import generate_reason_code_table
        table = generate_reason_code_table()
        for code in BlockReasonCode:
            assert code.value in table, f"{code.value} missing from table"

    def test_table_is_markdown(self):
        from backend.app.testing.release_version import generate_reason_code_table
        table = generate_reason_code_table()
        lines = table.strip().split("\n")
        # Header + separator + at least 1 data row
        assert len(lines) >= 3
        assert lines[0].startswith("|")
        assert "---" in lines[1]

    def test_table_deterministic(self):
        from backend.app.testing.release_version import generate_reason_code_table
        t1 = generate_reason_code_table()
        t2 = generate_reason_code_table()
        assert t1 == t2

    def test_table_absolute_blocks_marked(self):
        """GUARD_VIOLATION and OPS_GATE_FAIL must show non-overridable"""
        from backend.app.testing.release_version import generate_reason_code_table
        table = generate_reason_code_table()
        # Find lines containing absolute block codes
        for code_val in ("GUARD_VIOLATION", "OPS_GATE_FAIL"):
            matching = [l for l in table.split("\n") if code_val in l]
            assert len(matching) == 1
            assert "❌" in matching[0] or "Hayır" in matching[0]


# ===================================================================
# PBT — Property 15: Spec Hash Determinism
# ===================================================================

class TestPBTSpecHashDeterminism:
    """
    Property 15: Spec hash determinizmi
    **Validates: Requirements 4.3, 4.5**
    """

    @given(n=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_spec_hash_always_same(self, n: int):
        # Feature: release-governance-pack, Property 15: Spec hash determinizmi
        from backend.app.testing.release_version import spec_hash
        hashes = [spec_hash() for _ in range(n)]
        assert all(h == hashes[0] for h in hashes)


# ===================================================================
# PBT — Property 16: Reason Code Table Completeness
# ===================================================================

class TestPBTReasonCodeTableCompleteness:
    """
    Property 16: Reason code tablosu bütünlüğü
    **Validates: Requirements 2.1, 2.2, 2.3**
    """

    @given(n=st.integers(min_value=1, max_value=5))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_table_always_complete_and_deterministic(self, n: int):
        # Feature: release-governance-pack, Property 16: Reason code tablosu bütünlüğü
        from backend.app.testing.release_policy import BlockReasonCode
        from backend.app.testing.release_version import generate_reason_code_table

        tables = [generate_reason_code_table() for _ in range(n)]

        # Deterministic
        assert all(t == tables[0] for t in tables)

        # Complete
        for code in BlockReasonCode:
            assert code.value in tables[0]


# ===================================================================
# Drift Guard — README reason code table exact match
# ===================================================================

_README_PATH = Path(__file__).resolve().parents[2] / ".kiro" / "specs" / "release-governance" / "README.md"
_BEGIN_MARKER = "<!-- REASON_CODE_TABLE:BEGIN -->"
_END_MARKER = "<!-- REASON_CODE_TABLE:END -->"


def _extract_readme_table() -> str:
    """Extract reason code table from README between markers."""
    content = _README_PATH.read_text(encoding="utf-8")
    begin = content.index(_BEGIN_MARKER) + len(_BEGIN_MARKER)
    end = content.index(_END_MARKER)
    return content[begin:end].strip()


class TestReasonCodeTableDriftGuard:
    """
    T3: Reason code table drift guard.
    README'deki tablo ile generate_reason_code_table() çıktısı
    tam eşleşmeli (exact match). Enum/description değişince
    bu test kırılır → README güncellenmeden merge olmaz.
    """

    def test_readme_table_matches_generator_exact(self):
        from backend.app.testing.release_version import generate_reason_code_table

        generated = generate_reason_code_table().strip()
        readme_table = _extract_readme_table()

        # Normalize line endings for cross-OS stability
        generated_lines = [l.strip() for l in generated.splitlines()]
        readme_lines = [l.strip() for l in readme_table.splitlines()]

        assert generated_lines == readme_lines, (
            "README reason code table does not match generator output.\n"
            "Run generate_reason_code_table() and update README between "
            "REASON_CODE_TABLE markers.\n"
            f"Generated ({len(generated_lines)} lines) vs "
            f"README ({len(readme_lines)} lines)"
        )

    def test_readme_markers_exist(self):
        """Markers must exist in README for drift guard to work."""
        content = _README_PATH.read_text(encoding="utf-8")
        assert _BEGIN_MARKER in content, f"Missing {_BEGIN_MARKER} in README"
        assert _END_MARKER in content, f"Missing {_END_MARKER} in README"
