"""
PDSMR-R1D — validator sonuc tipleri (typed + deterministik).

Owner kurali: sonuc YALNIZ iki degerden biri olabilir —
  PASS            : adoption on-kosullari saglandi
  HARD_STOP(codes): en az bir on-kosul saglanmadi

"WARN", "muhtemelen uygun", "kucuk sapma" gibi ara durum YOKTUR.
Bilinmeyen bir sapma gorulurse sonuc HARD_STOP'tur (fail-closed).

Cagrildigi yerler:
- app/legacy_adoption/validator.py [PDSMR-R1D/Faz2]
- tests/test_legacy_adoption_validator.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Outcome(str, Enum):
    PASS = "PASS"
    HARD_STOP = "HARD_STOP"


# ── Reason code'lar ──────────────────────────────────────────────────────
# Bu tuple ayni zamanda RAPORLAMA SIRASIDIR. Ayni girdi -> ayni sirali
# cikti (deterministik). Yeni kod eklerken sona degil, anlamli yerine ekle.
R_DB_FILE_MISSING = "DB_FILE_MISSING"
R_DB_FILE_UNREADABLE = "DB_FILE_UNREADABLE"
R_DB_NOT_SQLITE = "DB_NOT_SQLITE"
R_INTEGRITY_CHECK_FAILED = "INTEGRITY_CHECK_FAILED"
R_FOREIGN_KEY_VIOLATIONS = "FOREIGN_KEY_VIOLATIONS"
R_ALEMBIC_VERSION_MISSING = "ALEMBIC_VERSION_MISSING"
R_ALEMBIC_REVISION_NOT_ALLOWLISTED = "ALEMBIC_REVISION_NOT_ALLOWLISTED"
R_TABLE_COUNT_MISMATCH = "TABLE_COUNT_MISMATCH"
R_EXPECTED_TABLE_MISSING = "EXPECTED_TABLE_MISSING"
R_UNKNOWN_TABLE_PRESENT = "UNKNOWN_TABLE_PRESENT"
R_FORBIDDEN_TABLE_PRESENT = "FORBIDDEN_TABLE_PRESENT"
R_LEGACY_TABLE_MISSING = "LEGACY_TABLE_MISSING"
R_LEGACY_TABLE_NOT_EMPTY = "LEGACY_TABLE_NOT_EMPTY"
R_EXPECTED_COLUMN_MISSING = "EXPECTED_COLUMN_MISSING"
R_UNKNOWN_COLUMN_PRESENT = "UNKNOWN_COLUMN_PRESENT"
R_COLUMN_AFFINITY_DRIFT = "COLUMN_AFFINITY_DRIFT"
R_COLUMN_NULLABILITY_DRIFT = "COLUMN_NULLABILITY_DRIFT"
R_PRIMARY_KEY_DRIFT = "PRIMARY_KEY_DRIFT"
R_FOREIGN_KEY_DRIFT = "FOREIGN_KEY_DRIFT"
R_UNEXPECTED_CHECK_CONSTRAINT = "UNEXPECTED_CHECK_CONSTRAINT"
R_REQUIRED_INDEX_MISSING = "REQUIRED_INDEX_MISSING"
R_UNKNOWN_INDEX_PRESENT = "UNKNOWN_INDEX_PRESENT"
R_UNEXPECTED_VIEW_PRESENT = "UNEXPECTED_VIEW_PRESENT"
R_UNEXPECTED_TRIGGER_PRESENT = "UNEXPECTED_TRIGGER_PRESENT"
R_ROW_COUNT_BASELINE_MISMATCH = "ROW_COUNT_BASELINE_MISMATCH"
R_BACKUP_TARGET_NOT_WRITABLE = "BACKUP_TARGET_NOT_WRITABLE"
R_BACKUP_INSUFFICIENT_FREE_SPACE = "BACKUP_INSUFFICIENT_FREE_SPACE"
R_EVIDENCE_SANITIZATION_FAILED = "EVIDENCE_SANITIZATION_FAILED"

REASON_CODE_ORDER: tuple[str, ...] = (
    R_DB_FILE_MISSING,
    R_DB_FILE_UNREADABLE,
    R_DB_NOT_SQLITE,
    R_INTEGRITY_CHECK_FAILED,
    R_FOREIGN_KEY_VIOLATIONS,
    R_ALEMBIC_VERSION_MISSING,
    R_ALEMBIC_REVISION_NOT_ALLOWLISTED,
    R_TABLE_COUNT_MISMATCH,
    R_EXPECTED_TABLE_MISSING,
    R_UNKNOWN_TABLE_PRESENT,
    R_FORBIDDEN_TABLE_PRESENT,
    R_LEGACY_TABLE_MISSING,
    R_LEGACY_TABLE_NOT_EMPTY,
    R_EXPECTED_COLUMN_MISSING,
    R_UNKNOWN_COLUMN_PRESENT,
    R_COLUMN_AFFINITY_DRIFT,
    R_COLUMN_NULLABILITY_DRIFT,
    R_PRIMARY_KEY_DRIFT,
    R_FOREIGN_KEY_DRIFT,
    R_UNEXPECTED_CHECK_CONSTRAINT,
    R_REQUIRED_INDEX_MISSING,
    R_UNKNOWN_INDEX_PRESENT,
    R_UNEXPECTED_VIEW_PRESENT,
    R_UNEXPECTED_TRIGGER_PRESENT,
    R_ROW_COUNT_BASELINE_MISMATCH,
    R_BACKUP_TARGET_NOT_WRITABLE,
    R_BACKUP_INSUFFICIENT_FREE_SPACE,
    R_EVIDENCE_SANITIZATION_FAILED,
)

_ORDER_INDEX = {code: i for i, code in enumerate(REASON_CODE_ORDER)}


@dataclass(frozen=True)
class Finding:
    """Tek bir on-kosul ihlali. `detail` yalniz sema/sayim bilgisi icerir."""

    reason_code: str
    detail: str

    @property
    def sort_key(self) -> tuple[int, str]:
        return (_ORDER_INDEX.get(self.reason_code, len(REASON_CODE_ORDER)), self.detail)


@dataclass
class ValidationReport:
    outcome: Outcome
    findings: tuple[Finding, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)
    accepted_variants: tuple[str, ...] = ()

    @property
    def is_pass(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def reason_codes(self) -> tuple[str, ...]:
        """Tekrarsiz, deterministik sirali reason code listesi."""
        seen: list[str] = []
        for f in self.findings:
            if f.reason_code not in seen:
                seen.append(f.reason_code)
        return tuple(seen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason_codes": list(self.reason_codes),
            "findings": [{"reason_code": f.reason_code, "detail": f.detail} for f in self.findings],
            "accepted_variants": list(self.accepted_variants),
            "evidence": self.evidence,
        }


def build_report(
    findings: list[Finding],
    evidence: Optional[dict[str, Any]] = None,
    accepted_variants: Optional[list[str]] = None,
) -> ValidationReport:
    """Bulgulari deterministik siraya sokup typed rapora cevirir."""
    ordered = tuple(sorted(findings, key=lambda f: f.sort_key))
    return ValidationReport(
        outcome=Outcome.HARD_STOP if ordered else Outcome.PASS,
        findings=ordered,
        evidence=evidence or {},
        accepted_variants=tuple(sorted(accepted_variants or [])),
    )
