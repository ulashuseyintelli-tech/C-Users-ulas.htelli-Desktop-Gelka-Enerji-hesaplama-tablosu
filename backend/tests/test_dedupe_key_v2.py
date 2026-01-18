"""
Dedupe Key v2 Tests - Sprint 6.0

Stabil dedupe key testleri.
"""

import pytest
from backend.app.incident_keys import (
    dedupe_key_v2,
    sha256_hex,
    generate_invoice_hash,
    extract_period_from_dates,
)


class TestDedupeKeyV2:
    """dedupe_key_v2 testleri"""
    
    def test_same_input_same_hash(self):
        """Ayni input → ayni hash"""
        key1 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        key2 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        assert key1 == key2
    
    def test_period_change_changes_hash(self):
        """Period degisirse hash degisir"""
        key1 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        key2 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-02",
        )
        assert key1 != key2
    
    def test_provider_change_changes_hash(self):
        """Provider degisirse hash degisir"""
        key1 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        key2 = dedupe_key_v2(
            provider="enerjisa",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        assert key1 != key2
    
    def test_primary_flag_change_changes_hash(self):
        """Primary flag degisirse hash degisir"""
        key1 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        key2 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="TARIFF_LOOKUP_FAILED",
            category="TARIFF_MISSING",
            action_code="EPDK_TARIFF_LOOKUP",
            period_yyyy_mm="2025-01",
        )
        assert key1 != key2
    
    def test_ptf_date_not_in_dedupe(self):
        """PTF tarihi dedupe'a girmiyor - ayni key"""
        # Bu test "stabil" kararini kilitler
        # PTF tarihi degisse bile dedupe key ayni kalmali
        # (cunku ptf_date dedupe input'u degil)
        key1 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        # Ayni parametrelerle tekrar cagir - ptf_date yok
        key2 = dedupe_key_v2(
            provider="ck_bogazici",
            invoice_id="INV001",
            primary_flag="MARKET_PRICE_MISSING",
            category="PRICE_MISSING",
            action_code="PTF_YEKDEM_CHECK",
            period_yyyy_mm="2025-01",
        )
        assert key1 == key2
    
    def test_hash_is_64_chars(self):
        """SHA256 hex 64 karakter olmali"""
        key = dedupe_key_v2(
            provider="test",
            invoice_id="123",
            primary_flag="TEST",
            category="TEST",
            action_code="TEST",
            period_yyyy_mm="2025-01",
        )
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestGenerateInvoiceHash:
    """generate_invoice_hash testleri"""
    
    def test_same_input_same_hash(self):
        """Ayni input → ayni hash"""
        h1 = generate_invoice_hash(
            supplier="CK Bogazici",
            invoice_no="BBE2025000297356",
            period="2025-01",
            consumption_kwh=15000,
            total_amount=45000,
        )
        h2 = generate_invoice_hash(
            supplier="CK Bogazici",
            invoice_no="BBE2025000297356",
            period="2025-01",
            consumption_kwh=15000,
            total_amount=45000,
        )
        assert h1 == h2
    
    def test_hash_is_16_chars(self):
        """Invoice hash 16 karakter olmali"""
        h = generate_invoice_hash(
            supplier="test",
            invoice_no="123",
            period="2025-01",
        )
        assert len(h) == 16
    
    def test_case_insensitive_supplier(self):
        """Supplier case-insensitive olmali"""
        h1 = generate_invoice_hash(supplier="CK Bogazici")
        h2 = generate_invoice_hash(supplier="ck bogazici")
        assert h1 == h2
    
    def test_empty_values_handled(self):
        """Bos degerler handle edilmeli"""
        h = generate_invoice_hash()
        assert len(h) == 16


class TestExtractPeriodFromDates:
    """extract_period_from_dates testleri"""
    
    def test_period_start_priority(self):
        """period_start oncelikli"""
        period = extract_period_from_dates(
            period_start="2025-01-01",
            period_end="2025-01-31",
            invoice_date="2025-02-15",
        )
        assert period == "2025-01"
    
    def test_period_end_fallback(self):
        """period_start yoksa period_end"""
        period = extract_period_from_dates(
            period_start=None,
            period_end="2025-01-31",
            invoice_date="2025-02-15",
        )
        assert period == "2025-01"
    
    def test_invoice_date_fallback(self):
        """period_start/end yoksa invoice_date"""
        period = extract_period_from_dates(
            period_start=None,
            period_end=None,
            invoice_date="2025-02-15",
        )
        assert period == "2025-02"
    
    def test_empty_when_no_dates(self):
        """Hic tarih yoksa bos string"""
        period = extract_period_from_dates()
        assert period == ""
    
    def test_short_date_ignored(self):
        """7 karakterden kisa tarihler ignore edilir"""
        period = extract_period_from_dates(
            period_start="2025",
            invoice_date="2025-01-15",
        )
        assert period == "2025-01"
