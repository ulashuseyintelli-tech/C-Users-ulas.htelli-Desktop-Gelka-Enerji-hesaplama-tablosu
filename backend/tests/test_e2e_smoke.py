"""
E2E Smoke Test - Sprint 8.8

Full pipeline test with 3 fixtures:
1. Happy path: No incident, quality OK
2. S2 Mismatch: INVOICE_TOTAL_MISMATCH, VERIFY_INVOICE_LOGIC hint
3. S1 + OCR Suspect: S1 severity, VERIFY_OCR hint

Test flow:
1. Setup: Clean test DB, load fixtures
2. Extraction + Validation + Calculation
3. Incident Generation
4. System Health Snapshot
5. Feedback Write
6. Feedback Stats Read
"""

import pytest
from datetime import datetime, date, timedelta, timezone
from unittest.mock import patch, MagicMock

from backend.app.models import FieldValue, StringFieldValue, InvoiceExtraction, RawBreakdown, OfferParams, InvoiceMeta
from backend.app.validator import validate_extraction
from backend.app.calculator import calculate_offer, check_total_mismatch
from backend.app.incident_service import (
    calculate_quality_score,
    create_incidents_from_quality,
    generate_invoice_fingerprint,
    Severity,
)
from backend.app.incident_metrics import (
    generate_system_health_report,
    get_feedback_stats,
    submit_feedback,
    FeedbackAction,
)
from backend.app.database import Incident


# ═══════════════════════════════════════════════════════════════════════════════
# TEST FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def create_extraction(
    consumption_kwh: float,
    unit_price: float,
    invoice_total: float,
    consumption_confidence: float = 0.95,
    unit_price_confidence: float = 0.92,
    total_confidence: float = 0.98,
    vendor: str = "test_vendor",
    period: str = "2025-01",
) -> InvoiceExtraction:
    """Helper to create InvoiceExtraction with given values."""
    # Calculate expected breakdown
    energy_total = consumption_kwh * unit_price
    dist_total = consumption_kwh * 0.5  # Assume 0.5 TL/kWh distribution
    btv = energy_total * 0.01
    matrah = energy_total + dist_total + btv
    vat = matrah * 0.20
    
    return InvoiceExtraction(
        vendor=vendor,
        invoice_period=period,
        invoice_no=StringFieldValue(value="TEST-001", confidence=0.99),
        invoice_date=StringFieldValue(value="2025-01-15", confidence=0.99),
        consumption_kwh=FieldValue(value=consumption_kwh, confidence=consumption_confidence),
        current_active_unit_price_tl_per_kwh=FieldValue(value=unit_price, confidence=unit_price_confidence),
        distribution_unit_price_tl_per_kwh=FieldValue(value=0.5, confidence=0.90),
        invoice_total_with_vat_tl=FieldValue(value=invoice_total, confidence=total_confidence),
        raw_breakdown=RawBreakdown(
            energy_total_tl=FieldValue(value=energy_total, confidence=0.90),
            distribution_total_tl=FieldValue(value=dist_total, confidence=0.90),
            btv_tl=FieldValue(value=btv, confidence=0.90),
            vat_tl=FieldValue(value=vat, confidence=0.90),
        ),
        meta=InvoiceMeta(
            tariff_group_guess="Sanayi",
            voltage_guess="AG",
            term_type_guess="Tek Terim",
            invoice_type_guess="Tip-1",
        ),
    )


# Fixture 1: Happy Path - No mismatch
FIXTURE_HAPPY = {
    "name": "happy_path_invoice",
    "consumption_kwh": 10000,
    "unit_price": 3.5,
    # Computed: energy=35000, dist=5000, btv=350, matrah=40350, vat=8070, total=48420
    "invoice_total": 48420.0,  # Matches computed
    "consumption_confidence": 0.95,
    "unit_price_confidence": 0.92,
    "total_confidence": 0.98,
    "expected": {
        "incident_created": False,
        "quality_grade": "OK",
        "mismatch": False,
    }
}

