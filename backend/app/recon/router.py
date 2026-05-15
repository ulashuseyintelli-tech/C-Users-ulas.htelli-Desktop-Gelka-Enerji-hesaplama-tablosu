"""
Invoice Reconciliation Engine — FastAPI Router.

Sorumluluklar (SADECE):
1. VALIDATE: file extension, size (gerçek byte read), request body
2. ORCHESTRATE: pipeline fonksiyonlarını sırayla çağır
3. SERIALIZE: ReconReport JSON döndür

YASAK: domain logic, hesaplama, T1/T2/T3 tanımı, tolerans kararı.

HTTP Status Contract:
- 200: Başarılı (status="ok") veya kısmi (status="partial", quote blocked)
- 400: Invalid Excel (empty_file, unknown_format, file_too_large, invalid_extension)
- 422: Request body validation (FastAPI/Pydantic default)
- 500: Unexpected internal error

Fail-closed davranış:
- PTF/YEKDEM eksik → 200 + status="partial" + quote_blocked=true
- Hiçbir teklif/savings mesajı üretilmez
- Parse + reconciliation sonucu yine döner

request_body optional:
- Verilmezse default ReconRequest kullanılır:
  - invoices: [] (boş — mutabakat yapılmaz)
  - tolerance: pct=1.0%, abs=1.0 kWh
  - comparison: gelka_margin=1.05
"""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from .classifier import classify_period_records
from .comparator import compare_costs
from .cost_engine import calculate_ptf_cost, check_quote_eligibility, get_yekdem_cost
from .parser import (
    EmptyFileError,
    FileTooLargeError,
    ParserError,
    UnknownFormatError,
    parse_excel,
)
from .reconciler import (
    calculate_effective_price,
    get_overall_severity,
    get_overall_status,
    reconcile_consumption,
)
from .report_builder import build_report
from .schemas import (
    ComparisonConfig,
    ErrorResponse,
    InvoiceInput,
    PeriodResult,
    ReconReport,
    ReconRequest,
    ToleranceConfig,
)
from .splitter import split_by_month, validate_period_completeness

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
SLOW_REQUEST_THRESHOLD_S = 30.0

recon_router = APIRouter(prefix="/api/recon", tags=["recon"])


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint
# ═══════════════════════════════════════════════════════════════════════════════


