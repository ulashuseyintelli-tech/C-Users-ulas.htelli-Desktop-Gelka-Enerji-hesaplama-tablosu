"""
Tests for PTF write lock — Phase 1 T1.5 (ptf-sot-unification).

Contract:
- Legacy PTF manual write/import/update → blocked (409 or rejected)
- YEKDEM write → still allowed (separate SoT)
- Canonical hourly PTF write → unaffected (pricing/router upload-market)
- Legacy PTF read rollback → still works (use_legacy_ptf=True)

Scope: market_prices.upsert_market_prices, bulk_importer.apply/preview
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.market_prices import upsert_market_prices
from app.bulk_importer import BulkImporter, ImportRow


class TestManualPtfWriteBlocked:
    """T1.5: upsert_market_prices blocks PTF writes."""

    def test_ptf_write_returns_failure(self):
        """PTF upsert → (False, 'manual_ptf_write_disabled...')."""
        db = MagicMock()
        success, msg = upsert_market_prices(
            db=db,
            period="2026-03",
            ptf_tl_per_mwh=2500.0,
            yekdem_tl_per_mwh=400.0,
            price_type="PTF",
        )
        assert success is False
        assert "manual_ptf_write_disabled" in msg

    def test_yekdem_write_still_allowed(self):
        """YEKDEM upsert passes through (not blocked by PTF guard).

        Note: This test verifies the guard doesn't fire for YEKDEM.
        The actual DB write may fail in test (no real DB), but the guard
        should NOT reject it.
        """
        db = MagicMock()
        # YEKDEM price_type with ptf=0 should pass the guard
        success, msg = upsert_market_prices(
            db=db,
            period="2026-03",
            ptf_tl_per_mwh=0.0,  # PTF=0 means YEKDEM-only update
            yekdem_tl_per_mwh=747.8,
            price_type="PTF",
        )
        # Guard should NOT fire (ptf=0 means no PTF write attempt)
        assert "manual_ptf_write_disabled" not in msg


class TestBulkImportPtfBlocked:
    """T1.5: bulk_importer.apply blocks PTF, preview warns."""

    def test_bulk_apply_ptf_rejected(self):
        """Bulk apply with price_type=PTF → all rows rejected."""
        importer = BulkImporter()
        db = MagicMock()
        rows = [
            ImportRow(row_number=1, period="2026-03", value=2500.0, status="final"),
            ImportRow(row_number=2, period="2026-04", value=2600.0, status="final"),
        ]
        result = importer.apply(
            db=db, rows=rows, updated_by="test",
            price_type="PTF",
        )
        assert result.accepted_count == 0
        assert result.rejected_count == 2
        assert any(
            "bulk_ptf_import_disabled" in r.get("error_code", "")
            for r in result.rejected_rows
        )

    def test_bulk_preview_ptf_returns_200_with_warning(self):
        """Bulk preview PTF → 200 (read-only) but includes apply-disabled warning."""
        importer = BulkImporter()
        db = MagicMock()
        # Mock the DB query for preview
        db.query.return_value.filter.return_value.first.return_value = None

        rows = [
            ImportRow(row_number=1, period="2026-03", value=2500.0, status="final",
                     validation_result=None),
        ]
        preview = importer.preview(db=db, rows=rows, price_type="PTF")
        # Preview should succeed (not raise)
        assert preview.total_rows == 1
        # But should contain the PTF_APPLY_DISABLED_WARNING
        warning_codes = [e.get("error_code") for e in preview.errors]
        assert "PTF_APPLY_DISABLED_WARNING" in warning_codes


class TestSampleSeedSkipped:
    """T1.5: _add_sample_market_prices is a no-op with warning log."""

    def test_seed_does_not_raise(self):
        """Startup seed function must not crash the app."""
        from app.main import _add_sample_market_prices
        # Should complete without error
        _add_sample_market_prices()

    def test_seed_logs_warning(self, caplog):
        """Seed function logs explicit skip warning."""
        import logging
        with caplog.at_level(logging.WARNING):
            from app.main import _add_sample_market_prices
            _add_sample_market_prices()
        assert any("SKIPPED" in r.message and "PTF" in r.message for r in caplog.records)
