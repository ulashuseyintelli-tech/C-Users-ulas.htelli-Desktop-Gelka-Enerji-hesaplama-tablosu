"""
PR-13: Release Governance Pack — spec hash + reason code table.

Utility functions for traceability and adoption. No decision logic.

spec_hash():
  - Covers: release_policy.py, release_report.py, release_gate.py
  - Canonicalization: files read in lexicographic order, content as-is (bytes)
  - Algorithm: SHA-256
  - Deterministic: same file contents → same hash

generate_reason_code_table():
  - Source: BlockReasonCode enum + _ACTION_DESCRIPTIONS + ABSOLUTE_BLOCK_REASONS
  - Output: markdown table string
  - Order: BlockReasonCode enum definition order (no hardcoded list)
  - Deterministic: same source → same output
"""
from __future__ import annotations

import hashlib
from pathlib import Path

VERSION = "1.0.0"

from backend.app.testing.release_policy import (
    ABSOLUTE_BLOCK_REASONS,
    BlockReasonCode,
    _ACTION_DESCRIPTIONS,
)

# Files covered by spec_hash (lexicographic order)
_SPEC_FILES = sorted([
    "backend/app/testing/release_policy.py",
    "backend/app/testing/release_report.py",
    "backend/app/testing/release_gate.py",
])


def spec_hash(base_dir: str | Path | None = None) -> str:
    """
    SHA-256 hash of the three release governance modules.

    Files are read in lexicographic order. Content is read as raw bytes
    (no LF/CRLF normalization) to keep the hash stable per-platform.

    Returns 64-char lowercase hex string.
    """
    h = hashlib.sha256()
    if base_dir is None:
        base = Path(__file__).resolve().parent.parent.parent.parent
    else:
        base = Path(base_dir)
    for rel_path in _SPEC_FILES:
        content = (base / rel_path).read_bytes()
        h.update(content)
    return h.hexdigest()


def generate_reason_code_table() -> str:
    """
    Markdown table of all BlockReasonCode entries.

    Columns: Neden Kodu | Verdict | Aksiyon | Override?
    Order: BlockReasonCode enum definition order (reflection, not hardcoded).
    Source: _ACTION_DESCRIPTIONS dict + ABSOLUTE_BLOCK_REASONS frozenset.
    """
    lines: list[str] = []
    lines.append("| Neden Kodu | Verdict | Aksiyon | Override? |")
    lines.append("|---|---|---|---|")

    for code in BlockReasonCode:
        # Determine verdict level
        if code in ABSOLUTE_BLOCK_REASONS:
            verdict = "BLOCK"
            override = "❌ Hayır (sözleşme ihlali)"
        elif code in (
            BlockReasonCode.NO_TIER_DATA,
            BlockReasonCode.NO_FLAKE_DATA,
        ):
            verdict = "BLOCK"
            override = "N/A (veri eksik)"
        elif code in (
            BlockReasonCode.NO_DRIFT_DATA,
            BlockReasonCode.NO_CANARY_DATA,
        ):
            verdict = "HOLD"
            override = "✅ Evet"
        else:
            verdict = "HOLD"
            override = "✅ Evet"

        description = _ACTION_DESCRIPTIONS.get(code, "—")
        lines.append(f"| {code.value} | {verdict} | {description} | {override} |")

    return "\n".join(lines)