@recon_router.post(
    "/analyze",
    response_model=ReconReport,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid Excel file"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
    summary="Fatura Mutabakat Analizi",
    description=(
        "Saatlik tüketim Excel dosyasını parse eder, T1/T2/T3 hesaplar, "
        "fatura değerleriyle karşılaştırır ve PTF/YEKDEM bazlı maliyet hesaplar. "
        "request_body optional — verilmezse default tolerans ve boş fatura listesi kullanılır."
    ),
)
async def analyze_recon(
    file: UploadFile = File(..., description="Saatlik tüketim Excel dosyası (.xlsx/.xls)"),
    request_body: Optional[str] = Form(
        default=None,
        description=(
            "JSON string — ReconRequest schema. Optional. "
            "Default: invoices=[], tolerance={pct:1.0, abs:1.0}, comparison={margin:1.05}"
        ),
    ),
    db: Session = Depends(get_db),
) -> ReconReport:
    """Fatura mutabakat analizi endpoint'i.

    Pipeline: validate → parse → split → classify → reconcile → cost → compare → report
    """
    start_time = time.time()

    # ── 1. VALIDATE ──────────────────────────────────────────────────────────

    # Extension check
    filename = file.filename or ""
    ext = _get_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        return _error_response(400, "invalid_extension", (
            f"Geçersiz dosya uzantısı: '{ext}'. "
            f"Kabul edilen: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        ))

    # Read file bytes (gerçek byte read — Content-Length'e güvenme)
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        return _error_response(400, "file_too_large", (
            f"Dosya boyutu ({len(file_bytes) / (1024*1024):.1f} MB) "
            f"50 MB limitini aşıyor."
        ))

    # Parse request_body
    recon_request = _parse_request_body(request_body)
    if isinstance(recon_request, JSONResponse):
        return recon_request  # 400 error

    # ── 2. ORCHESTRATE ───────────────────────────────────────────────────────

    try:
        report = _run_pipeline(file_bytes, recon_request, db)
    except EmptyFileError as e:
        return _error_response(400, "empty_file", str(e))
    except UnknownFormatError as e:
        return _error_response(400, "unknown_format", str(e))
    except FileTooLargeError as e:
        return _error_response(400, "file_too_large", str(e))
    except ParserError as e:
        return _error_response(400, "parse_error", str(e))
    except Exception as e:
        logger.exception("Recon pipeline unexpected error")
        return _error_response(500, "internal_error", "Beklenmeyen hata oluştu")

    # ── 3. LOGGING ───────────────────────────────────────────────────────────

    elapsed = time.time() - start_time
    if elapsed > SLOW_REQUEST_THRESHOLD_S:
        logger.warning(
            f"[RECON] Slow request: {elapsed:.1f}s "
            f"(file={filename}, rows={report.parse_stats.get('total_rows', 0)})"
        )
    else:
        logger.info(
            f"[RECON] Completed in {elapsed:.2f}s "
            f"(file={filename}, status={report.status}, "
            f"periods={len(report.periods)})"
        )

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline Orchestration (no domain logic — just wiring)
# ═══════════════════════════════════════════════════════════════════════════════


def _run_pipeline(
    file_bytes: bytes,
    request: ReconRequest,
    db: Session,
) -> ReconReport:
    """Pipeline orchestration — validate → parse → split → classify → reconcile → cost → compare → report."""

    # Parse Excel
    parse_result = parse_excel(file_bytes)

    # Split by month
    period_groups = split_by_month(parse_result.records)

    # Build invoice lookup
    invoice_map: dict[str, InvoiceInput] = {
        inv.period: inv for inv in request.invoices
    }

    # Process each period
    period_results: list[PeriodResult] = []
    all_warnings = list(parse_result.warnings)
    any_quote_blocked = False

    for period, records in period_groups.items():
        # Validate completeness
        stats = validate_period_completeness(period, records)
        if stats.has_gaps:
            all_warnings.append(
                f"Dönem {period}: {len(stats.missing_hours)} eksik saat tespit edildi"
            )
        if stats.duplicate_hours:
            all_warnings.append(
                f"Dönem {period}: {len(stats.duplicate_hours)} duplike saat tespit edildi"
            )

        # Classify T1/T2/T3
        tz_summary = classify_period_records(records)

        # Reconcile (if invoice data provided)
        invoice = invoice_map.get(period)
        recon_items = []
        if invoice:
            recon_items = reconcile_consumption(tz_summary, invoice, request.tolerance)

        overall_status = get_overall_status(recon_items)
        overall_severity = get_overall_severity(recon_items)

        # PTF cost
        ptf_result = calculate_ptf_cost(records, period, db)

        # YEKDEM cost
        yekdem_result = get_yekdem_cost(period, tz_summary.total_kwh, db)

        # Quote eligibility (fail-closed)
        quote_blocked, quote_block_reason = check_quote_eligibility(ptf_result, yekdem_result)

        # Cost comparison (only if NOT blocked and invoice data available)
        cost_comparison = None
        if not quote_blocked and invoice:
            effective_price = None
            if invoice.unit_price_tl_per_kwh is not None:
                effective_price = calculate_effective_price(
                    invoice.unit_price_tl_per_kwh,
                    invoice.discount_pct,
                )
            cost_comparison = compare_costs(
                total_kwh=tz_summary.total_kwh,
                effective_unit_price=effective_price,
                distribution_unit_price=invoice.distribution_unit_price_tl_per_kwh,
                ptf_cost_tl=Decimal(str(ptf_result.total_ptf_cost_tl)),
                yekdem_cost_tl=Decimal(str(yekdem_result.total_yekdem_cost_tl)),
                config=request.comparison,
            )

        if quote_blocked:
            any_quote_blocked = True
            all_warnings.append(
                f"Dönem {period}: Piyasa verisi eksik — teklif üretilemedi ({quote_block_reason})"
            )

        # Period warnings
        period_warnings: list[str] = []
        if ptf_result.warning:
            period_warnings.append(ptf_result.warning)

        period_results.append(PeriodResult(
            period=period,
            total_kwh=float(tz_summary.total_kwh),
            t1_kwh=float(tz_summary.t1_kwh),
            t2_kwh=float(tz_summary.t2_kwh),
            t3_kwh=float(tz_summary.t3_kwh),
            t1_pct=float(tz_summary.t1_pct),
            t2_pct=float(tz_summary.t2_pct),
            t3_pct=float(tz_summary.t3_pct),
            missing_hours=len(stats.missing_hours),
            duplicate_hours=len(stats.duplicate_hours),
            reconciliation=recon_items,
            overall_status=overall_status,
            overall_severity=overall_severity,
            ptf_cost=ptf_result,
            yekdem_cost=yekdem_result,
            cost_comparison=cost_comparison,
            quote_blocked=quote_blocked,
            quote_block_reason=quote_block_reason,
            warnings=period_warnings,
        ))

    # Determine overall status
    # "partial" if any period has quote_blocked=true
    # "ok" if all periods have quotes (or no quote needed)
    report_status = "partial" if any_quote_blocked else "ok"

    report = build_report(
        format_detected=parse_result.format_detected,
        total_rows=parse_result.total_rows,
        successful_rows=parse_result.successful_rows,
        failed_rows=parse_result.failed_rows,
        period_results=period_results,
        warnings=all_warnings,
        multiplier_metadata=parse_result.multiplier_metadata,
    )

    # Override status based on quote blocking
    report.status = report_status

    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers (validation only — no domain logic)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_extension(filename: str) -> str:
    """Extract file extension (lowercase)."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    return ""


def _parse_request_body(raw: Optional[str]) -> ReconRequest | JSONResponse:
    """Parse optional JSON request body.

    Default (when None): ReconRequest with default values:
    - invoices: [] (no reconciliation)
    - tolerance: pct=1.0%, abs=1.0 kWh
    - comparison: gelka_margin=1.05
    """
    if raw is None or raw.strip() == "":
        return ReconRequest()  # Explicit default

    try:
        data = json.loads(raw)
        return ReconRequest(**data)
    except json.JSONDecodeError as e:
        return _error_response(400, "invalid_request_body", (
            f"request_body JSON parse hatası: {str(e)}"
        ))
    except Exception as e:
        return _error_response(400, "invalid_request_body", (
            f"request_body validation hatası: {str(e)}"
        ))


def _error_response(status_code: int, error: str, message: str) -> JSONResponse:
    """Consistent error response."""
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=error, message=message).model_dump(),
    )
