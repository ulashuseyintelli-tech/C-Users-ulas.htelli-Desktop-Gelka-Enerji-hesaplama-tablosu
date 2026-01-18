"""
Golden Incident Tests - Sprint 5.1

Gercek fatura fixture'lari ile incident ciktisini dogrular.
Tum lookup'lar stub edilir - network cagirisi yok.
"""

import pytest
import json
from pathlib import Path
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from backend.app.incident_service import (
    calculate_quality_score,
    create_incidents_from_quality,
    QualityScore,
)
from backend.tests.utils.snapshot import normalize_incident, compare_incidents


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "invoices"
EXPECTED_FIXTURE_VERSION = 1


@dataclass
class FakeMarketPriceProvider:
    """PTF/YEKDEM stub provider"""
    prices: Dict[str, float]
    
    def get_price(self, date: str, hour: int = 0) -> Optional[float]:
        return self.prices.get(date)
    
    def get_ptf(self, date: str) -> Optional[float]:
        return self.prices.get(f"ptf_{date}")
    
    def get_yekdem(self, date: str) -> Optional[float]:
        return self.prices.get(f"yekdem_{date}")


@dataclass
class FakeTariffProvider:
    """EPDK tariff stub provider"""
    tariffs: Dict[str, Dict[str, float]]
    
    def get_distribution_tariff(self, key: str) -> Optional[Dict[str, float]]:
        return self.tariffs.get(key)
    
    def lookup(self, region: str, tariff_type: str, voltage: str) -> Optional[Dict[str, float]]:
        key = f"{region}_{tariff_type}_{voltage}"
        return self.tariffs.get(key)


def load_fixture(provider: str, period: str, invoice_id: str) -> Dict[str, Any]:
    """
    Fixture dosyalarini yukler.
    
    Struktur:
    fixtures/invoices/<provider>/<period>/<invoice_id>/
        meta.json (required - version check)
        extraction.json
        validation.json
        lookup_stub.json
        expected_incident.json
    """
    fixture_path = FIXTURES_DIR / provider / period / invoice_id
    
    if not fixture_path.exists():
        pytest.skip(f"Fixture not found: {fixture_path}")
    
    result = {}
    
    # Meta (required - version check)
    meta_file = fixture_path / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8-sig") as f:
            meta = json.load(f)
            if meta.get("fixture_version") != EXPECTED_FIXTURE_VERSION:
                pytest.fail(f"Fixture version mismatch: {fixture_path} got {meta.get('fixture_version')}, expected {EXPECTED_FIXTURE_VERSION}")
            result["meta"] = meta
    else:
        pytest.fail(f"meta.json required but not found: {fixture_path}")
    
    # Extraction
    extraction_file = fixture_path / "extraction.json"
    if extraction_file.exists():
        with open(extraction_file, "r", encoding="utf-8-sig") as f:
            result["extraction"] = json.load(f)
    else:
        result["extraction"] = {}
    
    # Validation
    validation_file = fixture_path / "validation.json"
    if validation_file.exists():
        with open(validation_file, "r", encoding="utf-8-sig") as f:
            result["validation"] = json.load(f)
    else:
        result["validation"] = {}
    
    # Calculation (optional)
    calculation_file = fixture_path / "calculation.json"
    if calculation_file.exists():
        with open(calculation_file, "r", encoding="utf-8-sig") as f:
            result["calculation"] = json.load(f)
    else:
        result["calculation"] = None
    
    # Lookup stubs
    lookup_file = fixture_path / "lookup_stub.json"
    if lookup_file.exists():
        with open(lookup_file, "r", encoding="utf-8-sig") as f:
            result["lookup_stub"] = json.load(f)
    else:
        result["lookup_stub"] = {}
    
    # Expected incident
    expected_file = fixture_path / "expected_incident.json"
    if expected_file.exists():
        with open(expected_file, "r", encoding="utf-8-sig") as f:
            result["expected_incident"] = json.load(f)
    else:
        result["expected_incident"] = None
    
    return result


def get_available_fixtures():
    """Mevcut fixture'lari listeler."""
    fixtures = []
    
    if not FIXTURES_DIR.exists():
        return fixtures
    
    for provider_dir in FIXTURES_DIR.iterdir():
        if not provider_dir.is_dir():
            continue
        provider = provider_dir.name
        
        for period_dir in provider_dir.iterdir():
            if not period_dir.is_dir():
                continue
            period = period_dir.name
            
            for invoice_dir in period_dir.iterdir():
                if not invoice_dir.is_dir():
                    continue
                invoice_id = invoice_dir.name
                
                # expected_incident.json olmali
                if (invoice_dir / "expected_incident.json").exists():
                    fixtures.append((provider, period, invoice_id))
    
    return fixtures