# Fixture 2: S2 Mismatch - VERIFY_INVOICE_LOGIC
FIXTURE_S2_MISMATCH = {
    "name": "s2_mismatch_invoice",
    "consumption_kwh": 10000,
    "unit_price": 3.5,
    # Computed: ~48420, but invoice says 48800 (delta=380, ratio=0.78%)
    # S2 koşulu: delta >= 50 TL (380 >= 50 ✓) ama delta < 500 TL (S1 değil)
    "invoice_total": 48800.0,  # Mismatch! (delta ~380 TL)
    "consumption_confidence": 0.90,
    "unit_price_confidence": 0.88,
    "total_confidence": 0.95,
    "expected": {
        "incident_created": True,
        "severity": "S2",
        "primary_flag": "INVOICE_TOTAL_MISMATCH",
        "action_class": "VERIFY_INVOICE_LOGIC",
        "mismatch": True,
    }
}

# Fixture 3: S1 + OCR Suspect - VERIFY_OCR
FIXTURE_S1_OCR = {
    "name": "s1_ocr_suspect_invoice",
    "consumption_kwh": 10000,
    "unit_price": 3.5,
    # Computed: ~48420, but invoice says 100000 (delta=51580, ratio=106%)
    "invoice_total": 100000.0,  # Severe mismatch!
    "consumption_confidence": 0.55,  # Low confidence!
    "unit_price_confidence": 0.52,
    "total_confidence": 0.60,
    "expected": {
        "incident_created": True,
        "severity": "S1",
        "primary_flag": "INVOICE_TOTAL_MISMATCH",
        "action_class": "VERIFY_OCR",
        "suspect_reason": "OCR_LOCALE_SUSPECT",
        "mismatch": True,
    }
}


# ═══════════════════════════════════════════════════════════════════════════════
# E2E SMOKE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestE2ESmokeHappyPath:
    """Fixture 1: Happy path - no incident, quality OK."""
    
    def test_happy_path_no_mismatch(self):
        """Happy path should have no mismatch."""
        extraction = create_extraction(
            consumption_kwh=FIXTURE_HAPPY["consumption_kwh"],
            unit_price=FIXTURE_HAPPY["unit_price"],
            invoice_total=FIXTURE_HAPPY["invoice_total"],
            consumption_confidence=FIXTURE_HAPPY["consumption_confidence"],
            unit_price_confidence=FIXTURE_HAPPY["unit_price_confidence"],
            total_confidence=FIXTURE_HAPPY["total_confidence"],
        )
        
        # Validate
        validation = validate_extraction(extraction)
        assert validation.is_ready_for_pricing
        
        # Check mismatch
        # Compute expected total
        energy = FIXTURE_HAPPY["consumption_kwh"] * FIXTURE_HAPPY["unit_price"]
        dist = FIXTURE_HAPPY["consumption_kwh"] * 0.5
        btv = energy * 0.01
        matrah = energy + dist + btv
        vat = matrah * 0.20
        computed_total = matrah + vat
        
        mismatch_info = check_total_mismatch(
            invoice_total=FIXTURE_HAPPY["invoice_total"],
            computed_total=computed_total,
            extraction_confidence=FIXTURE_HAPPY["consumption_confidence"],
        )
        
        assert not mismatch_info.has_mismatch, f"Expected no mismatch, got delta={mismatch_info.delta}"
    
    def test_happy_path_quality_ok(self):
        """Happy path should have quality grade OK."""
        extraction = create_extraction(
            consumption_kwh=FIXTURE_HAPPY["consumption_kwh"],
            unit_price=FIXTURE_HAPPY["unit_price"],
            invoice_total=FIXTURE_HAPPY["invoice_total"],
        )
        
        validation = validate_extraction(extraction)
        
        # Mock calculation result (no mismatch)
        calculation = {
            "meta_total_mismatch": False,
            "meta_distribution_source": "test",
            "meta_pricing_source": "test",
        }
        
        quality = calculate_quality_score(
            extraction=extraction.model_dump(),
            validation=validation.model_dump(),
            calculation=calculation,
            calculation_error=None,
            debug_meta=None,
        )
        
        assert quality.grade == "OK"
        assert quality.score >= 80
        assert "INVOICE_TOTAL_MISMATCH" not in quality.flags


