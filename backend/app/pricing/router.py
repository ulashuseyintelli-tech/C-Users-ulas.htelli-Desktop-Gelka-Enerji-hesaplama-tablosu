"""
Pricing Risk Engine — FastAPI Router.

Tüm /api/pricing/* endpoint'leri burada tanımlanır.

Endpoint'ler:
  POST /api/pricing/upload-market-data   — EPİAŞ Excel yükleme
  POST /api/pricing/upload-consumption   — Müşteri tüketim Excel yükleme
  POST /api/pricing/analyze              — Tam fiyatlama analizi
  POST /api/pricing/simulate             — Katsayı simülasyonu
  POST /api/pricing/compare              — Çoklu ay karşılaştırma
  GET  /api/pricing/templates            — Profil şablonları listesi
  GET  /api/pricing/periods              — Yüklü dönemler listesi
  YEKDEM CRUD endpoint'leri

Requirements: 15.1–15.4, 16.1–16.8, 19.1–19.4
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db


# ═══════════════════════════════════════════════════════════════════════════════
# Auth Dependencies — mevcut API key sistemiyle entegre
# ═══════════════════════════════════════════════════════════════════════════════

_API_KEY = os.getenv("API_KEY", "")
_API_KEY_ENABLED = os.getenv("API_KEY_ENABLED", "false").lower() == "true"
_ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
_ADMIN_KEY_ENABLED = os.getenv("ADMIN_API_KEY_ENABLED", "false").lower() == "true"
_ENV = os.getenv("ENV", "development").lower()

# KRİTİK: Production'da dev bypass kesinlikle kapalı
if _ENV == "production":
    if not _API_KEY_ENABLED:
        logging.getLogger(__name__).critical(
            "FATAL: API_KEY_ENABLED=false in production! Pricing endpoints unprotected."
        )
    _API_KEY_ENABLED = True  # Production'da zorla aç
    _ADMIN_KEY_ENABLED = True


def _require_pricing_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """Pricing endpoint'leri için API key kontrolü.
    API_KEY_ENABLED=false ise bypass (dev mode)."""
    if not _API_KEY_ENABLED:
        return x_api_key
    if x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "Geçersiz API anahtarı"})
    return x_api_key


def _require_pricing_admin(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")) -> str:
    """Piyasa verisi yükleme için admin key kontrolü.
    ADMIN_API_KEY_ENABLED=false ise bypass (dev mode)."""
    if not _ADMIN_KEY_ENABLED:
        return "admin-bypass"
    if not _ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail={"error": "admin_not_configured"})
    if x_admin_key != _ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Admin yetkisi gerekli"})
    return x_admin_key
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    CacheInfo,
    SimulateRequest,
    SimulateResponse,
    CompareRequest,
    CompareResponse,
    ImbalanceParams,
    SupplierCostSummary,
    PricingSummary,
    LossMapSummary,
    DataQualityReport,
    PeriodComparison,
    RiskLevel,
    DistributionInfo,
)
from .schemas import (
    HourlyMarketPrice,
    MonthlyYekdemPrice,
    ConsumptionProfile,
    ConsumptionHourlyData,
    ProfileTemplate,
    DataVersion,
)
from .excel_parser import (
    parse_epias_excel,
    parse_consumption_excel,
    ParsedMarketRecord,
    ParsedConsumptionRecord,
)
from .pricing_engine import calculate_weighted_prices, calculate_hourly_costs
from .time_zones import calculate_time_zone_breakdown
from .multiplier_simulator import (
    run_simulation,
    calculate_safe_multiplier,
    PeriodData,
)
from .risk_calculator import (
    calculate_risk_score,
    generate_offer_warning,
    check_risk_safe_multiplier_coherence,
)
from .margin_reality import calculate_margin_reality
from .yekdem_service import create_or_update_yekdem, get_yekdem, list_yekdem
from .consumption_service import save_consumption_profile
from .profile_templates import (
    seed_profile_templates,
    generate_hourly_consumption,
    generate_t1t2t3_consumption,
    BUILTIN_TEMPLATES,
)
from .pricing_cache import (
    CACHE_KEY_VERSION,
    build_cache_key,
    get_cached_result,
    set_cached_result,
    invalidate_cache_for_customer,
    invalidate_cache_for_period,
)
from .version_manager import get_active_version
from .pricing_report import generate_pdf_report, generate_excel_report

logger = logging.getLogger(__name__)

pricing_router = APIRouter(prefix="/api/pricing", tags=["pricing"])


from ..distribution_tariffs import get_distribution_unit_price as _lookup_dist_tariff
from ..distribution_tariffs import get_all_tariffs as _get_all_tariffs


# ═══════════════════════════════════════════════════════════════════════════════
# Yardımcı Fonksiyonlar
# ═══════════════════════════════════════════════════════════════════════════════


def _calculate_distribution_info(
    voltage_level: str,
    total_kwh: float,
    tariff_group: str = "sanayi",
    term_type: str = "çift_terim",
) -> DistributionInfo | None:
    """Dağıtım bedeli hesapla — voltage_level (AG/OG) bazlı.

    Mevcut distribution_tariffs.py modülündeki EPDK tarife tablosunu kullanır.
    Varsayılan: Sanayi, Çift Terim (en yaygın senaryo).
    """
    vl = voltage_level.upper() if voltage_level else "OG"
    if vl not in ("AG", "OG"):
        vl = "OG"

    lookup = _lookup_dist_tariff(tariff_group, vl, term_type)
    if not lookup.success or lookup.unit_price is None:
        return None

    total_tl = round(total_kwh * lookup.unit_price, 2)
    return DistributionInfo(
        voltage_level=vl,
        unit_price_tl_per_kwh=lookup.unit_price,
        total_kwh=round(total_kwh, 2),
        total_tl=total_tl,
        tariff_key=lookup.tariff_key,
    )


def _load_market_records(
    db: Session, period: str,
) -> list[ParsedMarketRecord]:
    """PTF read dispatcher — canonical, dual-read, or legacy rollback.

    Phase 1 T1.4 + Phase 2 T2.1 (ptf-sot-unification): three-way dispatch.

    Precedence (kill switch always wins):
      1. use_legacy_ptf=True               → legacy reader only (Phase 1 rollback)
      2. ptf_drift_log_enabled=True        → dual-read (canonical authoritative,
                                              legacy shadow, drift log best-effort)
      3. otherwise (Phase 1 default)       → canonical reader only

    Silent fallback YASAK: canonical boşsa boş liste döner → caller 404 atar.
    Dual-read modu da bu kontratı korur — legacy varlığı 404'ü engellemez.

    The dual-read path NEVER changes the response (canonical authoritative);
    legacy reads are observe-only and any failure is swallowed.
    """
    from ..guard_config import get_guard_config

    config = get_guard_config()

    # 1. Kill switch — emergency rollback. Always wins. Drift log not consulted.
    if config.use_legacy_ptf:
        return _load_market_records_legacy(db, period)

    # 2. Dual-read observe — Phase 2 default once T2.4 flips the flag.
    if config.ptf_drift_log_enabled:
        return _load_market_records_dual(db, period)

    # 3. Canonical-only — Phase 1 frozen behavior, also Phase 2 default until T2.4.
    return _load_market_records_canonical(db, period)


def _load_market_records_dual(
    db: Session, period: str,
) -> list[ParsedMarketRecord]:
    """Dual-read scaffold (T2.1) — canonical authoritative, legacy shadow.

    Contract:
      - Canonical reader is the SOURCE OF TRUTH for the response. Its result
        is returned unchanged. If canonical is empty → empty list returned →
        caller raises 404 (Hybrid-C). Legacy presence MUST NOT mask this.
      - Legacy reader is called purely for observability. Any failure
        (exception, empty result, type error) is swallowed — pricing is not
        impacted.
      - Drift recording is best-effort. The actual compute_drift + record_drift
        wiring lands in T2.2; T2.1 only emits a debug log line so we can
        confirm the dual path executed in real traffic.

    The function NEVER raises out of the legacy/drift side. The canonical
    reader is allowed to raise (DB connection issues, etc.) — those are real
    pricing failures and should propagate as before.
    """
    canonical_records = _load_market_records_canonical(db, period)

    legacy_records: list[ParsedMarketRecord] | None = None
    try:
        legacy_records = _load_market_records_legacy(db, period)
    except Exception as exc:  # noqa: BLE001 — observe-only must not fail request
        logger.warning(
            "[PTF-DUAL] legacy shadow read failed (suppressed) period=%s err=%s",
            period, exc,
        )
        legacy_records = None

    # T2.1: drift compute is a no-op stub. Real wiring comes in T2.2.
    # Defense in depth: even though _maybe_record_drift has its own try/except,
    # we wrap the call here so that if T2.2 (or any future patch) ever removes
    # the inner guard, the dispatcher still cannot leak telemetry exceptions
    # into the pricing response. observe-only is a hard contract.
    try:
        _maybe_record_drift(db, period, canonical_records, legacy_records)
    except Exception as exc:  # noqa: BLE001 — telemetry must not fail request
        logger.warning(
            "[PTF-DUAL] drift recorder raised through inner guard (suppressed) "
            "period=%s err=%s",
            period, exc,
        )

    # Authoritative return — canonical only. Legacy is never seen by caller.
    return canonical_records


def _maybe_record_drift(
    db: Session,
    period: str,
    canonical_records: list[ParsedMarketRecord],
    legacy_records: list[ParsedMarketRecord] | None,
) -> None:
    """T2.1 stub — debug telemetry only, no compute, no DB write.

    Records that the dual path actually executed and reports the canonical /
    legacy row counts so we can answer two operational questions in real
    traffic before T2.2 lands:
      1. Did dual_read run at all? (singleton cache may have returned a stale
         GuardConfig that masked the toggle.)
      2. Did legacy shadow read return anything? (legacy table may be empty
         for the period — that's fine, just want to know.)

    No drift math, no severity classification, no DB write here. T2.2 will
    replace this body with compute_drift + write_drift_record. The signature
    is stable so T2.2 is a body-swap, not a wiring change.

    NEVER raises. Any unexpected error is logged and swallowed; the pricing
    response is unaffected.
    """
    try:
        canonical_count = len(canonical_records)
        legacy_count = len(legacy_records) if legacy_records is not None else None
        logger.debug(
            "[PTF-DUAL] dual_read active period=%s canonical_count=%d legacy_count=%s",
            period,
            canonical_count,
            legacy_count if legacy_count is not None else "shadow_failed",
        )
    except Exception as exc:  # noqa: BLE001 — telemetry must not fail request
        logger.warning(
            "[PTF-DUAL] _maybe_record_drift unexpected error (suppressed) "
            "period=%s err=%s",
            period, exc,
        )


def _load_market_records_canonical(
    db: Session, period: str,
) -> list[ParsedMarketRecord]:
    """Canonical reader: hourly_market_prices (SoT).

    Returns empty list if no active records exist for the period.
    Caller is responsible for raising 409 on empty result (Hybrid-C contract).
    """
    rows = (
        db.query(HourlyMarketPrice)
        .filter(
            HourlyMarketPrice.period == period,
            HourlyMarketPrice.is_active == 1,
        )
        .order_by(HourlyMarketPrice.date, HourlyMarketPrice.hour)
        .all()
    )
    return [
        ParsedMarketRecord(
            period=r.period, date=r.date, hour=r.hour,
            ptf_tl_per_mwh=r.ptf_tl_per_mwh,
            smf_tl_per_mwh=r.smf_tl_per_mwh,
        )
        for r in rows
    ]


def _load_market_records_legacy(
    db: Session, period: str,
) -> list[ParsedMarketRecord]:
    """Legacy fallback reader: market_reference_prices (aylık ortalama PTF).

    ⚠️ WARNING: This path is NOT financially equivalent to canonical hourly PTF.
    Rollback only. The monthly average is spread uniformly across all hours of
    the month (typically 744 for 31-day months). This produces a flat profile
    that does NOT reflect real intra-day price variation.

    Use case: emergency rollback when canonical data is suspected corrupt.
    NOT a normal operating mode. Phase 4 deletes this function entirely.

    Returns empty list if no PTF record exists for the period.
    """
    from ..database import MarketReferencePrice
    import calendar
    from datetime import date as date_type

    row = (
        db.query(MarketReferencePrice)
        .filter(
            MarketReferencePrice.period == period,
            MarketReferencePrice.price_type == "PTF",
        )
        .first()
    )
    if row is None:
        return []

    # Spread monthly average across all hours of the month
    year, month = int(period[:4]), int(period[5:7])
    days_in_month = calendar.monthrange(year, month)[1]
    ptf = row.ptf_tl_per_mwh

    records: list[ParsedMarketRecord] = []
    for day in range(1, days_in_month + 1):
        d = date_type(year, month, day)
        date_str = d.isoformat()
        for hour in range(24):
            records.append(ParsedMarketRecord(
                period=period,
                date=date_str,
                hour=hour,
                ptf_tl_per_mwh=ptf,
                smf_tl_per_mwh=ptf,  # Legacy has no SMF; use PTF as proxy
            ))
    return records


def _load_consumption_records(
    db: Session, customer_id: str, period: str,
) -> list[ParsedConsumptionRecord]:
    """DB'den aktif tüketim profilini yükle ve ParsedConsumptionRecord'a dönüştür."""
    profile = (
        db.query(ConsumptionProfile)
        .filter(
            ConsumptionProfile.customer_id == customer_id,
            ConsumptionProfile.period == period,
            ConsumptionProfile.is_active == 1,
        )
        .first()
    )
    if not profile:
        return []

    hourly = (
        db.query(ConsumptionHourlyData)
        .filter(ConsumptionHourlyData.profile_id == profile.id)
        .order_by(ConsumptionHourlyData.date, ConsumptionHourlyData.hour)
        .all()
    )
    return [
        ParsedConsumptionRecord(
            date=h.date, hour=h.hour, consumption_kwh=h.consumption_kwh,
        )
        for h in hourly
    ]


def _get_or_generate_consumption(
    db: Session,
    period: str,
    customer_id: Optional[str],
    use_template: Optional[bool],
    template_name: Optional[str],
    template_monthly_kwh: Optional[float],
    t1_kwh: Optional[float] = None,
    t2_kwh: Optional[float] = None,
    t3_kwh: Optional[float] = None,
) -> list[ParsedConsumptionRecord]:
    """Tüketim verisi al: T1/T2/T3'den, şablondan veya DB'den.

    ⚠️ KRİTİK: Öncelik sırası kesin ve değiştirilemez
    Priority 1: T1/T2/T3 (override — fatura verisi varsa esas alınır)
    Priority 2: Template (şablon profili)
    Priority 3: DB historical (müşteri geçmiş profili)
    """
    # Priority 1: T1/T2/T3 (override — fatura verisi varsa esas alınır)
    t1 = t1_kwh or 0
    t2 = t2_kwh or 0
    t3 = t3_kwh or 0
    if (t1_kwh is not None or t2_kwh is not None or t3_kwh is not None) and (t1 + t2 + t3) > 0:
        return generate_t1t2t3_consumption(t1, t2, t3, period)

    # Priority 2: Template
    if use_template and template_name and template_monthly_kwh:
        return generate_hourly_consumption(
            template_name, template_monthly_kwh, period, db,
        )

    # Priority 3: DB historical
    if customer_id:
        records = _load_consumption_records(db, customer_id, period)
        if records:
            return records

    raise HTTPException(
        status_code=422,
        detail={
            "error": "missing_consumption_data",
            "message": (
                "Tüketim verisi bulunamadı. "
                "customer_id ile gerçek profil veya "
                "use_template + template_name + template_monthly_kwh ile şablon kullanın."
            ),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Excel Yükleme Endpoint'leri
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.post("/upload-market-data")
async def upload_market_data(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: str = Depends(_require_pricing_admin),
):
    """EPİAŞ uzlaştırma Excel dosyasını yükle ve DB'ye kaydet.

    Returns:
        Yükleme sonucu: dönem, satır sayısı, kalite skoru, versiyon.
    """
    content = await file.read()
    filename = file.filename or "unknown.xlsx"

    # Parse
    parse_output = parse_epias_excel(content, filename)
    result = parse_output.result

    if not result.success:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_excel_format",
                "message": "EPİAŞ Excel formatı ayrıştırılamadı.",
                "warnings": result.warnings,
            },
        )

    period = result.period
    records = parse_output.records

    # Mevcut aktif verileri arşivle
    existing = (
        db.query(HourlyMarketPrice)
        .filter(
            HourlyMarketPrice.period == period,
            HourlyMarketPrice.is_active == 1,
        )
        .all()
    )
    previous_archived = len(existing) > 0
    current_max_version = 0
    for row in existing:
        row.is_active = 0
        if row.version > current_max_version:
            current_max_version = row.version

    new_version = current_max_version + 1

    # Yeni verileri kaydet
    for rec in records:
        db.add(HourlyMarketPrice(
            period=rec.period,
            date=rec.date,
            hour=rec.hour,
            ptf_tl_per_mwh=rec.ptf_tl_per_mwh,
            smf_tl_per_mwh=rec.smf_tl_per_mwh,
            source="epias_excel",
            version=new_version,
            is_active=1,
        ))

    # data_versions kaydı
    existing_dv = (
        db.query(DataVersion)
        .filter(
            DataVersion.data_type == "market_data",
            DataVersion.period == period,
            DataVersion.is_active == 1,
        )
        .all()
    )
    for dv in existing_dv:
        dv.is_active = 0

    db.add(DataVersion(
        data_type="market_data",
        period=period,
        version=new_version,
        row_count=result.total_rows,
        quality_score=result.quality_score,
        upload_filename=filename,
        is_active=1,
    ))

    db.commit()

    # Cache invalidation: piyasa verisi güncellendi → dönem cache sil
    invalidate_cache_for_period(db, period)

    return {
        "status": "ok",
        "period": period,
        "total_rows": result.total_rows,
        "expected_hours": result.expected_hours,
        "missing_hours": result.missing_hours,
        "rejected_rows": result.rejected_rows,
        "warnings": result.warnings,
        "quality_score": result.quality_score,
        "version": new_version,
        "previous_version_archived": previous_archived,
    }


@pricing_router.post("/upload-consumption")
async def upload_consumption(
    file: UploadFile = File(...),
    customer_id: str = Form(...),
    customer_name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """Müşteri tüketim Excel dosyasını yükle ve DB'ye kaydet."""
    content = await file.read()
    filename = file.filename or "unknown.xlsx"

    parse_output = parse_consumption_excel(content, filename)
    result = parse_output.result

    if not result.success:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_consumption_format",
                "message": "Tüketim Excel formatı ayrıştırılamadı.",
                "warnings": result.warnings,
            },
        )

    # DB'ye kaydet
    profile = save_consumption_profile(
        db=db,
        customer_id=customer_id,
        customer_name=customer_name,
        period=result.period,
        records=parse_output.records,
        source="excel",
    )

    # Cache invalidation: tüketim verisi güncellendi → müşteri cache sil
    invalidate_cache_for_customer(db, customer_id)

    return {
        "status": "ok",
        "customer_id": customer_id,
        "period": result.period,
        "total_rows": result.total_rows,
        "total_kwh": result.total_kwh,
        "negative_hours": result.negative_hours,
        "quality_score": result.quality_score,
        "profile_id": profile.id,
        "version": profile.version,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Analiz Endpoint'leri
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    req: AnalyzeRequest,
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """Tam fiyatlama analizi — ana hesaplama endpoint'i.

    Cache katmanı: Aynı parametrelerle tekrar istek → cache'den döner.
    """
    period = req.period

    # ── Cache check ────────────────────────────────────────────────────
    # T7 / Decision 9: build_cache_key çağrısına 5 yeni alan geçirilir
    # (t1_kwh, t2_kwh, t3_kwh, use_template, voltage_level). Bu alanlar
    # response'u etkilediği için key'de de yer almalı — aksi halde LOW/HIGH
    # profilleri aynı cache kaydına collide eder (pricing-cache-key-completeness).
    imbalance_dict = req.imbalance_params.model_dump()
    # T1.4: ptf_source cache key'e girmeli — switch toggle sonrası stale cache
    # hit'i önler. Canonical ve legacy farklı sonuç üretir; aynı key = bug.
    from ..guard_config import get_guard_config
    _ptf_source = "legacy" if get_guard_config().use_legacy_ptf else "canonical"
    cache_key = build_cache_key(
        customer_id=req.customer_id,
        period=period,
        multiplier=req.multiplier,
        dealer_commission_pct=req.dealer_commission_pct,
        imbalance_params=imbalance_dict,
        template_name=req.template_name,
        template_monthly_kwh=req.template_monthly_kwh,
        t1_kwh=req.t1_kwh,
        t2_kwh=req.t2_kwh,
        t3_kwh=req.t3_kwh,
        use_template=req.use_template,
        voltage_level=req.voltage_level,
        ptf_source=_ptf_source,
    )

    cached = get_cached_result(db, cache_key)
    if cached:
        cached["cache_hit"] = True
        # Decision 9: yapılandırılmış cache observability.
        # v2 key çağrısı v1 kayıtlarına match olamaz (izolasyon), o yüzden
        # cached_key_version her zaman CACHE_KEY_VERSION ile eşit.
        cached["cache"] = {
            "hit": True,
            "key_version": CACHE_KEY_VERSION,
            "cached_key_version": CACHE_KEY_VERSION,
        }
        return cached

    # 1. Piyasa verisi yükle
    market_records = _load_market_records(db, period)
    if not market_records:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "market_data_not_found",
                "message": f"{period} dönemi için piyasa verisi bulunamadı.",
            },
        )

    # 2. Tüketim verisi al
    consumption_records = _get_or_generate_consumption(
        db, period, req.customer_id,
        req.use_template, req.template_name, req.template_monthly_kwh,
        t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh,
    )

    # 3. YEKDEM — hard-block when missing (P0 financial safety)
    # Incomplete market data = hard failure. A financial system does not
    # produce "approximate" results with missing cost components.
    # No fallback, no approximation, no previous-month reuse.
    warnings = []
    yekdem_record = get_yekdem(db, period)
    if not yekdem_record:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "yekdem_data_not_found",
                "message": (
                    f"{period} dönemi için YEKDEM verisi bulunamadı. "
                    f"Eksik YEKDEM ile analiz yapılamaz — fiyatlama doğruluğu garanti edilemez. "
                    f"Lütfen admin panelinden YEKDEM verisini yükleyin."
                ),
                "period": period,
                "resolution": "POST /api/pricing/yekdem ile dönem YEKDEM değerini ekleyin.",
            },
        )
    yekdem = yekdem_record.yekdem_tl_per_mwh

    # 4. Ağırlıklı fiyat hesapla
    weighted = calculate_weighted_prices(market_records, consumption_records)

    # 5. Saatlik maliyet hesapla — distribution entegrasyonu
    dist_info = _calculate_distribution_info(
        voltage_level=req.voltage_level or "og",
        total_kwh=weighted.total_consumption_kwh,
    )
    dist_unit_price = dist_info.unit_price_tl_per_kwh if dist_info else 0.0

    hourly_result = calculate_hourly_costs(
        market_records, consumption_records,
        yekdem_tl_per_mwh=yekdem,
        multiplier=req.multiplier,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
        distribution_unit_price_tl_per_kwh=dist_unit_price,
    )

    # 6. Zaman dilimi dağılımı
    tz_breakdown = calculate_time_zone_breakdown(
        market_records, consumption_records, yekdem,
    )

    # 7. Dengesizlik maliyeti (TL/MWh)
    from .imbalance import calculate_imbalance_cost
    imbalance_cost = calculate_imbalance_cost(
        weighted.weighted_ptf_tl_per_mwh,
        weighted.weighted_smf_tl_per_mwh,
        req.imbalance_params,
    )

    # 8. Güvenli katsayı
    pd = PeriodData(
        period=period,
        market_records=market_records,
        consumption_records=consumption_records,
    )
    safe_result = calculate_safe_multiplier(
        [pd],
        yekdem_tl_per_mwh=yekdem,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )

    # 9. Risk skoru
    risk = calculate_risk_score(weighted, tz_breakdown)

    # 10. Zarar haritası
    loss_hours_list = [e for e in hourly_result.hour_costs if e.is_loss_hour]
    loss_by_tz: dict[str, int] = {"T1": 0, "T2": 0, "T3": 0}
    for e in loss_hours_list:
        loss_by_tz[e.time_zone.value] = loss_by_tz.get(e.time_zone.value, 0) + 1

    worst_hours = sorted(loss_hours_list, key=lambda e: e.margin_tl)[:10]
    loss_map = LossMapSummary(
        total_loss_hours=len(loss_hours_list),
        total_loss_tl=round(sum(e.margin_tl for e in loss_hours_list), 2),
        by_time_zone=loss_by_tz,
        worst_hours=[
            {
                "date": e.date, "hour": e.hour,
                "ptf": e.ptf_tl_per_mwh,
                "sales_price": e.sales_price_tl,
                "loss_tl": e.margin_tl,
            }
            for e in worst_hours
        ],
    )

    # 11. Uyarılar (warnings list initialized before YEKDEM check)
    offer_warning = generate_offer_warning(
        req.multiplier, safe_result.safe_multiplier,
        safe_result.recommended_multiplier, risk.score,
    )
    if offer_warning:
        warnings.append({"type": "safe_multiplier_warning", "message": offer_warning})

    coherence = check_risk_safe_multiplier_coherence(
        risk.score, safe_result.safe_multiplier,
    )
    if coherence:
        warnings.append({"type": "coherence_warning", "message": coherence})

    # 12. Tedarikçi maliyet özeti
    energy_cost = weighted.weighted_ptf_tl_per_mwh + yekdem
    supplier_cost = SupplierCostSummary(
        weighted_ptf_tl_per_mwh=weighted.weighted_ptf_tl_per_mwh,
        yekdem_tl_per_mwh=yekdem,
        imbalance_tl_per_mwh=round(imbalance_cost, 2),
        total_cost_tl_per_mwh=round(
            weighted.weighted_ptf_tl_per_mwh + yekdem + imbalance_cost, 2
        ),
    )

    # 13. Fiyatlama özeti — dual price, dual margin, risk flags
    total_consumption = weighted.total_consumption_kwh
    dist_per_mwh = dist_unit_price * 1000  # TL/kWh → TL/MWh

    sales_energy_price_per_mwh = round(energy_cost * req.multiplier, 2)
    sales_effective_price_per_mwh = round(sales_energy_price_per_mwh + dist_per_mwh, 2)

    gross_margin_energy_per_mwh = round(sales_energy_price_per_mwh - energy_cost, 2)
    gross_margin_total_per_mwh = round(sales_energy_price_per_mwh - energy_cost - dist_per_mwh, 2)

    dealer_per_mwh = round(
        hourly_result.dealer_commission_total_tl / (total_consumption / 1000.0), 2
    ) if total_consumption > 0 else 0.0
    imbalance_per_mwh = round(
        hourly_result.imbalance_cost_total_tl / (total_consumption / 1000.0), 2
    ) if total_consumption > 0 else 0.0

    net_margin_per_mwh = round(
        gross_margin_total_per_mwh - dealer_per_mwh - imbalance_per_mwh, 2
    )

    # Risk flags (priority ordered: P1 > P2, both can coexist)
    risk_flags: list[dict] = []
    if hourly_result.net_margin_total_tl < 0:
        risk_flags.append({
            "type": "LOSS_RISK",
            "priority": 1,
            "message": "Net marj negatif — teklif zarar üretir",
        })
    if gross_margin_total_per_mwh < 0:
        risk_flags.append({
            "type": "UNPROFITABLE_OFFER",
            "priority": 2,
            "message": "Toplam brüt marj negatif — dağıtım dahil maliyet satışı aşıyor",
        })

    pricing = PricingSummary(
        multiplier=req.multiplier,
        # Dual sales price
        sales_energy_price_per_mwh=sales_energy_price_per_mwh,
        sales_effective_price_per_mwh=sales_effective_price_per_mwh,
        # Dual margin (per MWh)
        gross_margin_energy_per_mwh=gross_margin_energy_per_mwh,
        gross_margin_total_per_mwh=gross_margin_total_per_mwh,
        net_margin_per_mwh=net_margin_per_mwh,
        # Cost breakdown (per MWh)
        distribution_cost_per_mwh=round(dist_per_mwh, 2),
        imbalance_cost_per_mwh=imbalance_per_mwh,
        dealer_commission_per_mwh=dealer_per_mwh,
        # Risk flags
        risk_flags=risk_flags,
        # Totals (TL)
        total_sales_tl=hourly_result.total_sales_revenue_tl,
        total_cost_tl=hourly_result.total_base_cost_tl,
        total_gross_margin_tl=hourly_result.total_gross_margin_tl,
        total_dealer_commission_tl=hourly_result.dealer_commission_total_tl,
        total_net_margin_tl=hourly_result.total_net_margin_tl,
        # Backward compat aliases
        sales_price_tl_per_mwh=sales_energy_price_per_mwh,
        gross_margin_tl_per_mwh=gross_margin_energy_per_mwh,
        dealer_commission_tl_per_mwh=dealer_per_mwh,
        net_margin_tl_per_mwh=net_margin_per_mwh,
    )

    # ── 14. Nominal vs Gerçek Marj Analizi ─────────────────────────────
    try:
        hourly_ptf_list = [e.ptf_tl_per_mwh for e in hourly_result.hour_costs]
        hourly_kwh_list = [e.consumption_kwh for e in hourly_result.hour_costs]
        hourly_ts_list = [f"{e.date} {e.hour:02d}:00" for e in hourly_result.hour_costs]
        hourly_tz_list = [e.time_zone.value for e in hourly_result.hour_costs]

        margin_reality_result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=weighted.weighted_ptf_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem,
            multiplier=req.multiplier,
            hourly_ptf_prices=hourly_ptf_list,
            hourly_consumption_kwh=hourly_kwh_list,
            hourly_timestamps=hourly_ts_list,
            hourly_time_zones=hourly_tz_list,
            include_yekdem=True,
        )
        margin_reality_dict = margin_reality_result.model_dump()
    except Exception as e:
        logger.warning("margin_reality calculation failed (non-critical): %s", e)
        margin_reality_dict = None

    response = AnalyzeResponse(
        period=period,
        customer_id=req.customer_id,
        weighted_prices=weighted,
        supplier_cost=supplier_cost,
        pricing=pricing,
        time_zone_breakdown=tz_breakdown,
        loss_map=loss_map,
        risk_score=risk,
        safe_multiplier=safe_result,
        distribution=dist_info,
        margin_reality=margin_reality_dict,
        warnings=warnings,
        data_quality=DataQualityReport(),
        cache_hit=False,
        cache=CacheInfo(
            hit=False,
            key_version=CACHE_KEY_VERSION,
            cached_key_version=None,
        ),
    )

    # ── Cache write ────────────────────────────────────────────────────
    try:
        set_cached_result(
            db, cache_key,
            customer_id=req.customer_id,
            period=period,
            params_hash=cache_key,
            result=response.model_dump(),
        )
    except Exception as e:
        logger.warning("Cache write failed (non-critical): %s", e)

    # ── Analyze logging ────────────────────────────────────────────────
    logger.info(
        "pricing_analyze: customer=%s period=%s multiplier=%.2f "
        "safe=%.3f risk=%s net_margin=%.2f",
        req.customer_id or "template", period, req.multiplier,
        safe_result.safe_multiplier, risk.score.value,
        hourly_result.total_net_margin_tl,
    )

    return response