class TestGoldenIncidents:
    """Golden incident testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        db = MagicMock()
        return db
    
    def run_golden_test(
        self,
        mock_db,
        provider: str,
        period: str,
        invoice_id: str
    ):
        """
        Tek bir golden test calistirir.
        
        1. Fixture'i yukle
        2. Lookup'lari stub et
        3. Quality score hesapla
        4. Incident olustur
        5. Expected ile karsilastir
        """
        fixture = load_fixture(provider, period, invoice_id)
        
        if fixture["expected_incident"] is None:
            pytest.skip(f"No expected_incident.json for {provider}/{period}/{invoice_id}")
        
        # Quality score hesapla
        quality = calculate_quality_score(
            extraction=fixture["extraction"],
            validation=fixture["validation"],
            calculation=fixture["calculation"],
            calculation_error=None,
            debug_meta=None
        )
        
        # Incident olustur (mock db ile)
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id=f"golden-{invoice_id}",
                quality=quality,
                tenant_id="test",
                invoice_id=invoice_id,
                period=period
            )
            
            if mock_create.call_count == 0:
                # Incident olusturulmadi - expected da bos olmali
                if fixture["expected_incident"].get("primary_flag") is not None:
                    pytest.fail(
                        f"Expected incident but none created. "
                        f"Expected: {fixture['expected_incident']}"
                    )
                return
            
            # create_incident cagrisini al
            call_kwargs = mock_create.call_args[1]
            
            # Actual incident'i olustur
            actual_incident = {
                "category": call_kwargs["category"],
                "severity": call_kwargs["severity"],
                "details": call_kwargs["details"],
            }
            
            # Normalize et
            actual_normalized = normalize_incident(actual_incident)
            expected_normalized = fixture["expected_incident"]
            
            # Karsilastir
            errors = compare_incidents(actual_normalized, expected_normalized)
            
            if errors:
                pytest.fail(
                    f"Golden test failed for {provider}/{period}/{invoice_id}:\n" +
                    "\n".join(errors)
                )


# Dinamik test generation
def pytest_generate_tests(metafunc):
    """Mevcut fixture'lar icin dinamik test olusturur."""
    if "golden_fixture" in metafunc.fixturenames:
        fixtures = get_available_fixtures()
        if fixtures:
            metafunc.parametrize(
                "golden_fixture",
                fixtures,
                ids=[f"{p}/{d}/{i}" for p, d, i in fixtures]
            )


class TestGoldenIncidentsParametrized:
    """Parametrize edilmis golden testler"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    @pytest.fixture
    def golden_fixture(self):
        """Placeholder - pytest_generate_tests tarafindan doldurulur"""
        return None
    
    def test_golden_incident(self, mock_db, golden_fixture):
        """Her fixture icin golden test"""
        if golden_fixture is None:
            pytest.skip("No fixtures available")
        
        provider, period, invoice_id = golden_fixture
        
        tester = TestGoldenIncidents()
        tester.run_golden_test(mock_db, provider, period, invoice_id)


# Manuel test - fixture olmadan da calisir
class TestGoldenIncidentsManual:
    """Manuel golden testler - fixture'siz"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_market_price_missing_scenario(self, mock_db):
        """MARKET_PRICE_MISSING senaryosu"""
        validation = {}
        calculation = {
            "meta_pricing_source": "not_found",
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "MARKET_PRICE_MISSING" in quality.flags
        
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id="test-market-price",
                quality=quality
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["details"]["primary_flag"] == "MARKET_PRICE_MISSING"
    
    def test_tariff_meta_missing_scenario(self, mock_db):
        """TARIFF_META_MISSING senaryosu"""
        validation = {
            "distribution_tariff_meta_missing": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_META_MISSING" in quality.flags
        
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id="test-tariff-meta",
                quality=quality
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["details"]["primary_flag"] == "TARIFF_META_MISSING"
    
    def test_calc_bug_scenario(self, mock_db):
        """CALC_BUG senaryosu"""
        validation = {
            "distribution_computed_from_tariff": True,
        }
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "CALC_BUG" in quality.flags
        
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id="test-calc-bug",
                quality=quality
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["details"]["primary_flag"] == "CALC_BUG"
    
    def test_distribution_mismatch_scenario(self, mock_db):
        """DISTRIBUTION_MISMATCH senaryosu"""
        validation = {
            "distribution_line_mismatch": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "DISTRIBUTION_MISMATCH" in quality.flags
        
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id="test-dist-mismatch",
                quality=quality
            )
            
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["details"]["primary_flag"] == "DISTRIBUTION_MISMATCH"
    
    def test_multiple_flags_primary_selection(self, mock_db):
        """Birden fazla flag - primary secimi"""
        validation = {
            "distribution_tariff_meta_missing": True,
        }
        calculation = {
            "meta_pricing_source": "not_found",
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        # Her iki flag da olmali
        assert "TARIFF_META_MISSING" in quality.flags
        assert "MARKET_PRICE_MISSING" in quality.flags
        
        with patch('backend.app.incident_service.create_incident') as mock_create:
            mock_create.return_value = 1
            
            create_incidents_from_quality(
                db=mock_db,
                trace_id="test-multi-flag",
                quality=quality
            )
            
            call_kwargs = mock_create.call_args[1]
            # MARKET_PRICE_MISSING (10) < TARIFF_META_MISSING (25)
            assert call_kwargs["details"]["primary_flag"] == "MARKET_PRICE_MISSING"
            assert "TARIFF_META_MISSING" in call_kwargs["details"]["secondary_flags"]