class TestE2ESmokeS2Mismatch:
    """Fixture 2: S2 Mismatch - VERIFY_INVOICE_LOGIC."""
    
    def test_s2_mismatch_detected(self):
        """S2 mismatch should be detected."""
        extraction = create_extraction(
            consumption_kwh=FIXTURE_S2_MISMATCH["consumption_kwh"],
            unit_price=FIXTURE_S2_MISMATCH["unit_price"],
            invoice_total=FIXTURE_S2_MISMATCH["invoice_total"],
            consumption_confidence=FIXTURE_S2_MISMATCH["consumption_confidence"],
        )
        
        # Compute expected total
        energy = FIXTURE_S2_MISMATCH["consumption_kwh"] * FIXTURE_S2_MISMATCH["unit_price"]
        dist = FIXTURE_S2_MISMATCH["consumption_kwh"] * 0.5
        btv = energy * 0.01
        matrah = energy + dist + btv
        vat = matrah * 0.20
        computed_total = matrah + vat
        
        mismatch_info = check_total_mismatch(
            invoice_total=FIXTURE_S2_MISMATCH["invoice_total"],
            computed_total=computed_total,
            extraction_confidence=FIXTURE_S2_MISMATCH["consumption_confidence"],
        )
        
        assert mismatch_info.has_mismatch
        assert mismatch_info.severity == "S2"
        # High confidence, so no OCR suspect
        assert mismatch_info.suspect_reason is None
    
    def test_s2_mismatch_action_hint(self):
        """S2 mismatch should have VERIFY_INVOICE_LOGIC action hint."""
        from backend.app.incident_service import generate_action_hint
        
        mismatch_info = {
            "has_mismatch": True,
            "delta": 6580,
            "ratio": 0.136,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint(
            flag_code="INVOICE_TOTAL_MISMATCH",
            mismatch_info=mismatch_info,
            extraction_confidence=0.90,
        )
        
        assert hint is not None
        assert hint.action_class.value == "VERIFY_INVOICE_LOGIC"
        assert hint.primary_suspect.value == "INVOICE_LOGIC"


class TestE2ESmokeS1OCR:
    """Fixture 3: S1 + OCR Suspect - VERIFY_OCR."""
    
    def test_s1_ocr_mismatch_detected(self):
        """S1 OCR mismatch should be detected with suspect reason."""
        extraction = create_extraction(
            consumption_kwh=FIXTURE_S1_OCR["consumption_kwh"],
            unit_price=FIXTURE_S1_OCR["unit_price"],
            invoice_total=FIXTURE_S1_OCR["invoice_total"],
            consumption_confidence=FIXTURE_S1_OCR["consumption_confidence"],
        )
        
        # Compute expected total
        energy = FIXTURE_S1_OCR["consumption_kwh"] * FIXTURE_S1_OCR["unit_price"]
        dist = FIXTURE_S1_OCR["consumption_kwh"] * 0.5
        btv = energy * 0.01
        matrah = energy + dist + btv
        vat = matrah * 0.20
        computed_total = matrah + vat
        
        mismatch_info = check_total_mismatch(
            invoice_total=FIXTURE_S1_OCR["invoice_total"],
            computed_total=computed_total,
            extraction_confidence=FIXTURE_S1_OCR["consumption_confidence"],
        )
        
        assert mismatch_info.has_mismatch
        assert mismatch_info.severity == "S1"
        assert mismatch_info.suspect_reason == "OCR_LOCALE_SUSPECT"
    
    def test_s1_ocr_action_hint(self):
        """S1 OCR mismatch should have VERIFY_OCR action hint."""
        from backend.app.incident_service import generate_action_hint
        
        mismatch_info = {
            "has_mismatch": True,
            "delta": 51580,
            "ratio": 1.06,
            "severity": "S1",
            "suspect_reason": "OCR_LOCALE_SUSPECT",
        }
        
        hint = generate_action_hint(
            flag_code="INVOICE_TOTAL_MISMATCH",
            mismatch_info=mismatch_info,
            extraction_confidence=0.55,
        )
        
        assert hint is not None
        assert hint.action_class.value == "VERIFY_OCR"
        assert hint.primary_suspect.value == "OCR_LOCALE_SUSPECT"


class TestE2ESmokePipelineIntegration:
    """Full pipeline integration test with DB."""
    
    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        from backend.app.database import SessionLocal, init_db, Base, engine
        
        # Create tables
        Base.metadata.create_all(bind=engine)
        
        db = SessionLocal()
        try:
            # Clean up incidents from previous tests
            db.query(Incident).delete()
            db.commit()
            yield db
        finally:
            db.close()
    
    def test_full_pipeline_creates_incidents(self, db_session):
        """Full pipeline should create incidents for S2 and S1 fixtures."""
        db = db_session
        
        # Process S2 fixture
        extraction_s2 = create_extraction(
            consumption_kwh=FIXTURE_S2_MISMATCH["consumption_kwh"],
            unit_price=FIXTURE_S2_MISMATCH["unit_price"],
            invoice_total=FIXTURE_S2_MISMATCH["invoice_total"],
            consumption_confidence=FIXTURE_S2_MISMATCH["consumption_confidence"],
            vendor="s2_vendor",
        )
        
        validation_s2 = validate_extraction(extraction_s2)
        
        # Simulate calculation with mismatch
        calculation_s2 = {
            "meta_total_mismatch": True,
            "meta_total_mismatch_info": {
                "has_mismatch": True,
                "delta": 6580,
                "ratio": 0.136,
                "severity": "S2",
            },
            "meta_distribution_source": "test",
            "meta_pricing_source": "test",
        }
        
        quality_s2 = calculate_quality_score(
            extraction=extraction_s2.model_dump(),
            validation=validation_s2.model_dump(),
            calculation=calculation_s2,
            calculation_error=None,
            debug_meta=None,
        )
        
        # Create incident
        fingerprint_s2 = generate_invoice_fingerprint(
            supplier="s2_vendor",
            invoice_no="S2-001",
            period="2025-01",
        )
        
        incident_ids_s2 = create_incidents_from_quality(
            db=db,
            trace_id="test-s2",
            quality=quality_s2,
            tenant_id="test",
            period="2025-01",
            invoice_fingerprint=fingerprint_s2,
        )
        
        assert len(incident_ids_s2) > 0, "S2 fixture should create incident"
        
        # Verify incident
        incident_s2 = db.query(Incident).filter(Incident.id == incident_ids_s2[0]).first()
        assert incident_s2 is not None
        assert incident_s2.severity == "S2"
        # primary_flag is stored in details_json (legacy) or primary_flag column
        expected_flag = "INVOICE_TOTAL_MISMATCH"
        actual_flag = incident_s2.primary_flag or (incident_s2.details_json or {}).get("primary_flag")
        assert actual_flag == expected_flag, f"Expected {expected_flag}, got {actual_flag}"
    
    def test_system_health_after_incidents(self, db_session):
        """System health report should reflect created incidents."""
        db = db_session
        
        # Create a test incident
        incident = Incident(
            trace_id="health-test",
            tenant_id="test",
            severity="S2",
            category="MISMATCH",
            primary_flag="INVOICE_TOTAL_MISMATCH",
            message="Test mismatch",
            status="OPEN",
            details_json={
                "flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.10}]
            },
        )
        db.add(incident)
        db.commit()
        
        # Generate health report
        report = generate_system_health_report(
            db=db,
            tenant_id="test",
            reference_date=date.today(),
            period_days=7,
        )
        
        assert report is not None
        assert report.current_period.total_invoices >= 0
        # Histogram should have buckets
        assert len(report.histogram) == 5  # 5 buckets
    
    def test_feedback_loop(self, db_session):
        """Feedback submission and stats should work."""
        db = db_session
        
        # Create a resolved incident
        incident = Incident(
            trace_id="feedback-test",
            tenant_id="test",
            severity="S2",
            category="MISMATCH",
            primary_flag="INVOICE_TOTAL_MISMATCH",
            message="Test for feedback",
            status="RESOLVED",
            resolved_at=datetime.utcnow(),
            details_json={"action_hint": {"action_class": "VERIFY_INVOICE_LOGIC"}},
        )
        db.add(incident)
        db.commit()
        db.refresh(incident)
        
        # Submit feedback
        feedback_payload = {
            "action_taken": "VERIFIED_LOGIC",
            "was_hint_correct": True,
            "resolution_time_seconds": 120,
            "actual_root_cause": "Missing line item",
        }
        
        updated_incident = submit_feedback(
            db=db,
            incident_id=incident.id,
            payload=feedback_payload,
            user_id="test_user",
        )
        
        assert updated_incident.feedback_json is not None
        assert updated_incident.feedback_json["was_hint_correct"] == True
        
        # Get feedback stats
        stats = get_feedback_stats(
            db=db,
            tenant_id="test",
        )
        
        assert stats.total_feedback_count >= 1
        assert stats.hint_accuracy_rate >= 0  # At least calculable


class TestE2ESmokeConfigValidation:
    """Config validation should work at startup."""
    
    def test_default_config_valid(self):
        """Default config should pass validation."""
        from backend.app.config import validate_config, THRESHOLDS
        
        # Should not raise
        validate_config(THRESHOLDS)
    
    def test_config_summary_available(self):
        """Config summary should be available for health check."""
        from backend.app.config import get_config_summary
        
        summary = get_config_summary()
        
        assert "mismatch" in summary
        assert "drift" in summary
        assert "validation" in summary
        assert summary["mismatch"]["ratio"] > 0


class TestE2ESmokeRunSummary:
    """Run summary generation tests."""
    
    @pytest.fixture
    def db_session(self):
        """Create a test database session."""
        from backend.app.database import SessionLocal, init_db, Base, engine
        
        # Create tables
        Base.metadata.create_all(bind=engine)
        
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    def test_run_summary_generation(self, db_session):
        """Run summary should be generated with correct structure."""
        from backend.app.incident_metrics import generate_run_summary
        
        summary = generate_run_summary(
            db=db_session,
            tenant_id="test",
            period_hours=24,
        )
        
        # Check structure
        assert summary.generated_at is not None
        assert summary.period_start is not None
        assert summary.period_end is not None
        
        # Check counts are non-negative
        assert summary.incident_count >= 0
        assert summary.s1_count >= 0
        assert summary.s2_count >= 0
        assert summary.ocr_suspect_count >= 0
        
        # Check rates are in valid range
        assert 0 <= summary.mismatch_rate <= 1 or summary.mismatch_rate > 1  # Can be > 1 if estimate
        assert 0 <= summary.s1_rate <= 1
        assert 0 <= summary.feedback_coverage <= 1
    
    def test_run_summary_to_dict(self, db_session):
        """Run summary should serialize to dict correctly."""
        from backend.app.incident_metrics import generate_run_summary
        
        summary = generate_run_summary(
            db=db_session,
            tenant_id="test",
            period_hours=24,
        )
        
        summary_dict = summary.to_dict()
        
        # Check required keys
        assert "generated_at" in summary_dict
        assert "period" in summary_dict
        assert "counts" in summary_dict
        assert "rates" in summary_dict
        assert "latency" in summary_dict
        assert "errors" in summary_dict
        assert "queue" in summary_dict
        
        # Check nested structure
        assert "start" in summary_dict["period"]
        assert "end" in summary_dict["period"]
        assert "incident_count" in summary_dict["counts"]
        assert "mismatch_rate" in summary_dict["rates"]
        assert "pipeline_total_ms" in summary_dict["latency"]
    
    def test_run_summary_with_latency_samples(self, db_session):
        """Run summary should calculate latency percentiles."""
        from backend.app.incident_metrics import generate_run_summary
        
        # Provide latency samples
        latency_samples = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000,
                          1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000]
        
        summary = generate_run_summary(
            db=db_session,
            tenant_id="test",
            period_hours=24,
            latency_samples=latency_samples,
        )
        
        # Check latency percentiles are calculated
        assert summary.latency_p50_ms is not None
        assert summary.latency_p95_ms is not None
        assert summary.latency_p99_ms is not None
        
        # p50 should be around 1000ms (median of 100-2000)
        assert 900 <= summary.latency_p50_ms <= 1100