@pricing_router.post("/simulate", response_model=SimulateResponse)
def simulate(
    req: SimulateRequest,
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """Katsayı simülasyonu — belirtilen aralıkta her katsayı için hesaplama."""
    period = req.period

    market_records = _load_market_records(db, period)
    if not market_records:
        raise HTTPException(
            status_code=404,
            detail={"error": "market_data_not_found",
                    "message": f"{period} dönemi için piyasa verisi bulunamadı."},
        )

    consumption_records = _get_or_generate_consumption(
        db, period, req.customer_id,
        req.use_template, req.template_name, req.template_monthly_kwh,
    )

    yekdem_record = get_yekdem(db, period)
    if not yekdem_record:
        yekdem_value = 0.0
    else:
        yekdem_value = yekdem_record.yekdem_tl_per_mwh

    rows = run_simulation(
        market_records, consumption_records,
        yekdem_tl_per_mwh=yekdem_value,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
        multiplier_start=req.multiplier_start,
        multiplier_end=req.multiplier_end,
        multiplier_step=req.multiplier_step,
    )

    pd = PeriodData(
        period=period,
        market_records=market_records,
        consumption_records=consumption_records,
    )
    safe_result = calculate_safe_multiplier(
        [pd],
        yekdem_tl_per_mwh=yekdem_value,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )

    return SimulateResponse(
        period=period,
        simulation=rows,
        safe_multiplier=safe_result,
    )


@pricing_router.post("/compare", response_model=CompareResponse)
def compare(
    req: CompareRequest,
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """Çoklu ay karşılaştırma — her dönem için analiz + dönemler arası değişim."""
    comparisons: list[PeriodComparison] = []
    missing_periods: list[str] = []
    periods_data: list[PeriodData] = []
    prev_weighted_ptf: float | None = None

    for period in req.periods:
        market_records = _load_market_records(db, period)
        if not market_records:
            missing_periods.append(period)
            continue

        try:
            consumption_records = _get_or_generate_consumption(
                db, period, req.customer_id,
                req.use_template, req.template_name, req.template_monthly_kwh,
            )
        except HTTPException:
            missing_periods.append(period)
            continue

        yekdem_record = get_yekdem(db, period)
        if not yekdem_record:
            yekdem = 0.0  # Graceful fallback — include period with yekdem=0
        else:
            yekdem = yekdem_record.yekdem_tl_per_mwh

        # Hesapla
        weighted = calculate_weighted_prices(market_records, consumption_records)
        hourly_result = calculate_hourly_costs(
            market_records, consumption_records,
            yekdem_tl_per_mwh=yekdem,
            multiplier=req.multiplier,
            imbalance_params=req.imbalance_params,
            dealer_commission_pct=req.dealer_commission_pct,
        )
        tz_breakdown = calculate_time_zone_breakdown(
            market_records, consumption_records, yekdem,
        )
        risk = calculate_risk_score(weighted, tz_breakdown)

        # Değişim yüzdesi
        change_pct = None
        if prev_weighted_ptf is not None and prev_weighted_ptf != 0:
            ptf_change = (
                (weighted.weighted_ptf_tl_per_mwh - prev_weighted_ptf)
                / prev_weighted_ptf * 100
            )
            change_pct = {"weighted_ptf_change_pct": round(ptf_change, 2)}

        prev_weighted_ptf = weighted.weighted_ptf_tl_per_mwh

        comparisons.append(PeriodComparison(
            period=period,
            weighted_ptf_tl_per_mwh=weighted.weighted_ptf_tl_per_mwh,
            weighted_smf_tl_per_mwh=weighted.weighted_smf_tl_per_mwh,
            total_cost_tl=weighted.total_cost_tl,
            net_margin_tl=hourly_result.total_net_margin_tl,
            risk_score=risk.score,
            change_pct=change_pct,
        ))

        periods_data.append(PeriodData(
            period=period,
            market_records=market_records,
            consumption_records=consumption_records,
        ))

    # Güvenli katsayı (tüm dönemler üzerinden)
    if periods_data:
        yekdem_for_safe = get_yekdem(db, periods_data[0].period)
        yekdem_val = yekdem_for_safe.yekdem_tl_per_mwh if yekdem_for_safe else 0.0
        safe_result = calculate_safe_multiplier(
            periods_data,
            yekdem_tl_per_mwh=yekdem_val,
            imbalance_params=req.imbalance_params,
            dealer_commission_pct=req.dealer_commission_pct,
        )
    else:
        from .models import SafeMultiplierResult
        safe_result = SafeMultiplierResult(
            safe_multiplier=1.100,
            recommended_multiplier=1.10,
            periods_analyzed=0,
            warning="Karşılaştırma için yeterli veri bulunamadı.",
        )

    return CompareResponse(
        periods_analyzed=len(comparisons),
        missing_periods=missing_periods,
        comparison=comparisons,
        safe_multiplier=safe_result,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# YEKDEM CRUD Endpoint'leri
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.post("/yekdem")
def upsert_yekdem(
    period: str,
    yekdem_tl_per_mwh: float,
    source: str = "manual",
    db: Session = Depends(get_db),
    _admin: str = Depends(_require_pricing_admin),
):
    """YEKDEM kaydı oluştur veya güncelle."""
    try:
        record = create_or_update_yekdem(db, period, yekdem_tl_per_mwh, source)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"error": "validation_error", "message": str(e)})

    # Cache invalidation: YEKDEM güncellendi → dönem cache sil
    invalidate_cache_for_period(db, period)

    return {
        "status": "ok",
        "period": record.period,
        "yekdem_tl_per_mwh": record.yekdem_tl_per_mwh,
        "source": record.source,
    }


@pricing_router.get("/yekdem/{period}")
def get_yekdem_endpoint(
    period: str,
    db: Session = Depends(get_db),
):
    """Dönem bazlı YEKDEM kaydı sorgula."""
    record = get_yekdem(db, period)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error": "yekdem_not_found", "message": f"{period} dönemi için YEKDEM bulunamadı."},
        )
    return {
        "period": record.period,
        "yekdem_tl_per_mwh": record.yekdem_tl_per_mwh,
        "source": record.source,
    }


