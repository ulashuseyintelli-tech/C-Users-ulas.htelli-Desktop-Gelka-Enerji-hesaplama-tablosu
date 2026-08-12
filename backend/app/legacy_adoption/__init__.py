"""
PDSMR-R1D — legacy DB adoption ON-DOGRULAMASI (Faz 2).

Bu paket YALNIZCA DOGRULAR. Adoption'i (Faz 3) UYGULAMAZ; owner tarafindan
yetkilendirilmemistir. Pakette bilincli olarak migration calistiran, stamp
atan veya kaynak DB'ye yazan HICBIR kod yoktur.

Katmanlar ayridir:
  fingerprint.py — salt-okunur olcum      (politika bilmez)
  policy.py      — acik allowlist         (DB'ye dokunmaz)
  result.py      — typed sonuc + siralama (mantik icermez)
  validator.py   — ucunu birlestiren karar

Cagrildigi yerler:
- tests/test_legacy_adoption_validator.py
"""
from .fingerprint import DatabaseFingerprint, collect_fingerprint
from .result import Finding, Outcome, ValidationReport
from .validator import assert_evidence_sanitized, validate_legacy_db

__all__ = [
    "DatabaseFingerprint",
    "Finding",
    "Outcome",
    "ValidationReport",
    "assert_evidence_sanitized",
    "collect_fingerprint",
    "validate_legacy_db",
]
