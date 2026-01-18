"""
Expected outputs for integration testing with sample invoices.

These fixtures define the expected extraction results for known invoice samples.
They are used to validate the full extraction → validation → calculation flow.

Requirements: 2.1-2.8
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ExpectedExtraction:
    """Expected extraction result for a sample invoice."""
    vendor: str
    invoice_period: Optional[str]
    consumption_kwh_min: float  # Minimum expected value
    consumption_kwh_max: float  # Maximum expected value (tolerance)
    unit_price_min: float
    unit_price_max: float
    has_demand: bool
    total_with_vat_min: Optional[float]
    total_with_vat_max: Optional[float]


@dataclass
class ExpectedValidation:
    """Expected validation result."""
    is_ready_for_pricing: bool
    expected_missing_fields: list[str]
    max_errors: int
    max_warnings: int


@dataclass
class InvoiceTestFixture:
    """Complete test fixture for an invoice sample."""
    name: str
    file_path: str
    extraction: ExpectedExtraction
    validation: ExpectedValidation
    description: str


# ═══════════════════════════════════════════════════════════════════════════════
# Sample Invoice Fixtures
# These are based on the PDF samples in the workspace root
# ═══════════════════════════════════════════════════════════════════════════════

ENERJISA_FIXTURE = InvoiceTestFixture(
    name="enerjisa_sample",
    file_path="Fatura örnekler/ENERJİSA FT'LARI.pdf",
    extraction=ExpectedExtraction(
        vendor="enerjisa",
        invoice_period=None,  # May vary
        consumption_kwh_min=100,
        consumption_kwh_max=100000,
        unit_price_min=0.5,
        unit_price_max=10.0,
        has_demand=False,
        total_with_vat_min=100,
        total_with_vat_max=500000,
    ),
    validation=ExpectedValidation(
        is_ready_for_pricing=True,
        expected_missing_fields=[],
        max_errors=0,
        max_warnings=3,
    ),
    description="Enerjisa electricity invoice sample"
)

YELDEN_FIXTURE = InvoiceTestFixture(
    name="yelden_sample",
    file_path="Fatura örnekler/e-Fatura YELDEN ELEKTRİK.pdf",
    extraction=ExpectedExtraction(
        vendor="unknown",  # May be detected as unknown
        invoice_period=None,
        consumption_kwh_min=100,
        consumption_kwh_max=100000,
        unit_price_min=0.5,
        unit_price_max=10.0,
        has_demand=False,
        total_with_vat_min=100,
        total_with_vat_max=500000,
    ),
    validation=ExpectedValidation(
        is_ready_for_pricing=True,
        expected_missing_fields=[],
        max_errors=0,
        max_warnings=3,
    ),
    description="Yelden Elektrik e-Fatura sample"
)

GENERIC_FIXTURE = InvoiceTestFixture(
    name="generic_sample",
    file_path="Fatura örnekler/(Fatura)20069_2025-11.pdf",
    extraction=ExpectedExtraction(
        vendor="unknown",
        invoice_period="2025-11",
        consumption_kwh_min=100,
        consumption_kwh_max=100000,
        unit_price_min=0.5,
        unit_price_max=10.0,
        has_demand=False,
        total_with_vat_min=100,
        total_with_vat_max=500000,
    ),
    validation=ExpectedValidation(
        is_ready_for_pricing=True,
        expected_missing_fields=[],
        max_errors=0,
        max_warnings=3,
    ),
    description="Generic invoice sample from November 2025"
)


# All available fixtures
ALL_FIXTURES = [
    ENERJISA_FIXTURE,
    YELDEN_FIXTURE,
    GENERIC_FIXTURE,
]


def get_fixture_by_name(name: str) -> Optional[InvoiceTestFixture]:
    """Get a fixture by its name."""
    for fixture in ALL_FIXTURES:
        if fixture.name == name:
            return fixture
    return None


def get_fixture_by_vendor(vendor: str) -> list[InvoiceTestFixture]:
    """Get all fixtures for a specific vendor."""
    return [f for f in ALL_FIXTURES if f.extraction.vendor == vendor]