@pricing_router.get("/yekdem")
def list_yekdem_endpoint(
    db: Session = Depends(get_db),
):
    """Tüm YEKDEM kayıtlarını listele."""
    records = list_yekdem(db)
    return {
        "status": "ok",
        "count": len(records),
        "items": [
            {
                "period": r.period,
                "yekdem_tl_per_mwh": r.yekdem_tl_per_mwh,
                "source": r.source,
            }
            for r in records
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Şablon ve Dönem Endpoint'leri
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.get("/templates")
def list_templates(db: Session = Depends(get_db)):
    """Profil şablonları listesi — T1/T2/T3 oranları ve risk metadata dahil."""
    templates = db.query(ProfileTemplate).all()

    # BUILTIN_TEMPLATES'ten metadata lookup
    builtin_map = {t.name: t for t in BUILTIN_TEMPLATES}

    def _template_item(name, display_name, description):
        bt = builtin_map.get(name)
        return {
            "name": name,
            "display_name": display_name,
            "description": description or "",
            "t1_pct": bt.t1_pct if bt else 40,
            "t2_pct": bt.t2_pct if bt else 25,
            "t3_pct": bt.t3_pct if bt else 35,
            "risk_level": bt.risk_level if bt else "medium",
            "risk_buffer_pct": bt.risk_buffer_pct if bt else 2,
        }

    # DB'de yoksa in-memory listeden döndür
    if not templates:
        return {
            "status": "ok",
            "count": len(BUILTIN_TEMPLATES),
            "items": [
                _template_item(t.name, t.display_name, t.description)
                for t in BUILTIN_TEMPLATES
            ],
        }

    return {
        "status": "ok",
        "count": len(templates),
        "items": [
            _template_item(t.name, t.display_name, t.description)
            for t in templates
        ],
    }


@pricing_router.get("/periods")
def list_periods(db: Session = Depends(get_db)):
    """Yüklü dönemler listesi — piyasa verisi, YEKDEM, tüketim profilleri."""
    # Piyasa verisi dönemleri
    market_periods = (
        db.query(HourlyMarketPrice.period)
        .filter(HourlyMarketPrice.is_active == 1)
        .distinct()
        .all()
    )
    market_periods = sorted([r[0] for r in market_periods], reverse=True)

    # YEKDEM dönemleri
    yekdem_periods = (
        db.query(MonthlyYekdemPrice.period)
        .distinct()
        .all()
    )
    yekdem_periods = sorted([r[0] for r in yekdem_periods], reverse=True)

    # Tüketim profili dönemleri
    consumption_periods = (
        db.query(
            ConsumptionProfile.customer_id,
            ConsumptionProfile.period,
        )
        .filter(ConsumptionProfile.is_active == 1)
        .distinct()
        .all()
    )

    return {
        "status": "ok",
        "market_data_periods": market_periods,
        "yekdem_periods": yekdem_periods,
        "consumption_profiles": [
            {"customer_id": r[0], "period": r[1]}
            for r in consumption_periods
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Bayi Segment Endpoint'i (Public — frontend doğrulaması için)
# ═══════════════════════════════════════════════════════════════════════════════

# Bayi Komisyon Segmentleri — PUAN PAYLAŞIMI MODELİ (tek doğru kaynak)
BAYI_SEGMENTS = [
    {"name": "Özel Onay",  "min_multiplier": 1.01, "max_multiplier": 1.03, "bayi_points": 0,   "requires_approval": True},
    {"name": "Sabit",      "min_multiplier": 1.03, "max_multiplier": 1.06, "bayi_points": 1,   "requires_approval": False},
    {"name": "Artırılmış", "min_multiplier": 1.06, "max_multiplier": 1.09, "bayi_points": 1.5, "requires_approval": False},
    {"name": "Yüksek",     "min_multiplier": 1.09, "max_multiplier": 1.12, "bayi_points": 2,   "requires_approval": False},
    {"name": "Yüksek+",    "min_multiplier": 1.12, "max_multiplier": 1.15, "bayi_points": 3,   "requires_approval": False},
    {"name": "Premium",    "min_multiplier": 1.15, "max_multiplier": 99,   "bayi_points": 4,   "requires_approval": False},
]


@pricing_router.get("/bayi-segments")
def list_bayi_segments():
    """Bayi komisyon segmentlerini listele — frontend doğrulaması için.

    Frontend bu endpoint'i kullanarak segment tanımlarını backend ile senkronize eder.
    """
    return {
        "status": "ok",
        "count": len(BAYI_SEGMENTS),
        "segments": BAYI_SEGMENTS,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Dağıtım Tarife Endpoint'leri (Public — admin key gerekmez)
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.get("/distribution-tariffs")
def list_distribution_tariffs_public(
    period: Optional[str] = Query(
        default=None,
        description="Dönem (YYYY-MM). Belirtilmezse en güncel tarife döner.",
    ),
):
    """EPDK dağıtım tarifelerini listele — public endpoint (admin key gerekmez).

    Frontend bu endpoint'i kullanarak dönem bazlı tarifeleri çeker.
    """
    tariffs = _get_all_tariffs(period)
    return {
        "status": "ok",
        "period": period,
        "count": len(tariffs),
        "tariffs": tariffs,
    }


@pricing_router.get("/distribution-tariffs/lookup")
def lookup_distribution_tariff_public(
    voltage: str = Query(
        ..., description="Gerilim seviyesi: AG veya OG",
    ),
    group: str = Query(
        default="sanayi", description="Tarife grubu: sanayi, ticarethane, mesken, vb.",
    ),
    term: str = Query(
        default="çift_terim", description="Terim tipi: tek_terim veya çift_terim (TT/ÇT)",
    ),
    period: Optional[str] = Query(
        default=None,
        description="Dönem (YYYY-MM). Belirtilmezse en güncel tarife döner.",
    ),
):
    """Tek dağıtım tarifesi lookup — public endpoint (admin key gerekmez).

    Frontend bu endpoint'i kullanarak belirli bir tarife kombinasyonunu sorgular.
    """
    result = _lookup_dist_tariff(group, voltage, term, period)
    return {
        "status": "ok",
        "success": result.success,
        "unit_price_tl_per_kwh": result.unit_price,
        "tariff_key": result.tariff_key,
        "normalized": {
            "group": result.normalized_group,
            "voltage": result.normalized_voltage,
            "term": result.normalized_term,
        },
        "error_message": result.error_message,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Rapor Endpoint'leri
# ═══════════════════════════════════════════════════════════════════════════════


@pricing_router.post("/report/pdf")
def report_pdf(
    req: AnalyzeRequest,
    customer_name: Optional[str] = None,
    contact_person: Optional[str] = None,
    report_mode: str = "internal",
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """PDF fiyatlama analiz raporu üret ve indir.
    
    report_mode: "internal" (watermark yok) veya "demo" (watermark var).
    """
    from fastapi.responses import Response

    # Analiz hesapla (analyze endpoint'i ile aynı mantık)
    analysis = analyze(req, db)
    analysis_dict = analysis.model_dump() if hasattr(analysis, 'model_dump') else analysis

    # Simülasyon ekle (PDF'de simülasyon tablosu için)
    sim_rows = run_simulation(
        _load_market_records(db, req.period),
        _get_or_generate_consumption(
            db, req.period, req.customer_id,
            req.use_template, req.template_name, req.template_monthly_kwh,
            t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh,
        ),
        yekdem_tl_per_mwh=analysis_dict.get("supplier_cost", {}).get("yekdem_tl_per_mwh", 0),
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )
    analysis_dict["simulation"] = [r.model_dump() for r in sim_rows]

    # Saatlik detay ekle (hour_costs)
    hourly_result = calculate_hourly_costs(
        _load_market_records(db, req.period),
        _get_or_generate_consumption(
            db, req.period, req.customer_id,
            req.use_template, req.template_name, req.template_monthly_kwh,
            t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh,
        ),
        yekdem_tl_per_mwh=analysis_dict.get("supplier_cost", {}).get("yekdem_tl_per_mwh", 0),
        multiplier=req.multiplier,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )
    analysis_dict["hour_costs"] = [e.model_dump() for e in hourly_result.hour_costs]

    pdf_bytes = generate_pdf_report(
        analysis_dict,
        customer_name=customer_name or req.customer_id,
        contact_person=contact_person,
        report_mode=report_mode,
    )

    filename = f"pricing_analysis_{req.period}_{req.customer_id or 'template'}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@pricing_router.post("/report/excel")
def report_excel(
    req: AnalyzeRequest,
    customer_name: Optional[str] = None,
    db: Session = Depends(get_db),
    _key: str | None = Depends(_require_pricing_key),
):
    """Excel fiyatlama analiz raporu üret ve indir."""
    from fastapi.responses import Response

    # Analiz hesapla
    analysis = analyze(req, db)
    analysis_dict = analysis.model_dump() if hasattr(analysis, 'model_dump') else analysis

    # Simülasyon ekle
    sim_rows = run_simulation(
        _load_market_records(db, req.period),
        _get_or_generate_consumption(
            db, req.period, req.customer_id,
            req.use_template, req.template_name, req.template_monthly_kwh,
            t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh,
        ),
        yekdem_tl_per_mwh=analysis_dict.get("supplier_cost", {}).get("yekdem_tl_per_mwh", 0),
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )
    analysis_dict["simulation"] = [r.model_dump() for r in sim_rows]

    # Saatlik detay ekle
    hourly_result = calculate_hourly_costs(
        _load_market_records(db, req.period),
        _get_or_generate_consumption(
            db, req.period, req.customer_id,
            req.use_template, req.template_name, req.template_monthly_kwh,
            t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh,
        ),
        yekdem_tl_per_mwh=analysis_dict.get("supplier_cost", {}).get("yekdem_tl_per_mwh", 0),
        multiplier=req.multiplier,
        imbalance_params=req.imbalance_params,
        dealer_commission_pct=req.dealer_commission_pct,
    )
    analysis_dict["hour_costs"] = [e.model_dump() for e in hourly_result.hour_costs]

    excel_bytes = generate_excel_report(
        analysis_dict,
        customer_name=customer_name or req.customer_id,
    )

    filename = f"pricing_analysis_{req.period}_{req.customer_id or 'template'}.xlsx"
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
