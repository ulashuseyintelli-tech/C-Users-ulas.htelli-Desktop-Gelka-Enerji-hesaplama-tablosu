import io
import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query, Header, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session
from dotenv import load_dotenv

load_dotenv()

from .models import InvoiceExtraction, OfferParams, CalculationResult, ValidationResult, InvoiceStatus, JobType, JobStatus, FieldValue, InvoiceMeta
from .extractor import extract_invoice_data, clear_extraction_cache, mask_pii, ExtractionError
from .calculator import calculate_offer
from .validator import validate_extraction
from .database import init_db, get_db, Customer, Offer, Invoice, Job, STORAGE_DIR, API_KEY, API_KEY_ENABLED
from .pdf_generator import generate_offer_html, generate_offer_pdf
from .pdf_render import render_pdf_first_page, get_page1_path
from .image_prep import preprocess_image_bytes
from .job_queue import enqueue_job, enqueue_job_idempotent, get_job_by_id, get_jobs_by_invoice, get_active_job, list_jobs
from .core.config import settings

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Constants for input validation
# ═══════════════════════════════════════════════════════════════════════════════
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_MIME_TYPES = frozenset([
    "image/jpeg",
    "image/jpg", 
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
])
ALLOWED_PDF_MIME_TYPE = "application/pdf"
ALLOWED_HTML_MIME_TYPE = "text/html"
ALLOWED_MIME_TYPES = ALLOWED_IMAGE_MIME_TYPES | {ALLOWED_PDF_MIME_TYPE, ALLOWED_HTML_MIME_TYPE}


def convert_pdf_to_image(pdf_bytes: bytes, max_pages: int = 3) -> tuple[bytes, str]:
    """
    PDF'i optimize edilmiş PNG'ye dönüştür.
    Tüm sayfaları (max_pages'e kadar) dikey birleştirir.
    CK faturalarında dağıtım bedeli genelde 2. sayfada olduğu için önemli.
    
    Returns: (image_bytes, mime_type)
    """
    import pypdfium2 as pdfium
    from PIL import Image
    
    pdf = pdfium.PdfDocument(pdf_bytes)
    page_count = len(pdf)
    
    if page_count == 0:
        pdf.close()
        raise ValueError("PDF boş")
    
    # Tüm sayfaları (max_pages'e kadar) render et
    pages_to_render = min(page_count, max_pages)
    images = []
    
    for i in range(pages_to_render):
        page = pdf[i]
        bitmap = page.render(scale=1.5)  # Daha yüksek çözünürlük
        pil_image = bitmap.to_pil()
        if pil_image.mode not in ("RGB", "L"):
            pil_image = pil_image.convert("RGB")
        images.append(pil_image)
        page.close()
    
    pdf.close()
    
    # Sayfaları dikey birleştir
    if len(images) == 1:
        combined = images[0]
    else:
        total_height = sum(img.height for img in images)
        max_width = max(img.width for img in images)
        combined = Image.new('RGB', (max_width, total_height), (255, 255, 255))
        y_offset = 0
        for img in images:
            combined.paste(img, (0, y_offset))
            y_offset += img.height
    
    # PNG formatı (kalite kaybı yok)
    img_byte_arr = io.BytesIO()
    combined.save(img_byte_arr, format='PNG', optimize=True)
    
    logger.info(f"PDF converted: {page_count} total pages, rendered {pages_to_render} pages, combined image size: {len(img_byte_arr.getvalue())} bytes")
    
    return img_byte_arr.getvalue(), "image/png"


# Ensure storage directory exists
Path(STORAGE_DIR).mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Gelka Enerji API",
    description="Fatura analizi ve teklif hesaplama",
    version="1.0.0"
)


# ═══════════════════════════════════════════════════════════════════════════════
# Security - API Key Authentication + Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════════

# Admin API Key (ayrı, daha güçlü yetki)
# GÜVENLIK: Default key YOK - sadece env'den set edilmeli
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")  # Boş = disabled
ADMIN_API_KEY_ENABLED = os.getenv("ADMIN_API_KEY_ENABLED", "false").lower() == "true"  # Default: kapalı


def require_api_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """
    API key doğrulama + rate limiting dependency.
    API_KEY_ENABLED=false ise kontrol yapılmaz (MVP modu).
    
    Returns:
        API key (for rate limiting key)
    """
    if not API_KEY_ENABLED:
        return x_api_key
    
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "Geçersiz veya eksik API anahtarı"}
        )
    
    # Rate limiting
    from .services.rate_limit import check_rate_limit, RateLimitExceeded
    try:
        check_rate_limit(f"api_key:{x_api_key}")
    except RateLimitExceeded as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Rate limit aşıldı: {e.limit} istek/{e.window}s",
                "retry_after": e.window
            }
        )
    
    return x_api_key


def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    request: Request = None
) -> str:
    """
    Admin API key doğrulama dependency.
    Admin endpoint'leri için zorunlu.
    
    GÜVENLIK:
    - ADMIN_API_KEY_ENABLED=false (default) → bypass (dev mode)
    - ADMIN_API_KEY_ENABLED=true → env'den ADMIN_API_KEY zorunlu
    """
    client_ip = "unknown"
    if request:
        client_ip = request.client.host if request.client else "unknown"
    
    if not ADMIN_API_KEY_ENABLED:
        logger.info(f"[ADMIN] Auth bypassed (disabled), ip={client_ip}")
        return "admin-bypass"
    
    # ADMIN_API_KEY boşsa ve enabled ise → config hatası
    if not ADMIN_API_KEY:
        logger.error(f"[ADMIN] ADMIN_API_KEY not configured but ADMIN_API_KEY_ENABLED=true")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "admin_not_configured",
                "message": "Admin API key yapılandırılmamış. ADMIN_API_KEY env değişkenini ayarlayın."
            }
        )
    
    if not x_admin_key:
        logger.warning(f"[ADMIN] Auth failed: missing key, ip={client_ip}")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "admin_unauthorized",
                "message": "Admin API anahtarı gerekli (X-Admin-Key header)"
            }
        )
    
    if x_admin_key != ADMIN_API_KEY:
        logger.warning(f"[ADMIN] Auth failed: invalid key, ip={client_ip}")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "admin_forbidden",
                "message": "Geçersiz admin API anahtarı"
            }
        )
    
    logger.info(f"[ADMIN] Auth success, ip={client_ip}")
    return x_admin_key


# ═══════════════════════════════════════════════════════════════════════════════
# Storage Service
# ═══════════════════════════════════════════════════════════════════════════════

def save_uploaded_file(filename: str, content: bytes) -> tuple[str, str]:
    """
    Dosyayı storage'a kaydet.
    Returns: (storage_path, file_hash)
    """
    import uuid
    ext = os.path.splitext(filename)[1].lower() or ".bin"
    file_hash = hashlib.sha256(content).hexdigest()
    key = f"{uuid.uuid4()}{ext}"
    path = os.path.join(STORAGE_DIR, key)
    
    with open(path, "wb") as f:
        f.write(content)
    
    return path, file_hash

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    from .incident_service import check_production_guard, validate_environment
    from .config import validate_config, ConfigValidationError
    
    # Sprint 8.8: Config validation (MUST be first!)
    try:
        validate_config()
        logger.info("Config validation passed")
    except ConfigValidationError as e:
        logger.critical(f"FATAL: Config validation failed:\n{e}")
        raise RuntimeError(str(e))
    
    # Sprint 4 P2: ENV whitelist + Production guard
    env = os.getenv("ENV", "development").lower()
    
    # ENV whitelist kontrolü
    env_valid, env_error = validate_environment(env)
    if not env_valid:
        logger.critical(f"FATAL: {env_error}")
        raise RuntimeError(env_error)
    
    # Production guard
    guard_ok, guard_error = check_production_guard(env, API_KEY_ENABLED, API_KEY or "")
    if not guard_ok:
        logger.critical(f"FATAL: {guard_error}")
        raise RuntimeError(guard_error)
    
    if env == "production":
        logger.info(f"Production mode: API key protection enabled (key length: {len(API_KEY or '')})")
    elif not API_KEY_ENABLED:
        logger.warning(f"WARNING: Running in {env} mode without API key protection")
    
    init_db()
    logger.info("Database initialized")
    
    # Sprint 8.9.1: Pilot guard config logging
    from .pilot_guard import log_pilot_config
    log_pilot_config()

    # Ops-Guard: Load guard config at startup (Feature: ops-guard, Task 2.2)
    from .guard_config import load_guard_config
    load_guard_config()
    
    # Sample market prices data (dev/test için)
    _add_sample_market_prices()
    
    # EPDK tarifeleri DB'ye seed et (yoksa)
    _seed_distribution_tariffs()


def _add_sample_market_prices():
    """
    Sample PTF/YEKDEM verisi ekle (eğer yoksa).
    Production'da admin panelden girilmeli.
    """
    from .database import SessionLocal, MarketReferencePrice
    
    sample_data = [
        ("2024-11", 2850.0, 350.0),
        ("2024-12", 2920.0, 355.0),
        ("2025-01", 2974.1, 364.0),
        ("2025-02", 3050.0, 370.0),
    ]
    
    db = SessionLocal()
    try:
        for period, ptf, yekdem in sample_data:
            existing = db.query(MarketReferencePrice).filter(
                MarketReferencePrice.period == period
            ).first()
            
            if not existing:
                record = MarketReferencePrice(
                    period=period,
                    ptf_tl_per_mwh=ptf,
                    yekdem_tl_per_mwh=yekdem,
                    source_note="Sample data (dev)",
                    is_locked=0
                )
                db.add(record)
                logger.info(f"Sample market price added: {period}")
        
        db.commit()
    except Exception as e:
        logger.warning(f"Could not add sample market prices: {e}")
        db.rollback()
    finally:
        db.close()


def _seed_distribution_tariffs():
    """
    EPDK dağıtım tarifelerini DB'ye seed et (yoksa).
    In-memory DISTRIBUTION_TARIFFS listesinden alır.
    """
    from .database import SessionLocal, DistributionTariffDB
    from .distribution_tariffs import DISTRIBUTION_TARIFFS
    
    db = SessionLocal()
    try:
        # Mevcut kayıt var mı kontrol et
        existing_count = db.query(DistributionTariffDB).count()
        if existing_count > 0:
            logger.info(f"Distribution tariffs already seeded: {existing_count} records")
            return
        
        # In-memory listeden seed et (DISTRIBUTION_TARIFFS artık DistributionTariff listesi)
        for tariff in DISTRIBUTION_TARIFFS:
            record = DistributionTariffDB(
                valid_from="2025-01-01",
                valid_to=None,  # Hala geçerli
                tariff_group=tariff.tariff_group,
                voltage_level=tariff.voltage_level,
                term_type=tariff.term_type,
                unit_price_tl_per_kwh=tariff.unit_price_tl_per_kwh,
                source_note="EPDK Ocak 2025 tarifesi (seed)"
            )
            db.add(record)
        
        db.commit()
        logger.info(f"Distribution tariffs seeded: {len(DISTRIBUTION_TARIFFS)} records")
    except Exception as e:
        logger.warning(f"Could not seed distribution tariffs: {e}")
        db.rollback()
    finally:
        db.close()


def validate_uploaded_file(file: UploadFile, content: bytes) -> None:
    """
    Validate uploaded file for size and MIME type.
    
    Raises HTTPException with 400 status for invalid files.
    Requirements: 1.4, 9.1-9.5
    """
    # Validate file size
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "file_too_large",
                "message": f"Dosya boyutu çok büyük. Maksimum: {MAX_FILE_SIZE_BYTES // (1024*1024)} MB",
                "max_size_bytes": MAX_FILE_SIZE_BYTES,
                "actual_size_bytes": len(content)
            }
        )
    
    # Validate MIME type strictly
    content_type = file.content_type or ""
    
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_file_type",
                "message": "Desteklenmeyen dosya formatı. Sadece görsel (JPG, PNG, WebP, GIF, BMP, TIFF), PDF veya HTML dosyası yükleyin.",
                "allowed_types": list(ALLOWED_MIME_TYPES),
                "received_type": content_type
            }
        )
    
    # Validate file is not empty
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "empty_file",
                "message": "Dosya boş. Lütfen geçerli bir fatura dosyası yükleyin."
            }
        )

# ── CORS — environment-aware ──────────────────────────────────────────────────
_CORS_ORIGINS_ENV = os.getenv("CORS_ALLOWED_ORIGINS", "")  # comma-separated
_cors_origins: list[str] = (
    [o.strip() for o in _CORS_ORIGINS_ENV.split(",") if o.strip()]
    if _CORS_ORIGINS_ENV
    else ["*"]  # dev fallback — prod MUST set CORS_ALLOWED_ORIGINS
)
_cors_env = os.getenv("ENV", "development").lower()

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_env != "production",  # prod'da False (cookie yoksa gereksiz)
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key", "X-Admin-Key", "X-Tenant-Id"],
)

# ── Metrics Middleware ────────────────────────────────────────────────────────
from .metrics_middleware import MetricsMiddleware
app.add_middleware(MetricsMiddleware)

# ── Guard Decision Middleware (Feature: runtime-guard-decision, Wiring) ───────
# Added BEFORE OpsGuardMiddleware → inner layer. OpsGuard (outer) runs first;
# if it denies, GuardDecision never executes. Decision layer only activates
# on the allow path.
from .guards.guard_decision_middleware import GuardDecisionMiddleware
app.add_middleware(GuardDecisionMiddleware)

# ── Ops-Guard Middleware (no-op skeleton — Feature: ops-guard, Task 2.2) ──────
from .ops_guard_middleware import OpsGuardMiddleware
app.add_middleware(OpsGuardMiddleware)


# ── Prometheus Metrics Endpoint ───────────────────────────────────────────────
@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    """
    GET /metrics — Prometheus text exposition format.
    No authentication required (standard for metrics scraping).
    Instance-level registry only — no global/default registry.
    """
    from .ptf_metrics import get_ptf_metrics
    from fastapi.responses import Response

    return Response(
        content=get_ptf_metrics().generate_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ── Telemetry Event Ingestion ─────────────────────────────────────────────────
import re as _re
import time as _time
import collections as _collections
from pydantic import BaseModel as _PydanticBaseModel

_EVENT_NAME_PREFIX = "ptf_admin."
_MAX_BATCH_SIZE = 100
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60  # requests per window per IP
_EVENT_NAME_MAX_LEN = 100  # max chars for event name
_EVENT_NAME_PATTERN = _re.compile(r"^[a-z0-9._]+$")  # ASCII slug: lowercase + digits + dot + underscore
_MAX_PROPERTIES_KEYS = 20  # max number of keys in properties dict

# Simple in-memory rate limiter: IP → deque of timestamps
_rate_limit_buckets: dict[str, _collections.deque] = {}


class TelemetryEvent(_PydanticBaseModel):
    event: str
    properties: dict = {}
    timestamp: str  # ISO 8601


class TelemetryEventsRequest(_PydanticBaseModel):
    events: List[TelemetryEvent]


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = _time.monotonic()
    bucket = _rate_limit_buckets.setdefault(client_ip, _collections.deque())
    # Evict expired entries
    while bucket and bucket[0] < now - _RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT_MAX:
        return False
    bucket.append(now)
    return True


def _validate_event_name(name: str) -> str | None:
    """Validate event name. Returns rejection reason or None if valid."""
    if not name.startswith(_EVENT_NAME_PREFIX):
        return "UNKNOWN_PREFIX"
    if len(name) > _EVENT_NAME_MAX_LEN:
        return "NAME_TOO_LONG"
    if not _EVENT_NAME_PATTERN.match(name):
        return "INVALID_CHARSET"
    return None


@app.post("/admin/telemetry/events", include_in_schema=False)
async def ingest_telemetry_events(body: TelemetryEventsRequest, request: Request):
    """
    POST /admin/telemetry/events — Frontend event ingestion.

    Auth: YOK (bilinçli istisna). Endpoint yalnızca counter artırır,
    başka write/read yapmaz. Risk profili GET /metrics ile aynı sınıfta.

    Validation rules:
    - event name: must start with "ptf_admin.", max 100 chars, ASCII slug [a-z0-9._]
    - properties: max 20 keys (values not inspected, never stored/labeled)
    - batch: max 100 events per request
    - rate limit: 60 req/min/IP

    Dedupe: intentionally absent. Fire-and-forget semantics mean retries
    may inflate counters. Accepted trade-off for simplicity.
    """
    # Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Max batch size check
    if len(body.events) > _MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Batch size {len(body.events)} exceeds maximum {_MAX_BATCH_SIZE}",
        )

    from .ptf_metrics import get_ptf_metrics
    from .event_store import get_event_store

    metrics = get_ptf_metrics()
    store = get_event_store()

    accepted = 0
    rejected = 0
    reject_reasons: dict[str, int] = {}

    for ev in body.events:
        # Validate event name
        reason = _validate_event_name(ev.event)
        if reason is not None:
            store.increment_rejected()
            rejected += 1
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
            continue

        # Validate properties key count (defense against payload bloat)
        if len(ev.properties) > _MAX_PROPERTIES_KEYS:
            store.increment_rejected()
            rejected += 1
            reject_reasons["TOO_MANY_PROPS"] = reject_reasons.get("TOO_MANY_PROPS", 0) + 1
            continue

        # Valid event — increment counters
        store.increment(ev.event)
        metrics.inc_frontend_event(ev.event)
        accepted += 1

    # Single INFO line: counts only, no event names (PII/garbage risk)
    logger.info(
        f"telemetry_ingest accepted={accepted} rejected={rejected}"
        f" distinct_events={store.get_counters().__len__()}"
        + (f" reject_reasons={reject_reasons}" if reject_reasons else "")
    )

    return {
        "status": "ok",
        "accepted_count": accepted,
        "rejected_count": rejected,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(db: Session = Depends(get_db)):
    """
    Readiness check - Sprint 8.8 + 8.9
    
    Checks:
    - config: Config validation passed
    - database: DB connection working
    - openai_api: API key configured
    - queue: No stuck jobs (optional)
    
    Returns 200 if ready, 503 if not ready.
    
    Sprint 8.9: Includes build_id and config_hash for version tracking.
    """
    from datetime import datetime, timezone
    from .config import validate_config, ConfigValidationError, get_config_summary, get_config_hash
    import time
    import subprocess
    
    # Get build ID
    def get_build_id() -> str:
        build_id = os.getenv("BUILD_ID")
        if build_id:
            return build_id
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return f"git:{result.stdout.strip()}"
        except:
            pass
        return "unknown"
    
    checks = {}
    failing_checks = []
    
    # Check 1: Config validation
    try:
        validate_config()
        checks["config"] = {"status": "ok", "validated": True}
    except ConfigValidationError as e:
        checks["config"] = {"status": "error", "message": str(e)[:200]}
        failing_checks.append("config")
    
    # Check 2: Database connection
    try:
        start = time.time()
        db.execute("SELECT 1")
        latency_ms = int((time.time() - start) * 1000)
        
        if latency_ms > 500:
            checks["database"] = {"status": "error", "latency_ms": latency_ms, "message": "High latency"}
            failing_checks.append("database")
        elif latency_ms > 100:
            checks["database"] = {"status": "warning", "latency_ms": latency_ms}
        else:
            checks["database"] = {"status": "ok", "latency_ms": latency_ms}
    except Exception as e:
        checks["database"] = {"status": "error", "message": str(e)[:100]}
        failing_checks.append("database")
    
    # Check 3: OpenAI API key
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key and len(openai_key) > 10:
        checks["openai_api"] = {"status": "ok", "key_configured": True}
    else:
        checks["openai_api"] = {"status": "warning", "message": "API key not configured"}
    
    # Check 4: Queue status (check for stuck jobs)
    try:
        from .database import Job
        from datetime import timedelta
        
        # Jobs stuck for more than 10 minutes
        stuck_threshold = datetime.utcnow() - timedelta(minutes=10)
        stuck_jobs = db.query(Job).filter(
            Job.status == "processing",
            Job.updated_at < stuck_threshold
        ).count()
        
        pending_jobs = db.query(Job).filter(Job.status == "pending").count()
        
        if stuck_jobs > 0:
            checks["queue"] = {
                "status": "warning",
                "depth": pending_jobs,
                "stuck_count": stuck_jobs,
                "message": f"{stuck_jobs} stuck job(s) detected"
            }
        else:
            checks["queue"] = {"status": "ok", "depth": pending_jobs}
    except Exception as e:
        checks["queue"] = {"status": "warning", "message": f"Could not check queue: {str(e)[:50]}"}
    
    # Last activity (optional info)
    last_activity = {}
    try:
        from .database import Incident
        last_incident = db.query(Incident).order_by(Incident.created_at.desc()).first()
        if last_incident and last_incident.created_at:
            last_activity["last_incident_at"] = last_incident.created_at.isoformat()
    except:
        pass
    
    # Sprint 8.9.1: Pilot status
    from .pilot_guard import is_pilot_enabled, get_pilot_tenant_id, get_pilot_rate_status
    pilot_status = {
        "enabled": is_pilot_enabled(),
        "tenant_id": get_pilot_tenant_id(),
        "rate_limit": get_pilot_rate_status(),
    }
    
    # Build response
    status = "not_ready" if failing_checks else "ready"
    response = {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "build_id": get_build_id(),
        "config_hash": get_config_hash(),
        "checks": checks,
        "pilot": pilot_status,
    }
    
    if last_activity:
        response["last_activity"] = last_activity
    
    if failing_checks:
        response["failing_checks"] = failing_checks
    
    # Return 503 if not ready
    if failing_checks:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content=response)
    
    return response


@app.delete("/cache")
async def clear_cache():
    """
    Extraction cache'ini temizle.
    
    Kullanım: Aynı faturayı farklı parametrelerle tekrar analiz etmek için.
    """
    count = clear_extraction_cache()
    logger.info(f"Cache cleared via API: {count} entries removed")
    return {
        "status": "ok",
        "message": f"Cache temizlendi: {count} kayıt silindi",
        "cleared_count": count
    }

@app.post("/analyze-invoice", response_model=dict)
async def analyze_invoice(
    file: UploadFile = File(...),
    fast_mode: bool = Query(default=False, description="Hızlı mod: gpt-4o-mini (varsayılan: false - gpt-4o kullan)")
):
    """
    Fatura görselini analiz et ve alanları çıkar.
    
    Args:
        file: Fatura görseli, PDF veya HTML
        fast_mode: True = hızlı analiz (gpt-4o-mini), False = detaylı analiz (gpt-4o)
    
    Requirements: 1.1-1.5, 2.1-2.8, 9.1
    """
    content = await file.read()
    
    # Validate file (size, MIME type, empty check)
    validate_uploaded_file(file, content)
    
    # HTML ise görsele çevir (analyze-invoice endpoint)
    mime_type = file.content_type
    pdf_text_hint = ""  # Hibrit yaklaşım için
    
    if file.content_type == ALLOWED_HTML_MIME_TYPE or (file.filename and file.filename.lower().endswith('.html')):
        try:
            from .html_render import render_html_to_image_async
            logger.info(f"[analyze] Converting HTML to image, size: {len(content)} bytes")
            content = await render_html_to_image_async(content, width=1200)
            mime_type = "image/png"
            logger.info(f"[analyze] HTML converted to image: {len(content)} bytes")
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            logger.error(f"[analyze] HTML conversion error: {error_detail}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "html_conversion_error",
                    "message": f"HTML dönüştürme hatası: {type(e).__name__}: {str(e)}"
                }
            )
    
    # PDF ise sayfaları görsele çevir (tüm sayfalar birleştirilir)
    elif file.content_type == ALLOWED_PDF_MIME_TYPE:
        # KATMAN 1: PDF'den metin çıkar (hibrit yaklaşım)
        try:
            from .pdf_text_extractor import extract_text_from_pdf, create_extraction_hint
            pdf_extracted = extract_text_from_pdf(content)
            pdf_text_hint = create_extraction_hint(pdf_extracted)
            logger.info(f"[analyze] PDF text extraction: quality={pdf_extracted.extraction_quality}, odenecek={pdf_extracted.odenecek_tutar}")
        except Exception as e:
            logger.warning(f"[analyze] PDF text extraction failed: {e}")
        
        # KATMAN 2: PDF'i görsele çevir
        try:
            content, mime_type = convert_pdf_to_image(content, max_pages=3)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "pdf_conversion_error",
                    "message": f"PDF dönüştürme hatası: {str(e)}"
                }
            )
    
    try:
        # External API çağrısını wrapper ile sar (EXTERNAL_API, read path)
        import asyncio as _asyncio
        from .guards.dependency_wrapper import CircuitOpenError
        wrapper = _get_wrapper("external_api")
        try:
            extraction = await wrapper.call(
                _asyncio.to_thread,
                extract_invoice_data,
                content, mime_type, fast_mode=fast_mode, text_hint=pdf_text_hint,
                is_write=False,
            )
        except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
            raise _map_wrapper_error_to_http(exc)

        validation = validate_extraction(extraction)
        
        # Sanity check: Eğer validation başarısız ve kritik hatalar varsa, fast_mode=False ile tekrar dene
        if fast_mode and not validation.is_ready_for_pricing and validation.errors:
            logger.warning(f"Fast mode failed validation, retrying with full model. Errors: {validation.errors}")
            # Cache'i temizle ve tekrar dene
            from .extractor import compute_image_hash, _extraction_cache
            image_hash = compute_image_hash(content)
            if image_hash in _extraction_cache:
                del _extraction_cache[image_hash]
            
            try:
                extraction = await wrapper.call(
                    _asyncio.to_thread,
                    extract_invoice_data,
                    content, mime_type, fast_mode=False, text_hint=pdf_text_hint,
                    is_write=False,
                )
            except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
                raise _map_wrapper_error_to_http(exc)
            validation = validate_extraction(extraction)
            fast_mode = False  # Meta'da doğru göster
        
        return {
            "extraction": extraction.model_dump(),
            "validation": validation.model_dump(),
            "meta": {
                "fast_mode": fast_mode,
                "model": "gpt-4o-mini" if fast_mode else "gpt-4o"
            }
        }
    except ExtractionError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "extraction_error",
                "message": f"OpenAI API hatası: {str(e)}",
                "retry": True
            }
        )
    except Exception as e:
        logger.exception("Unexpected error during invoice analysis")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "analysis_error",
                "message": f"Analiz hatası: {str(e)}"
            }
        )

@app.post("/calculate-offer", response_model=CalculationResult)
async def calculate_offer_endpoint(
    extraction: InvoiceExtraction,
    params: OfferParams = None,
    db: Session = Depends(get_db)
):
    """
    Çıkarılan verilerle teklif hesapla.
    
    PTF/YEKDEM otomatik olarak fatura dönemine göre DB'den çekilir.
    Override için: use_reference_prices=False ve weighted_ptf_tl_per_mwh/yekdem_tl_per_mwh değerlerini verin.
    
    Requirements: 5.1-5.9, 6.1-6.4, 9.2
    """
    from .calculator import CalculationError
    
    if params is None:
        params = OfferParams()
    
    # DB çağrısını wrapper ile sar (read path)
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        return await wrapper.call(
            _asyncio.to_thread,
            calculate_offer,
            extraction, params, db=db,
            is_write=False,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    except CalculationError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "calculation_error",
                "message": str(e)
            }
        )

@app.post("/full-process", response_model=dict)
async def full_process(
    file: UploadFile = File(...),
    weighted_ptf_tl_per_mwh: Optional[float] = Query(default=None, description="PTF (TL/MWh) - boş bırakılırsa DB'den çekilir"),
    yekdem_tl_per_mwh: Optional[float] = Query(default=None, description="YEKDEM (TL/MWh) - boş bırakılırsa DB'den çekilir"),
    agreement_multiplier: float = 1.01,
    use_reference_prices: bool = Query(default=True, description="True: DB'den çek, False: verilen değerleri kullan"),
    fast_mode: bool = Query(default=False, description="Hızlı mod: gpt-4o-mini (varsayılan: false - gpt-4o kullan)"),
    debug: bool = Query(default=False, description="Debug modu: LLM raw output dahil"),
    db: Session = Depends(get_db)
):
    """
    Tek endpoint: Fatura yükle → Analiz → Hesapla → Sonuç.
    
    PTF/YEKDEM:
    - use_reference_prices=True (default): Fatura dönemine göre DB'den otomatik çekilir
    - use_reference_prices=False: weighted_ptf_tl_per_mwh ve yekdem_tl_per_mwh değerleri kullanılır
    
    Debug:
    - debug=True: LLM raw output ve detaylı debug bilgisi döner
    
    Desteklenen formatlar: PDF, HTML, görsel (JPG, PNG, etc.)
    
    Requirements: 9.3
    """
    import uuid
    from .calculator import CalculationError
    from .models import DebugMeta
    
    # Trace ID üret
    trace_id = str(uuid.uuid4())[:8]
    logger.info(f"[{trace_id}] full-process started: file={file.filename}, fast_mode={fast_mode}, debug={debug}")
    
    content = await file.read()
    
    # Validate file (size, MIME type, empty check)
    validate_uploaded_file(file, content)
    
    # Debug meta başlat
    debug_meta = DebugMeta(trace_id=trace_id)
    debug_meta.warnings = []
    debug_meta.errors = []
    
    # PDF text hint (hibrit yaklaşım için)
    pdf_text_hint = ""
    pdf_extracted = None
    roi_payable_total = None  # ROI crop'tan gelen değer
    roi_multi_fields = None  # ROI multi-field extraction sonucu
    
    # HTML ise görsele çevir (full-process endpoint)
    mime_type = file.content_type
    if file.content_type == ALLOWED_HTML_MIME_TYPE or (file.filename and file.filename.lower().endswith('.html')):
        try:
            from .html_render import render_html_to_image_async
            logger.info(f"[{trace_id}] Converting HTML to image, size: {len(content)} bytes")
            content = await render_html_to_image_async(content, width=1200)
            mime_type = "image/png"
            logger.info(f"[{trace_id}] HTML converted to image: {len(content)} bytes")
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            logger.error(f"[{trace_id}] HTML conversion error: {error_detail}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "html_conversion_error",
                    "message": f"HTML dönüştürme hatası: {type(e).__name__}: {str(e)}",
                    "trace_id": trace_id
                }
            )
    
    # PDF ise sayfaları görsele çevir (tüm sayfalar birleştirilir)
    elif file.content_type == ALLOWED_PDF_MIME_TYPE:
        # KATMAN 1: PDF'den metin çıkar (hibrit yaklaşım)
        try:
            from .pdf_text_extractor import extract_text_from_pdf, create_extraction_hint
            pdf_extracted = extract_text_from_pdf(content)
            pdf_text_hint = create_extraction_hint(pdf_extracted)
            
            if pdf_extracted.odenecek_tutar:
                debug_meta.warnings.append(f"PDF'den okunan Ödenecek Tutar: {pdf_extracted.odenecek_tutar:.2f} TL")
            if pdf_extracted.kdv_tutari:
                debug_meta.warnings.append(f"PDF'den okunan KDV: {pdf_extracted.kdv_tutari:.2f} TL")
                
            logger.info(f"[{trace_id}] PDF text extraction: quality={pdf_extracted.extraction_quality}, odenecek={pdf_extracted.odenecek_tutar}")
        except Exception as e:
            logger.warning(f"[{trace_id}] PDF text extraction failed: {e}")
        
        # KATMAN 2: PDF'i görsele çevir
        try:
            # ROI crop için sayfa 1'i ayrı tut
            page1_content = None
            try:
                import pypdfium2 as pdfium
                from PIL import Image
                pdf = pdfium.PdfDocument(content)
                if len(pdf) > 0:
                    page = pdf[0]
                    bitmap = page.render(scale=1.5)
                    pil_image = bitmap.to_pil()
                    if pil_image.mode == "RGBA":
                        pil_image = pil_image.convert("RGB")
                    buffer = io.BytesIO()
                    pil_image.save(buffer, format='PNG')
                    page1_content = buffer.getvalue()
                    logger.info(f"[{trace_id}] Page 1 rendered for ROI: {pil_image.width}x{pil_image.height}")
                    pdf.close()
            except Exception as e:
                logger.warning(f"[{trace_id}] Page 1 render failed: {e}")
            
            content, mime_type = convert_pdf_to_image(content, max_pages=3)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "pdf_conversion_error",
                    "message": f"PDF dönüştürme hatası: {str(e)}",
                    "trace_id": trace_id
                }
            )
        
        # KATMAN 2.5: ROI Crop (pdfplumber başarısız olduysa)
        # Taranmış PDF'lerde metin çıkmaz, bu durumda bölge kırpma ile dene
        roi_payable_total = None
        
        if pdf_extracted and not pdf_extracted.odenecek_tutar and pdf_extracted.extraction_quality == "poor":
            # Sayfa 1'i kullan (birleştirilmiş görsel değil)
            roi_image = page1_content if page1_content else content
            try:
                from .region_extractor import (
                    get_regions_for_vendor, 
                    crop_multiple_regions,
                    create_multi_field_extraction_func,
                    MultiFieldResult
                )
                from .extractor import get_openai_client
                
                logger.info(f"[{trace_id}] PDF is scanned, trying ROI multi-field extraction")
                
                # Vendor henüz bilinmiyor, generic bölgeler kullan
                regions = get_regions_for_vendor("unknown")
                cropped_images = crop_multiple_regions(roi_image, regions)
                
                if cropped_images:
                    # OpenAI client al
                    client = get_openai_client()
                    extract_func = create_multi_field_extraction_func(client, model=settings.openai_model_accurate)
                    
                    # Multi-field extraction - en iyi crop'u bul
                    from .region_extractor import extract_multi_fields_from_crops
                    roi_multi_fields = extract_multi_fields_from_crops(cropped_images, extract_func)
                    
                    # Sonuçları logla
                    if roi_multi_fields:
                        roi_payable_total = roi_multi_fields.payable_total
                        
                        found_fields = []
                        if roi_multi_fields.payable_total:
                            found_fields.append(f"payable_total={roi_multi_fields.payable_total:.2f}")
                        if roi_multi_fields.vat_amount:
                            found_fields.append(f"vat={roi_multi_fields.vat_amount:.2f}")
                        if roi_multi_fields.energy_total:
                            found_fields.append(f"energy={roi_multi_fields.energy_total:.2f}")
                        if roi_multi_fields.distribution_total:
                            found_fields.append(f"dist={roi_multi_fields.distribution_total:.2f}")
                        if roi_multi_fields.consumption_kwh:
                            found_fields.append(f"kwh={roi_multi_fields.consumption_kwh:.2f}")
                        
                        logger.info(f"[{trace_id}] ROI multi-field success: {roi_multi_fields.source_region} → {', '.join(found_fields)}")
                        debug_meta.warnings.append(f"ROI crop'tan okunan alanlar ({roi_multi_fields.source_region}): {', '.join(found_fields)}")
                        
                        # Hint'e ekle
                        hint_parts = []
                        if roi_multi_fields.payable_total:
                            hint_parts.append(f"Ödenecek Tutar: {roi_multi_fields.payable_total:.2f} TL")
                        if roi_multi_fields.vat_amount:
                            hint_parts.append(f"KDV: {roi_multi_fields.vat_amount:.2f} TL")
                        if roi_multi_fields.energy_total:
                            hint_parts.append(f"Enerji Bedeli: {roi_multi_fields.energy_total:.2f} TL")
                        if roi_multi_fields.distribution_total:
                            hint_parts.append(f"Dağıtım Bedeli: {roi_multi_fields.distribution_total:.2f} TL")
                        
                        if hint_parts:
                            pdf_text_hint += f"\n\n⚠️ ROI CROP'TAN OKUNAN DEĞERLER:\n" + "\n".join(hint_parts) + "\nBu değerleri doğrula!"
                            
            except Exception as e:
                logger.warning(f"[{trace_id}] ROI multi-field extraction failed: {e}")
    
    try:
        # Extraction (debug modunda raw output capture)
        extraction_cache_hit = False
        llm_raw_output = None
        
        if debug:
            from .extractor import compute_image_hash, get_cached_extraction
            image_hash = compute_image_hash(content)
            cached = get_cached_extraction(image_hash)
            extraction_cache_hit = cached is not None
        
        extraction = extract_invoice_data(content, mime_type, fast_mode=fast_mode, text_hint=pdf_text_hint)
        validation = validate_extraction(extraction)
        
        # Debug meta güncelle
        debug_meta.llm_model_used = settings.openai_model_fast if fast_mode else settings.openai_model_accurate
        debug_meta.extraction_cache_hit = extraction_cache_hit
        
        # Sanity check: Eğer validation başarısız ve kritik hatalar varsa, fast_mode=False ile tekrar dene
        if fast_mode and not validation.is_ready_for_pricing and validation.errors:
            logger.warning(f"[{trace_id}] Fast mode failed validation, retrying with full model. Errors: {validation.errors}")
            debug_meta.warnings.append("Fast mode başarısız, full model ile tekrar denendi")
            
            from .extractor import compute_image_hash, _extraction_cache
            image_hash = compute_image_hash(content)
            if image_hash in _extraction_cache:
                del _extraction_cache[image_hash]
            
            extraction = extract_invoice_data(content, mime_type, fast_mode=False, text_hint=pdf_text_hint)
            validation = validate_extraction(extraction)
            fast_mode = False
            debug_meta.llm_model_used = settings.openai_model_accurate
        
        # ═══════════════════════════════════════════════════════════════════════
        # KATMAN 4: Cross-validation (pdfplumber / ROI vs Vision)
        # ═══════════════════════════════════════════════════════════════════════
        # Öncelik: pdfplumber > ROI crop > Vision
        reference_total = None
        reference_source = None
        
        if pdf_extracted and pdf_extracted.odenecek_tutar:
            reference_total = pdf_extracted.odenecek_tutar
            reference_source = "pdfplumber"
        elif roi_payable_total:
            reference_total = roi_payable_total
            reference_source = "roi_crop"
        
        if reference_total:
            from .parse_tr import reconcile_amount
            from decimal import Decimal
            
            # Vision'dan gelen değer
            vision_total = None
            if extraction.invoice_total_with_vat_tl and extraction.invoice_total_with_vat_tl.value:
                vision_total = Decimal(str(extraction.invoice_total_with_vat_tl.value))
            
            # Referans değer
            text_total = Decimal(str(reference_total))
            
            # Reconcile
            reconciled = reconcile_amount(text_total, vision_total)
            
            logger.info(f"[{trace_id}] Cross-validation: {reference_source}={text_total}, vision={vision_total}, result={reconciled}")
            
            if reconciled["flag"]:
                debug_meta.warnings.append(f"Cross-validation: {reconciled['flag']} ({reference_source}={text_total}, vision={vision_total})")
            
            # Eğer referans değer daha güvenilirse, extraction'ı güncelle
            if reconciled["final"] and reconciled["source"] in ["text_confirmed", "text_with_rounding", "text_only", "text_override"]:
                if extraction.invoice_total_with_vat_tl.value != float(reconciled["final"]):
                    old_value = extraction.invoice_total_with_vat_tl.value
                    extraction.invoice_total_with_vat_tl.value = float(reconciled["final"])
                    extraction.invoice_total_with_vat_tl.confidence = reconciled["confidence"]
                    extraction.invoice_total_with_vat_tl.evidence = f"[CROSS-VALIDATED: {reference_source}]"
                    logger.info(f"[{trace_id}] invoice_total updated: {old_value} → {reconciled['final']} (source: {reference_source})")
                    debug_meta.warnings.append(f"Fatura tutarı düzeltildi: {old_value} → {reconciled['final']} ({reference_source})")
        
        # ═══════════════════════════════════════════════════════════════════════
        # KATMAN 4.5: ROI Multi-field → Extraction Update
        # ═══════════════════════════════════════════════════════════════════════
        # ROI'den gelen diğer alanları da extraction'a ekle (Vision'dan daha güvenilir)
        if roi_multi_fields:
            try:
                from .models import FieldValue, RawBreakdown
                
                # raw_breakdown yoksa oluştur
                if not extraction.raw_breakdown:
                    extraction.raw_breakdown = RawBreakdown()
                    logger.info(f"[{trace_id}] raw_breakdown created for ROI values")
                
                # KDV (vat_tl)
                if roi_multi_fields.vat_amount and roi_multi_fields.vat_amount_confidence >= 0.7:
                    old_vat = None
                    if extraction.raw_breakdown.vat_tl and extraction.raw_breakdown.vat_tl.value:
                        old_vat = extraction.raw_breakdown.vat_tl.value
                    extraction.raw_breakdown.vat_tl = FieldValue(
                        value=roi_multi_fields.vat_amount,
                        confidence=roi_multi_fields.vat_amount_confidence,
                        evidence=f"[ROI: {roi_multi_fields.source_region}]"
                    )
                    logger.info(f"[{trace_id}] vat_tl updated from ROI: {old_vat} → {roi_multi_fields.vat_amount}")
                    debug_meta.warnings.append(f"KDV ROI'den alındı: {roi_multi_fields.vat_amount:.2f} TL")
                
                # Enerji Bedeli (energy_total_tl)
                if roi_multi_fields.energy_total and roi_multi_fields.energy_total_confidence >= 0.7:
                    old_energy = None
                    if extraction.raw_breakdown.energy_total_tl and extraction.raw_breakdown.energy_total_tl.value:
                        old_energy = extraction.raw_breakdown.energy_total_tl.value
                    extraction.raw_breakdown.energy_total_tl = FieldValue(
                        value=roi_multi_fields.energy_total,
                        confidence=roi_multi_fields.energy_total_confidence,
                        evidence=f"[ROI: {roi_multi_fields.source_region}]"
                    )
                    logger.info(f"[{trace_id}] energy_total_tl updated from ROI: {old_energy} → {roi_multi_fields.energy_total}")
                    debug_meta.warnings.append(f"Enerji Bedeli ROI'den alındı: {roi_multi_fields.energy_total:.2f} TL")
                
                # Dağıtım Bedeli (distribution_total_tl)
                if roi_multi_fields.distribution_total and roi_multi_fields.distribution_total_confidence >= 0.7:
                    old_dist = None
                    if extraction.raw_breakdown.distribution_total_tl and extraction.raw_breakdown.distribution_total_tl.value:
                        old_dist = extraction.raw_breakdown.distribution_total_tl.value
                    extraction.raw_breakdown.distribution_total_tl = FieldValue(
                        value=roi_multi_fields.distribution_total,
                        confidence=roi_multi_fields.distribution_total_confidence,
                        evidence=f"[ROI: {roi_multi_fields.source_region}]"
                    )
                    logger.info(f"[{trace_id}] distribution_total_tl updated from ROI: {old_dist} → {roi_multi_fields.distribution_total}")
                    debug_meta.warnings.append(f"Dağıtım Bedeli ROI'den alındı: {roi_multi_fields.distribution_total:.2f} TL")
                
                # Tüketim (consumption_kwh) - sadece Vision'dan gelen değer yoksa veya düşük confidence ise
                if roi_multi_fields.consumption_kwh and roi_multi_fields.consumption_kwh_confidence >= 0.7:
                    vision_consumption_conf = 0
                    if extraction.consumption_kwh and extraction.consumption_kwh.confidence:
                        vision_consumption_conf = extraction.consumption_kwh.confidence
                    if vision_consumption_conf < 0.8:
                        old_kwh = None
                        if extraction.consumption_kwh and extraction.consumption_kwh.value:
                            old_kwh = extraction.consumption_kwh.value
                        extraction.consumption_kwh = FieldValue(
                            value=roi_multi_fields.consumption_kwh,
                            confidence=roi_multi_fields.consumption_kwh_confidence,
                            evidence=f"[ROI: {roi_multi_fields.source_region}]"
                        )
                        logger.info(f"[{trace_id}] consumption_kwh updated from ROI: {old_kwh} → {roi_multi_fields.consumption_kwh}")
                        debug_meta.warnings.append(f"Tüketim ROI'den alındı: {roi_multi_fields.consumption_kwh:.2f} kWh")
            except Exception as e:
                logger.error(f"[{trace_id}] ROI multi-field update failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
        
        # Hesaplama (eğer hazırsa)
        calculation = None
        calculation_error = None
        if validation.is_ready_for_pricing:
            params = OfferParams(
                weighted_ptf_tl_per_mwh=weighted_ptf_tl_per_mwh,
                yekdem_tl_per_mwh=yekdem_tl_per_mwh,
                agreement_multiplier=agreement_multiplier,
                use_reference_prices=use_reference_prices
            )
            try:
                calculation = calculate_offer(extraction, params, db=db)
                
                # Debug meta'yı calculation'dan doldur
                debug_meta.pricing_period = calculation.meta_pricing_period
                debug_meta.pricing_source = calculation.meta_pricing_source
                debug_meta.ptf_tl_per_mwh = calculation.meta_ptf_tl_per_mwh
                debug_meta.yekdem_tl_per_mwh = calculation.meta_yekdem_tl_per_mwh
                debug_meta.epdk_tariff_key = calculation.meta_distribution_tariff_key
                debug_meta.distribution_unit_price_tl_per_kwh = calculation.offer_distribution_unit_tl_per_kwh
                debug_meta.distribution_source = calculation.meta_distribution_source
                debug_meta.consumption_kwh = calculation.meta_consumption_kwh
                debug_meta.energy_amount_tl = calculation.offer_energy_tl
                debug_meta.distribution_amount_tl = calculation.offer_distribution_tl
                debug_meta.btv_amount_tl = calculation.offer_btv_tl
                debug_meta.kdv_amount_tl = calculation.offer_vat_tl
                debug_meta.total_amount_tl = calculation.offer_total_with_vat_tl
                
                # Mismatch warning
                if calculation.meta_distribution_mismatch_warning:
                    debug_meta.warnings.append(calculation.meta_distribution_mismatch_warning)
                    
            except CalculationError as e:
                calculation_error = str(e)
                debug_meta.errors.append(str(e))
                logger.error(f"[{trace_id}] Calculation error: {e}")
        else:
            # Validation başarısız
            for field in validation.missing_fields:
                debug_meta.errors.append(f"Eksik alan: {field}")
            for err in validation.errors:
                if isinstance(err, dict):
                    debug_meta.errors.append(err.get("message", str(err)))
                else:
                    debug_meta.errors.append(str(err))
        
        # Quality Score hesapla (Sprint 3)
        from .incident_service import calculate_quality_score, create_incidents_from_quality, generate_invoice_fingerprint
        
        quality = calculate_quality_score(
            extraction=extraction.model_dump(),
            validation=validation.model_dump(),
            calculation=calculation.model_dump() if calculation else None,
            calculation_error=calculation_error,
            debug_meta=debug_meta.model_dump()
        )
        
        # S1/S2 severity için incident oluştur (dedupe destekli - Sprint 4)
        if quality.flags:
            try:
                # Invoice fingerprint üret (dedupe için)
                invoice_fingerprint = generate_invoice_fingerprint(
                    supplier=extraction.vendor,
                    invoice_no=extraction.invoice_no.value if extraction.invoice_no else "",
                    period=extraction.invoice_period,
                    consumption_kwh=extraction.consumption_kwh.value if extraction.consumption_kwh else 0,
                    total_amount=extraction.invoice_total_with_vat_tl.value if extraction.invoice_total_with_vat_tl else 0
                )
                
                incident_ids = create_incidents_from_quality(
                    db=db,
                    trace_id=trace_id,
                    quality=quality,
                    tenant_id="default",
                    invoice_id=None,  # TODO: invoice_id varsa ekle
                    # Dedupe parametreleri (Sprint 4)
                    period=extraction.invoice_period or "",
                    invoice_fingerprint=invoice_fingerprint
                )
                if incident_ids:
                    logger.warning(f"[{trace_id}] Created/updated {len(incident_ids)} incidents for quality flags")
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to create incidents: {e}")
        
        logger.info(f"[{trace_id}] full-process completed: calculation={'OK' if calculation else 'FAILED'}, quality={quality.score}/{quality.grade}")
        
        return {
            "extraction": extraction.model_dump(),
            "validation": validation.model_dump(),
            "calculation": calculation.model_dump() if calculation else None,
            "calculation_error": calculation_error,
            "quality_score": {
                "score": quality.score,
                "grade": quality.grade,
                "flags": quality.flags,
                "flag_details": quality.flag_details
            },
            "debug_meta": debug_meta.model_dump() if debug else {"trace_id": trace_id},
            "meta": {
                "trace_id": trace_id,
                "fast_mode": fast_mode,
                "model": settings.openai_model_fast if fast_mode else settings.openai_model_accurate
            }
        }
    except ExtractionError as e:
        logger.error(f"[{trace_id}] Extraction error: {e}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "extraction_error",
                "message": f"OpenAI API hatası: {str(e)}",
                "retry": True,
                "trace_id": trace_id
            }
        )
    except Exception as e:
        logger.exception("Unexpected error during full process")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": f"Beklenmeyen hata: {str(e)}"
            }
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Customer Management Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/customers", response_model=dict)
async def create_customer(
    name: str,
    company: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    notes: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Yeni müşteri oluştur"""
    customer = Customer(
        name=name,
        company=company,
        email=email,
        phone=phone,
        address=address,
        notes=notes
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    
    return {
        "id": customer.id,
        "name": customer.name,
        "company": customer.company,
        "email": customer.email,
        "phone": customer.phone,
        "created_at": customer.created_at.isoformat()
    }


@app.get("/customers", response_model=List[dict])
async def list_customers(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Müşteri listesi"""
    query = db.query(Customer)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Customer.name.ilike(search_term)) |
            (Customer.company.ilike(search_term)) |
            (Customer.email.ilike(search_term))
        )
    
    customers = query.order_by(Customer.created_at.desc()).offset(skip).limit(limit).all()
    
    return [
        {
            "id": c.id,
            "name": c.name,
            "company": c.company,
            "email": c.email,
            "phone": c.phone,
            "offer_count": len(c.offers),
            "created_at": c.created_at.isoformat()
        }
        for c in customers
    ]


@app.get("/customers/{customer_id}", response_model=dict)
async def get_customer(customer_id: int, db: Session = Depends(get_db)):
    """Müşteri detayı"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Müşteri bulunamadı")
    
    return {
        "id": customer.id,
        "name": customer.name,
        "company": customer.company,
        "email": customer.email,
        "phone": customer.phone,
        "address": customer.address,
        "notes": customer.notes,
        "created_at": customer.created_at.isoformat(),
        "updated_at": customer.updated_at.isoformat(),
        "offers": [
            {
                "id": o.id,
                "invoice_period": o.invoice_period,
                "savings_amount": o.savings_amount,
                "savings_ratio": o.savings_ratio,
                "status": o.status,
                "created_at": o.created_at.isoformat()
            }
            for o in customer.offers
        ]
    }


@app.put("/customers/{customer_id}", response_model=dict)
async def update_customer(
    customer_id: int,
    name: Optional[str] = None,
    company: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    notes: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Müşteri güncelle"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Müşteri bulunamadı")
    
    if name is not None:
        customer.name = name
    if company is not None:
        customer.company = company
    if email is not None:
        customer.email = email
    if phone is not None:
        customer.phone = phone
    if address is not None:
        customer.address = address
    if notes is not None:
        customer.notes = notes
    
    db.commit()
    db.refresh(customer)
    
    return {"status": "ok", "message": "Müşteri güncellendi"}


@app.delete("/customers/{customer_id}")
async def delete_customer(customer_id: int, db: Session = Depends(get_db)):
    """Müşteri sil"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Müşteri bulunamadı")
    
    db.delete(customer)
    db.commit()
    
    return {"status": "ok", "message": "Müşteri silindi"}


# ═══════════════════════════════════════════════════════════════════════════════
# Offer Archive Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/offers", response_model=dict)
async def create_offer(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Teklifi kaydet ve arşivle"""
    offer = Offer(
        customer_id=customer_id,
        vendor=extraction.vendor,
        invoice_period=extraction.invoice_period,
        consumption_kwh=extraction.consumption_kwh.value or 0,
        current_unit_price=extraction.current_active_unit_price_tl_per_kwh.value or 0,
        distribution_unit_price=extraction.distribution_unit_price_tl_per_kwh.value,
        demand_qty=extraction.demand_qty.value,
        demand_unit_price=extraction.demand_unit_price_tl_per_unit.value,
        weighted_ptf=params.weighted_ptf_tl_per_mwh,
        yekdem=params.yekdem_tl_per_mwh,
        agreement_multiplier=params.agreement_multiplier,
        current_total=calculation.current_total_with_vat_tl,
        offer_total=calculation.offer_total_with_vat_tl,
        savings_amount=calculation.difference_incl_vat_tl,
        savings_ratio=calculation.savings_ratio,
        calculation_result=calculation.model_dump(),
        extraction_result=extraction.model_dump(),
        status="draft"
    )
    
    db.add(offer)
    db.commit()
    db.refresh(offer)
    
    return {
        "id": offer.id,
        "savings_amount": offer.savings_amount,
        "savings_ratio": offer.savings_ratio,
        "status": offer.status,
        "created_at": offer.created_at.isoformat()
    }


@app.get("/offers", response_model=List[dict])
async def list_offers(
    customer_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Teklif listesi"""
    query = db.query(Offer)
    
    if customer_id:
        query = query.filter(Offer.customer_id == customer_id)
    if status:
        query = query.filter(Offer.status == status)
    
    offers = query.order_by(Offer.created_at.desc()).offset(skip).limit(limit).all()
    
    return [
        {
            "id": o.id,
            "customer_id": o.customer_id,
            "customer_name": o.customer.name if o.customer else None,
            "vendor": o.vendor,
            "invoice_period": o.invoice_period,
            "consumption_kwh": o.consumption_kwh,
            "current_total": o.current_total,
            "offer_total": o.offer_total,
            "savings_amount": o.savings_amount,
            "savings_ratio": o.savings_ratio,
            "status": o.status,
            "created_at": o.created_at.isoformat()
        }
        for o in offers
    ]


@app.get("/offers/{offer_id}", response_model=dict)
async def get_offer(offer_id: int, db: Session = Depends(get_db)):
    """Teklif detayı"""
    offer = db.query(Offer).filter(Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    
    return {
        "id": offer.id,
        "customer_id": offer.customer_id,
        "customer": {
            "id": offer.customer.id,
            "name": offer.customer.name,
            "company": offer.customer.company
        } if offer.customer else None,
        "vendor": offer.vendor,
        "invoice_period": offer.invoice_period,
        "consumption_kwh": offer.consumption_kwh,
        "current_unit_price": offer.current_unit_price,
        "distribution_unit_price": offer.distribution_unit_price,
        "demand_qty": offer.demand_qty,
        "demand_unit_price": offer.demand_unit_price,
        "weighted_ptf": offer.weighted_ptf,
        "yekdem": offer.yekdem,
        "agreement_multiplier": offer.agreement_multiplier,
        "current_total": offer.current_total,
        "offer_total": offer.offer_total,
        "savings_amount": offer.savings_amount,
        "savings_ratio": offer.savings_ratio,
        "calculation_result": offer.calculation_result,
        "extraction_result": offer.extraction_result,
        "status": offer.status,
        "pdf_ref": offer.pdf_ref,
        "created_at": offer.created_at.isoformat()
    }


@app.put("/offers/{offer_id}/status")
async def update_offer_status(
    offer_id: int,
    status: str = Query(..., regex="^(draft|sent|viewed|accepted|rejected|contracting|completed|expired)$"),
    notes: Optional[str] = Query(default=None, description="Durum değişikliği notu"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Teklif durumunu güncelle.
    
    Lifecycle: draft → sent → viewed → accepted → contracting → completed
                                    ↘ rejected
                                    ↘ expired
    
    Webhook: Durum değişikliğinde ilgili webhook'lara event gönderilir.
    Audit: Tüm durum değişiklikleri loglanır.
    """
    from .models import OfferStatus, AuditAction
    from .services.audit import log_action
    from .services.webhook import send_webhook
    
    offer = db.query(Offer).filter(Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    
    old_status = offer.status
    
    # Validate status transition
    valid_transitions = {
        "draft": ["sent", "expired"],
        "sent": ["viewed", "accepted", "rejected", "expired"],
        "viewed": ["accepted", "rejected", "expired"],
        "accepted": ["contracting", "rejected"],
        "contracting": ["completed", "rejected"],
        "rejected": [],  # Terminal state
        "completed": [],  # Terminal state
        "expired": [],  # Terminal state
    }
    
    if old_status and status not in valid_transitions.get(old_status, [status]):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_transition",
                "message": f"'{old_status}' → '{status}' geçişi geçersiz",
                "valid_transitions": valid_transitions.get(old_status, [])
            }
        )
    
    # Update status
    offer.status = status
    db.commit()
    
    # Audit log
    log_action(
        db,
        AuditAction.OFFER_STATUS_CHANGED,
        tenant_id=offer.tenant_id,
        actor_type="api_key",
        target_type="offer",
        target_id=str(offer.id),
        details={
            "old_status": old_status,
            "new_status": status,
            "notes": notes
        }
    )
    
    # Send webhook
    try:
        webhook_results = await send_webhook(
            db,
            tenant_id=offer.tenant_id,
            event_type=f"offer.{status}",
            payload={
                "offer_id": offer.id,
                "old_status": old_status,
                "new_status": status,
                "customer_id": offer.customer_id,
                "savings_amount": offer.savings_amount,
                "notes": notes
            }
        )
    except Exception as e:
        logger.error(f"Webhook send failed: {e}")
        webhook_results = []
    
    return {
        "status": "ok",
        "message": f"Teklif durumu '{status}' olarak güncellendi",
        "offer_id": offer.id,
        "old_status": old_status,
        "new_status": status,
        "webhooks_triggered": len(webhook_results)
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PDF/HTML Generation Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/offers/{offer_id}/generate-pdf")
async def generate_pdf_for_offer(
    offer_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Kayıtlı teklif için PDF oluştur ve storage'a kaydet.
    
    PDF storage backend'e kaydedilir:
    - Local: ./storage/offers/{offer_id}/offer.pdf
    - S3: s3://bucket/offers/{offer_id}/offer.pdf
    
    Returns:
        {offer_id, pdf_ref, message, download_url}
    """
    offer = db.query(Offer).filter(Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    
    if not offer.extraction_result or not offer.calculation_result:
        raise HTTPException(
            status_code=400,
            detail="Teklif verisi eksik (extraction_result veya calculation_result)"
        )
    
    # Reconstruct extraction and calculation from stored JSON
    extraction = InvoiceExtraction(**offer.extraction_result)
    calculation = CalculationResult(**offer.calculation_result)
    params = OfferParams(
        weighted_ptf_tl_per_mwh=offer.weighted_ptf,
        yekdem_tl_per_mwh=offer.yekdem,
        agreement_multiplier=offer.agreement_multiplier
    )
    
    customer_name = offer.customer.name if offer.customer else None
    customer_company = offer.customer.company if offer.customer else None
    
    try:
        # Generate and store PDF (uses storage backend)
        from .pdf_generator import generate_and_store_offer_pdf
        
        pdf_ref = generate_and_store_offer_pdf(
            extraction=extraction,
            calculation=calculation,
            params=params,
            offer_id=offer.id,
            customer_name=customer_name,
            customer_company=customer_company
        )
        
        # Update offer with PDF ref
        offer.pdf_ref = pdf_ref
        db.commit()
        
        logger.info(f"PDF generated and stored: {pdf_ref}")
        
        return {
            "offer_id": offer.id,
            "pdf_ref": pdf_ref,
            "message": "PDF başarıyla oluşturuldu",
            "download_url": f"/offers/{offer.id}/download"
        }
    except Exception as e:
        logger.exception(f"PDF generation failed for offer {offer_id}")
        raise HTTPException(status_code=500, detail=f"PDF oluşturma hatası: {str(e)}")


@app.get("/offers/{offer_id}/download")
async def download_offer_pdf(
    offer_id: int,
    expires: int = Query(default=300, ge=60, le=3600, description="Presigned URL geçerlilik süresi (saniye)"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Teklif PDF'ini indir.
    
    Args:
        expires: Presigned URL geçerlilik süresi (60-3600 saniye, default 300)
    
    Returns:
        - S3 storage: JSON with presigned URL
        - Local storage: Dosya stream (FileResponse)
    
    Note: PDF önce generate-pdf ile oluşturulmalı.
    """
    offer = db.query(Offer).filter(Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    
    if not offer.pdf_ref:
        raise HTTPException(
            status_code=404, 
            detail="PDF henüz oluşturulmamış. Önce POST /offers/{id}/generate-pdf çağırın."
        )
    
    ref = offer.pdf_ref
    filename = f"teklif_{offer.id}.pdf"
    content_type = "application/pdf"
    
    # Get storage backend
    from .services.storage import get_storage
    from .services.storage_local import LocalStorage
    storage = get_storage()
    
    # 1) S3 ise presigned URL dön
    presigned_url = storage.get_presigned_url(ref, expires_in=expires)
    if presigned_url:
        from fastapi.responses import JSONResponse
        return JSONResponse({
            "type": "presigned_url",
            "url": presigned_url,
            "expires_seconds": expires,
            "filename": filename,
            "content_type": content_type
        })
    
    # 2) Local ise dosyayı stream et
    if isinstance(storage, LocalStorage):
        try:
            local_path = storage.resolve_local_path(ref)
        except ValueError as e:
            logger.error(f"Path traversal attempt: {ref}")
            raise HTTPException(status_code=400, detail=str(e))
        
        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail="PDF dosyası bulunamadı")
        
        return FileResponse(
            path=local_path,
            filename=filename,
            media_type=content_type
        )
    
    # 3) Fallback: local path olarak dene (eski PDF'ler için)
    if os.path.exists(ref):
        return FileResponse(
            path=ref,
            filename=filename,
            media_type=content_type
        )
    
    raise HTTPException(status_code=404, detail="PDF dosyası bulunamadı")


@app.post("/offers/{offer_id}/generate-html", response_class=HTMLResponse)
async def generate_html_for_offer(offer_id: int, db: Session = Depends(get_db)):
    """Kayıtlı teklif için HTML oluştur"""
    offer = db.query(Offer).filter(Offer.id == offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail="Teklif bulunamadı")
    
    extraction = InvoiceExtraction(**offer.extraction_result)
    calculation = CalculationResult(**offer.calculation_result)
    params = OfferParams(
        weighted_ptf_tl_per_mwh=offer.weighted_ptf,
        yekdem_tl_per_mwh=offer.yekdem,
        agreement_multiplier=offer.agreement_multiplier
    )
    
    customer_name = offer.customer.name if offer.customer else None
    customer_company = offer.customer.company if offer.customer else None
    
    html_content = generate_offer_html(
        extraction, calculation, params,
        customer_name=customer_name,
        customer_company=customer_company,
        offer_id=offer.id
    )
    
    return HTMLResponse(content=html_content)


@app.post("/generate-pdf-direct")
async def generate_pdf_direct(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None
):
    """Kaydetmeden direkt PDF oluştur"""
    try:
        pdf_path = generate_offer_pdf(
            extraction, calculation, params,
            customer_name=customer_name,
            customer_company=customer_company
        )
        
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename="teklif.pdf"
        )
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        raise HTTPException(status_code=500, detail=f"PDF oluşturma hatası: {str(e)}")


@app.post("/generate-pdf-simple")
def generate_pdf_simple(
    weighted_ptf_tl_per_mwh: float = Form(2974.1),
    yekdem_tl_per_mwh: float = Form(364.0),
    agreement_multiplier: float = Form(1.01),
    consumption_kwh: float = Form(...),
    current_unit_price: float = Form(0),
    distribution_unit_price: float = Form(0),
    invoice_total: float = Form(0),
    current_energy_tl: float = Form(...),
    current_distribution_tl: float = Form(0),
    current_btv_tl: float = Form(0),
    current_vat_tl: float = Form(0),
    current_vat_matrah_tl: float = Form(0),
    current_total_with_vat_tl: float = Form(0),
    offer_energy_tl: float = Form(...),
    offer_distribution_tl: float = Form(0),
    offer_btv_tl: float = Form(0),
    offer_vat_tl: float = Form(0),
    offer_vat_matrah_tl: float = Form(0),
    offer_total: float = Form(...),
    difference_incl_vat_tl: float = Form(0),
    savings_ratio: float = Form(...),
    vendor: str = Form("unknown"),
    invoice_period: str = Form(""),
    customer_name: Optional[str] = Form(None),
    tariff_group: str = Form("Sanayi"),
    vat_rate: float = Form(0.20),  # KDV oranı: 0.20 = %20, 0.10 = %10
    contact_person: Optional[str] = Form(None),  # Yetkili kişi
    offer_date: Optional[str] = Form(None),  # Teklif tarihi (YYYY-MM-DD)
    offer_validity_days: int = Form(15),  # Teklif geçerlilik süresi (gün)
):
    """Basit parametrelerle PDF oluştur - Frontend için"""
    logger.info(f"PDF Generation Request:")
    logger.info(f"  customer_name: '{customer_name}'")
    logger.info(f"  contact_person: '{contact_person}'")
    logger.info(f"  offer_energy_tl: {offer_energy_tl}")
    logger.info(f"  offer_vat_matrah_tl: {offer_vat_matrah_tl}")
    logger.info(f"  offer_total: {offer_total}")
    logger.info(f"  current_energy_tl: {current_energy_tl}")
    logger.info(f"  vat_rate: {vat_rate}")
    
    try:
        # Mevcut toplam hesapla (eğer gönderilmediyse)
        if current_total_with_vat_tl == 0 and invoice_total > 0:
            current_total_with_vat_tl = invoice_total
        
        # KDV matrahı hesapla (eğer gönderilmediyse)
        if current_vat_matrah_tl == 0:
            current_vat_matrah_tl = current_energy_tl + current_distribution_tl + current_btv_tl
        if offer_vat_matrah_tl == 0:
            offer_vat_matrah_tl = offer_energy_tl + offer_distribution_tl + offer_btv_tl
        
        # Fark hesapla (eğer gönderilmediyse)
        if difference_incl_vat_tl == 0:
            difference_incl_vat_tl = current_total_with_vat_tl - offer_total
        
        # Basit extraction oluştur
        extraction = InvoiceExtraction(
            vendor=vendor,
            invoice_period=invoice_period,
            consumption_kwh=FieldValue(value=consumption_kwh, confidence=1.0),
            current_active_unit_price_tl_per_kwh=FieldValue(value=current_unit_price, confidence=1.0),
            distribution_unit_price_tl_per_kwh=FieldValue(value=distribution_unit_price, confidence=1.0),
            invoice_total_with_vat_tl=FieldValue(value=current_total_with_vat_tl, confidence=1.0),
            demand_qty=FieldValue(value=0, confidence=1.0),
            demand_unit_price_tl_per_unit=FieldValue(value=0, confidence=1.0),
            meta=InvoiceMeta(tariff_group_guess=tariff_group),
        )
        
        # Params
        params = OfferParams(
            weighted_ptf_tl_per_mwh=weighted_ptf_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            agreement_multiplier=agreement_multiplier,
        )
        
        # kWh başı hesaplamalar
        current_total_tl_per_kwh = current_total_with_vat_tl / consumption_kwh if consumption_kwh > 0 else 0
        offer_total_tl_per_kwh = offer_total / consumption_kwh if consumption_kwh > 0 else 0
        saving_tl_per_kwh = current_total_tl_per_kwh - offer_total_tl_per_kwh
        annual_saving_tl = difference_incl_vat_tl * 12
        
        # Birim fiyat
        offer_unit_price = (weighted_ptf_tl_per_mwh / 1000 + yekdem_tl_per_mwh / 1000) * agreement_multiplier
        unit_price_savings_ratio = (current_unit_price - offer_unit_price) / current_unit_price if current_unit_price > 0 else 0
        
        calculation = CalculationResult(
            current_energy_tl=current_energy_tl,
            current_distribution_tl=current_distribution_tl,
            current_demand_tl=0,
            current_btv_tl=current_btv_tl,
            current_vat_matrah_tl=current_vat_matrah_tl,
            current_vat_tl=current_vat_tl,
            current_total_with_vat_tl=current_total_with_vat_tl,
            current_energy_unit_tl_per_kwh=current_unit_price,
            current_distribution_unit_tl_per_kwh=distribution_unit_price,
            offer_ptf_tl=offer_energy_tl / agreement_multiplier if agreement_multiplier else offer_energy_tl,
            offer_yekdem_tl=0,
            offer_energy_tl=offer_energy_tl,
            offer_distribution_tl=offer_distribution_tl,
            offer_demand_tl=0,
            offer_btv_tl=offer_btv_tl,
            offer_vat_matrah_tl=offer_vat_matrah_tl,
            offer_vat_tl=offer_vat_tl,
            offer_total_with_vat_tl=offer_total,
            offer_energy_unit_tl_per_kwh=offer_unit_price,
            offer_distribution_unit_tl_per_kwh=distribution_unit_price,
            difference_excl_vat_tl=current_vat_matrah_tl - offer_vat_matrah_tl,
            difference_incl_vat_tl=difference_incl_vat_tl,
            savings_ratio=savings_ratio,
            unit_price_savings_ratio=unit_price_savings_ratio,
            current_total_tl_per_kwh=current_total_tl_per_kwh,
            offer_total_tl_per_kwh=offer_total_tl_per_kwh,
            saving_tl_per_kwh=saving_tl_per_kwh,
            annual_saving_tl=annual_saving_tl,
            meta_consumption_kwh=consumption_kwh,
            meta_vat_rate=vat_rate,
        )
        
        pdf_path = generate_offer_pdf(
            extraction, calculation, params,
            customer_name=customer_name,
            contact_person=contact_person,
            offer_date=offer_date,
            offer_validity_days=offer_validity_days,
        )
        
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"teklif_{invoice_period or 'fatura'}.pdf"
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"PDF generation error: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=f"PDF hatası: {str(e)} - {tb[:500]}")


@app.post("/generate-html-direct", response_class=HTMLResponse)
async def generate_html_direct(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None
):
    """Kaydetmeden direkt HTML oluştur"""
    html_content = generate_offer_html(
        extraction, calculation, params,
        customer_name=customer_name,
        customer_company=customer_company
    )
    
    return HTMLResponse(content=html_content)


# ═══════════════════════════════════════════════════════════════════════════════
# Statistics Endpoint
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/stats")
async def get_statistics(db: Session = Depends(get_db)):
    """Genel istatistikler"""
    from sqlalchemy import func
    
    total_customers = db.query(func.count(Customer.id)).scalar()
    total_offers = db.query(func.count(Offer.id)).scalar()
    
    total_savings = db.query(func.sum(Offer.savings_amount)).filter(
        Offer.status == "accepted"
    ).scalar() or 0
    
    offers_by_status = db.query(
        Offer.status,
        func.count(Offer.id)
    ).group_by(Offer.status).all()
    
    return {
        "total_customers": total_customers,
        "total_offers": total_offers,
        "total_savings_accepted": round(total_savings, 2),
        "offers_by_status": {status: count for status, count in offers_by_status}
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Field Patching Endpoint (Eksik alan düzeltme)
# ═══════════════════════════════════════════════════════════════════════════════

@app.patch("/extraction/patch-fields")
async def patch_extraction_fields(
    extraction: InvoiceExtraction,
    patches: dict
):
    """
    Extraction JSON'ına kullanıcı düzeltmelerini uygula ve tekrar validate et.
    
    Kullanım: UI'da eksik alan soruldu, kullanıcı cevapladı, bu endpoint'e gönder.
    
    Body örneği:
    {
        "extraction": { ... mevcut extraction ... },
        "patches": {
            "consumption_kwh": 168330,
            "current_active_unit_price_tl_per_kwh": 3.87927
        }
    }
    
    Returns: Güncellenmiş extraction + yeni validation
    """
    # Extraction'ı dict'e çevir
    extraction_dict = extraction.model_dump()
    
    # Patch'leri uygula
    for field_name, new_value in patches.items():
        if field_name in extraction_dict:
            # FieldValue formatında güncelle
            if isinstance(extraction_dict[field_name], dict) and "value" in extraction_dict[field_name]:
                extraction_dict[field_name]["value"] = new_value
                extraction_dict[field_name]["confidence"] = 1.0  # Kullanıcı girişi = %100 güven
                extraction_dict[field_name]["evidence"] = "Kullanıcı tarafından manuel girildi"
    
    # Güncellenmiş extraction'ı oluştur
    updated_extraction = InvoiceExtraction(**extraction_dict)
    
    # Tekrar validate et
    validation = validate_extraction(updated_extraction)
    
    return {
        "extraction": updated_extraction.model_dump(),
        "validation": validation.model_dump(),
        "patched_fields": list(patches.keys())
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Suggested Fix Application Endpoint
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/extraction/apply-suggested-fixes")
async def apply_suggested_fixes(
    extraction: InvoiceExtraction,
    validation: ValidationResult
):
    """
    Validation'daki suggested_fixes'ları otomatik uygula.
    
    Kullanım: UI'da "Önerileri Uygula" butonu tıklandığında.
    """
    if not validation.suggested_fixes:
        return {
            "extraction": extraction.model_dump(),
            "validation": validation.model_dump(),
            "applied_fixes": []
        }
    
    # Extraction'ı dict'e çevir
    extraction_dict = extraction.model_dump()
    applied_fixes = []
    
    # Suggested fix'leri uygula
    for fix in validation.suggested_fixes:
        field_name = fix.field_name
        if field_name in extraction_dict:
            if isinstance(extraction_dict[field_name], dict) and "value" in extraction_dict[field_name]:
                extraction_dict[field_name]["value"] = fix.suggested_value
                extraction_dict[field_name]["confidence"] = fix.confidence
                extraction_dict[field_name]["evidence"] = f"Türetildi: {fix.basis}"
                applied_fixes.append({
                    "field": field_name,
                    "value": fix.suggested_value,
                    "basis": fix.basis
                })
    
    # Güncellenmiş extraction'ı oluştur
    updated_extraction = InvoiceExtraction(**extraction_dict)
    
    # Tekrar validate et
    new_validation = validate_extraction(updated_extraction)
    
    return {
        "extraction": updated_extraction.model_dump(),
        "validation": new_validation.model_dump(),
        "applied_fixes": applied_fixes
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Invoice Management Endpoints (Durum Takipli)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/invoices", response_model=dict)
async def upload_invoice(
    file: UploadFile = File(...),
    reuse: bool = Query(default=False, description="Aynı hash varsa mevcut invoice'ı döndür"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Depends(lambda x_tenant_id: __import__('app.services.tenant', fromlist=['get_tenant_id']).get_tenant_id(x_tenant_id))
):
    """
    Fatura yükle ve kaydet.
    - Görsel: EXIF fix + preprocessing → JPEG
    - PDF: Page1 render → preprocessing → JPEG
    Durum: UPLOADED olarak başlar.
    
    Args:
        reuse: True ise aynı hash'e sahip mevcut invoice döndürülür (cache)
    
    Storage: Local veya S3/MinIO (config'e göre)
    Multi-tenant: X-Tenant-Id header ile izolasyon
    """
    # Import tenant dependency properly
    from .services.tenant import get_tenant_id as _get_tenant
    
    content = await file.read()
    
    # Validate file
    validate_uploaded_file(file, content)
    
    # Check if same file already exists (by hash) within tenant
    file_hash = hashlib.sha256(content).hexdigest()
    existing = db.query(Invoice).filter(
        Invoice.file_hash == file_hash,
        Invoice.tenant_id == tenant_id
    ).first()
    
    if existing:
        if reuse:
            # Return existing invoice (cache hit)
            return {
                "id": existing.id,
                "tenant_id": existing.tenant_id,
                "source_filename": existing.source_filename,
                "status": existing.status.value,
                "storage_page1_ref": existing.storage_page1_ref,
                "message": "Bu dosya daha önce yüklenmiş (cache)",
                "is_duplicate": True,
                "reused": True
            }
        else:
            # Warn but create new (default behavior)
            logger.info(f"Duplicate file detected for tenant {tenant_id}, creating new record")
    
    # Get storage backend
    from .services.storage import get_storage
    storage = get_storage()
    
    # Generate unique invoice key (include tenant for S3 organization)
    import uuid
    invoice_key = str(uuid.uuid4())
    storage_prefix = f"{tenant_id}/invoices/{invoice_key}" if tenant_id != "default" else f"invoices/{invoice_key}"
    
    page1_ref = None
    original_ref = None
    final_content_type = file.content_type
    
    if file.content_type in ALLOWED_IMAGE_MIME_TYPES:
        # ═══════════════════════════════════════════════════════════════════
        # GÖRSEL: EXIF fix + preprocessing
        # ═══════════════════════════════════════════════════════════════════
        try:
            processed_bytes, processed_ct = preprocess_image_bytes(
                content,
                max_width=2000,
                jpeg_quality=85,
                output_format="JPEG"
            )
            final_content_type = processed_ct
            
            # Save to storage backend
            ext = "jpg" if processed_ct == "image/jpeg" else "png"
            original_ref = storage.put_bytes(
                key=f"{storage_prefix}/original.{ext}",
                data=processed_bytes,
                content_type=processed_ct
            )
            logger.info(f"Image preprocessed and saved: {original_ref}")
            
        except Exception as e:
            logger.error(f"Image preprocessing failed: {e}")
            # Fallback: save original
            ext = os.path.splitext(file.filename)[1].lower() or ".bin"
            original_ref = storage.put_bytes(
                key=f"{storage_prefix}/original{ext}",
                data=content,
                content_type=file.content_type
            )
            final_content_type = file.content_type
    
    elif file.content_type == ALLOWED_PDF_MIME_TYPE:
        # ═══════════════════════════════════════════════════════════════════
        # PDF: Save original → Render page1 → Preprocess
        # ═══════════════════════════════════════════════════════════════════
        
        # Save original PDF to storage
        original_ref = storage.put_bytes(
            key=f"{storage_prefix}/original.pdf",
            data=content,
            content_type="application/pdf"
        )
        final_content_type = "application/pdf"
        
        try:
            # PDF render için temp dosya kullan (pypdfium2 path istiyor)
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                pdf_path = os.path.join(td, "tmp.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(content)
                
                # Render page 1 to PNG
                page1_png_path = os.path.join(td, "p1.png")
                render_pdf_first_page(pdf_path, page1_png_path, scale=2.5)
                
                # Read rendered page
                with open(page1_png_path, "rb") as f:
                    page1_bytes = f.read()
            
            # Preprocess the rendered page
            processed_bytes, processed_ct = preprocess_image_bytes(
                page1_bytes,
                max_width=2200,
                jpeg_quality=88,
                output_format="JPEG"
            )
            
            # Save preprocessed page1 to storage
            page1_ref = storage.put_bytes(
                key=f"{storage_prefix}/page1.jpg",
                data=processed_bytes,
                content_type=processed_ct
            )
            
            logger.info(f"PDF page1 rendered and preprocessed: {page1_ref}")
            
        except Exception as e:
            logger.error(f"PDF render/preprocess failed: {e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "pdf_render_error",
                    "message": f"PDF işleme başarısız: {str(e)}"
                }
            )
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_file_type",
                "message": "Desteklenmeyen dosya formatı"
            }
        )
    
    # Create invoice record with ref fields and tenant
    invoice = Invoice(
        tenant_id=tenant_id,
        source_filename=file.filename,
        content_type=final_content_type,
        storage_original_ref=original_ref,
        storage_page1_ref=page1_ref,
        file_hash=file_hash,
        status=InvoiceStatus.UPLOADED
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    
    return {
        "id": invoice.id,
        "tenant_id": invoice.tenant_id,
        "source_filename": invoice.source_filename,
        "content_type": invoice.content_type,
        "storage_original_ref": invoice.storage_original_ref,
        "storage_page1_ref": invoice.storage_page1_ref,
        "status": invoice.status.value,
        "created_at": invoice.created_at.isoformat(),
        "is_duplicate": False,
        "preprocessed": True
    }


@app.post("/invoices/{invoice_id}/extract", response_model=dict)
async def extract_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Yüklenen faturayı analiz et.
    - PDF: Preprocessed page1 JPEG kullanılır
    - Görsel: Preprocessed JPEG kullanılır
    Durum: UPLOADED → EXTRACTED veya FAILED
    
    Storage: Local veya S3/MinIO (config'e göre)
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    # Get storage backend
    from .services.storage import get_storage
    storage = get_storage()
    
    # Hangi görseli kullanacağız?
    if invoice.content_type == "application/pdf":
        # PDF için preprocessed page1 JPEG
        if not invoice.storage_page1_ref:
            raise HTTPException(
                status_code=400,
                detail="PDF page1 görseli yok. Upload aşamasında işlem başarısız olmuş."
            )
        image_ref = invoice.storage_page1_ref
        mime_type = "image/jpeg"
    else:
        # Görsel için preprocessed dosya
        image_ref = invoice.storage_original_ref
        mime_type = invoice.content_type
    
    # Read image from storage backend
    try:
        content = storage.get_bytes(image_ref)
    except Exception as e:
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = f"Görsel dosyası okunamadı: {str(e)}"
        db.commit()
        raise HTTPException(status_code=404, detail=f"Görsel dosyası storage'da bulunamadı: {image_ref}")
    
    # Extract
    try:
        extraction = extract_invoice_data(content, mime_type)
        invoice.extraction_json = extraction.model_dump()
        invoice.vendor_guess = extraction.vendor
        invoice.invoice_period = extraction.invoice_period or None
        invoice.status = InvoiceStatus.EXTRACTED
        invoice.error_message = None
        db.commit()
        
        return {
            "id": invoice.id,
            "status": invoice.status.value,
            "extraction": extraction.model_dump()
        }
    except ExtractionError as e:
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail={
                "error": "extraction_error",
                "message": str(e),
                "retry": True
            }
        )
    except Exception as e:
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.commit()
        raise HTTPException(status_code=500, detail=f"Extraction hatası: {str(e)}")


@app.post("/invoices/{invoice_id}/validate", response_model=dict)
async def validate_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Extraction sonucunu doğrula.
    Durum: EXTRACTED → READY veya NEEDS_INPUT
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    if not invoice.extraction_json:
        raise HTTPException(status_code=400, detail="Önce extraction yapılmalı")
    
    extraction = InvoiceExtraction(**invoice.extraction_json)
    validation = validate_extraction(extraction)
    
    invoice.validation_json = validation.model_dump()
    invoice.status = InvoiceStatus.READY if validation.is_ready_for_pricing else InvoiceStatus.NEEDS_INPUT
    db.commit()
    
    return {
        "id": invoice.id,
        "status": invoice.status.value,
        "validation": validation.model_dump()
    }


@app.patch("/invoices/{invoice_id}/fields", response_model=dict)
async def patch_invoice_fields(
    invoice_id: str,
    patches: dict,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Fatura alanlarını manuel düzelt ve tekrar validate et.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    if not invoice.extraction_json:
        raise HTTPException(status_code=400, detail="Önce extraction yapılmalı")
    
    extraction_dict = invoice.extraction_json.copy()
    
    # Apply patches
    for field_name, new_value in patches.items():
        if field_name in extraction_dict:
            if isinstance(extraction_dict[field_name], dict) and "value" in extraction_dict[field_name]:
                extraction_dict[field_name]["value"] = float(new_value) if new_value is not None else None
                extraction_dict[field_name]["confidence"] = 1.0
                extraction_dict[field_name]["evidence"] = "manual_patch"
    
    # Update and validate
    extraction = InvoiceExtraction(**extraction_dict)
    validation = validate_extraction(extraction)
    
    invoice.extraction_json = extraction.model_dump()
    invoice.validation_json = validation.model_dump()
    invoice.status = InvoiceStatus.READY if validation.is_ready_for_pricing else InvoiceStatus.NEEDS_INPUT
    db.commit()
    
    return {
        "id": invoice.id,
        "status": invoice.status.value,
        "extraction": extraction.model_dump(),
        "validation": validation.model_dump(),
        "patched_fields": list(patches.keys())
    }


@app.get("/invoices/{invoice_id}", response_model=dict)
async def get_invoice(
    invoice_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """Fatura detayı"""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    return {
        "id": invoice.id,
        "source_filename": invoice.source_filename,
        "content_type": invoice.content_type,
        "storage_original_ref": invoice.storage_original_ref,
        "storage_page1_ref": invoice.storage_page1_ref,
        "vendor_guess": invoice.vendor_guess,
        "invoice_period": invoice.invoice_period,
        "status": invoice.status.value,
        "error_message": invoice.error_message,
        "extraction_json": invoice.extraction_json,
        "validation_json": invoice.validation_json,
        "created_at": invoice.created_at.isoformat(),
        "updated_at": invoice.updated_at.isoformat()
    }


@app.get("/invoices", response_model=List[dict])
async def list_invoices(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """Fatura listesi"""
    query = db.query(Invoice)
    
    if status:
        try:
            status_enum = InvoiceStatus(status)
            query = query.filter(Invoice.status == status_enum)
        except ValueError:
            pass
    
    invoices = query.order_by(Invoice.created_at.desc()).offset(skip).limit(limit).all()
    
    return [
        {
            "id": inv.id,
            "source_filename": inv.source_filename,
            "vendor_guess": inv.vendor_guess,
            "invoice_period": inv.invoice_period,
            "status": inv.status.value,
            "created_at": inv.created_at.isoformat()
        }
        for inv in invoices
    ]


@app.get("/invoices/{invoice_id}/download")
async def download_invoice_file(
    invoice_id: str,
    asset: str = Query(default="original", regex="^(original|page1)$", description="Hangi dosya: original veya page1"),
    expires: int = Query(default=300, ge=60, le=3600, description="Presigned URL geçerlilik süresi (saniye)"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Fatura dosyasını indir.
    
    Args:
        asset: "original" (orijinal dosya) veya "page1" (PDF'nin ilk sayfası)
        expires: Presigned URL geçerlilik süresi (60-3600 saniye, default 300)
    
    Returns:
        - S3 storage: JSON with presigned URL
        - Local storage: Dosya stream (FileResponse)
    
    Response (S3):
        {"type": "presigned_url", "url": "https://...", "expires_seconds": 300}
    
    Response (Local):
        Binary file stream
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    # Hangi ref'i kullanacağız?
    if asset == "original":
        ref = invoice.storage_original_ref
        filename = invoice.source_filename or "invoice"
        content_type = invoice.content_type
    else:
        ref = invoice.storage_page1_ref
        filename = f"{os.path.splitext(invoice.source_filename or 'invoice')[0]}_page1.jpg"
        content_type = "image/jpeg"
    
    if not ref:
        raise HTTPException(status_code=404, detail=f"{asset} dosyası bulunamadı")
    
    # Get storage backend
    from .services.storage import get_storage
    from .services.storage_local import LocalStorage
    storage = get_storage()
    
    # 1) S3 ise presigned URL dön
    presigned_url = storage.get_presigned_url(ref, expires_in=expires)
    if presigned_url:
        from fastapi.responses import JSONResponse
        return JSONResponse({
            "type": "presigned_url",
            "url": presigned_url,
            "expires_seconds": expires,
            "filename": filename,
            "content_type": content_type
        })
    
    # 2) Local ise dosyayı stream et
    if isinstance(storage, LocalStorage):
        try:
            local_path = storage.resolve_local_path(ref)
        except ValueError as e:
            logger.error(f"Path traversal attempt: {ref}")
            raise HTTPException(status_code=400, detail=str(e))
        
        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail="Dosya storage'da bulunamadı")
        
        return FileResponse(
            path=local_path,
            filename=filename,
            media_type=content_type
        )
    
    # 3) Fallback: beklenmeyen backend
    raise HTTPException(status_code=500, detail="Storage backend download not supported")


# ═══════════════════════════════════════════════════════════════════════════════
# Async Job Endpoints (Queue-Ready)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/invoices/{invoice_id}/process", response_model=dict)
async def process_invoice_async(
    invoice_id: str,
    force: bool = Query(default=False, description="FAILED invoice için zorla yeni job aç"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Faturayı async olarak işle (extract + validate).
    
    Args:
        force: True ise FAILED durumundaki invoice için yeni job açar
    
    Returns:
        202 Accepted + job_id
    
    UI akışı:
        1. POST /invoices/{id}/process → job_id al
        2. GET /jobs/{job_id} ile polling
        3. status=SUCCEEDED olunca invoice READY/NEEDS_INPUT
    
    Not: Aynı invoice için aktif job varsa yeni oluşturmaz, mevcut job'ı döndürür.
    """
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Fatura bulunamadı")
    
    # FAILED invoice için force=True gerekli (retry mekanizması)
    if invoice.status == InvoiceStatus.FAILED and not force:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invoice_failed",
                "message": "Bu fatura daha önce başarısız oldu. Tekrar denemek için force=true kullanın.",
                "hint": "POST /invoices/{id}/process?force=true"
            }
        )
    
    # Idempotent job oluştur
    job, created_new = enqueue_job_idempotent(
        db=db,
        invoice_id=invoice_id,
        job_type=JobType.EXTRACT_AND_VALIDATE
    )
    
    # Yeni job oluşturulduysa invoice'ı PROCESSING yap
    if created_new:
        invoice.status = InvoiceStatus.PROCESSING
        invoice.error_message = None  # Önceki hatayı temizle
        db.add(invoice)
        db.commit()
        logger.info(f"Job created: {job.id} for invoice {invoice_id}")
        
        # Redis varsa kuyruğa ekle (opsiyonel)
        try:
            from .rq_adapter import enqueue_to_redis, is_redis_enabled
            if is_redis_enabled():
                enqueue_to_redis(job.id)
                logger.info(f"Job {job.id} pushed to Redis queue")
        except ImportError:
            pass  # Redis modülleri yok, DB polling kullanılacak
    else:
        logger.info(f"Existing job returned: {job.id} for invoice {invoice_id}")
    
    # 202 Accepted
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.id,
            "invoice_id": invoice_id,
            "status": job.status.value,
            "message": "İşlem kuyruğa alındı" if created_new else "Fatura zaten işleniyor",
            "created_new": created_new
        }
    )


@app.get("/jobs/{job_id}", response_model=dict)
async def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Job durumunu sorgula.
    
    Returns:
        - status: QUEUED | RUNNING | SUCCEEDED | FAILED
        - result: (SUCCEEDED ise) sonuç
        - error: (FAILED ise) hata mesajı
    """
    job = get_job_by_id(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı")
    
    response = {
        "job_id": job.id,
        "invoice_id": job.invoice_id,
        "job_type": job.job_type.value,
        "status": job.status.value,
        "created_at": job.created_at.isoformat(),
    }
    
    if job.started_at:
        response["started_at"] = job.started_at.isoformat()
    
    if job.finished_at:
        response["finished_at"] = job.finished_at.isoformat()
        response["duration_ms"] = int((job.finished_at - job.started_at).total_seconds() * 1000) if job.started_at else None
    
    if job.status == JobStatus.SUCCEEDED and job.result_json:
        response["result"] = job.result_json
    
    if job.status == JobStatus.FAILED and job.error:
        response["error"] = job.error
    
    return response


@app.get("/invoices/{invoice_id}/jobs", response_model=List[dict])
async def get_invoice_jobs(
    invoice_id: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """Invoice'a ait tüm job'ları listele."""
    jobs = get_jobs_by_invoice(db, invoice_id)
    
    return [
        {
            "job_id": job.id,
            "job_type": job.job_type.value,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": job.error if job.status == JobStatus.FAILED else None
        }
        for job in jobs
    ]


@app.get("/jobs", response_model=List[dict])
async def list_all_jobs(
    invoice_id: Optional[str] = Query(default=None, description="Belirli invoice'a ait job'lar"),
    status: Optional[str] = Query(default=None, description="Job durumu: QUEUED, RUNNING, SUCCEEDED, FAILED"),
    job_type: Optional[str] = Query(default=None, description="Job tipi: EXTRACT, VALIDATE, EXTRACT_AND_VALIDATE"),
    limit: int = Query(default=50, ge=1, le=200, description="Maksimum sonuç sayısı"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key)
):
    """
    Tüm job'ları listele - filtreleme destekli.
    
    UI için altın:
    - Invoice sayfasında "iş geçmişi" gösterirsin
    - Hata ayıklama süper kolaylaşır
    - Dashboard'da aktif işleri izlersin
    """
    # Enum dönüşümleri
    status_enum = None
    if status:
        try:
            status_enum = JobStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Geçersiz status: {status}")
    
    job_type_enum = None
    if job_type:
        try:
            job_type_enum = JobType(job_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Geçersiz job_type: {job_type}")
    
    jobs = list_jobs(
        db=db,
        invoice_id=invoice_id,
        status=status_enum,
        job_type=job_type_enum,
        limit=limit
    )
    
    def dt(v):
        return v.isoformat() if v else None
    
    return [
        {
            "job_id": j.id,
            "invoice_id": j.invoice_id,
            "job_type": j.job_type.value,
            "status": j.status.value,
            "payload_json": j.payload_json,
            "result_json": j.result_json if j.status == JobStatus.SUCCEEDED else None,
            "error": j.error if j.status == JobStatus.FAILED else None,
            "created_at": dt(j.created_at),
            "started_at": dt(j.started_at),
            "finished_at": dt(j.finished_at),
            "duration_ms": int((j.finished_at - j.started_at).total_seconds() * 1000) if j.finished_at and j.started_at else None
        }
        for j in jobs
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Webhook Management Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/webhooks", response_model=dict)
async def create_webhook(
    url: str = Query(..., description="Webhook URL"),
    events: str = Query(..., description="Virgülle ayrılmış event listesi"),
    secret: Optional[str] = Query(default=None, description="HMAC signing secret"),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """
    Yeni webhook konfigürasyonu oluştur.
    
    Events:
    - invoice.uploaded, invoice.extracted, invoice.failed
    - offer.created, offer.sent, offer.viewed, offer.accepted, offer.rejected
    - offer.contracting, offer.completed, offer.expired
    - customer.created, customer.updated
    """
    from .services.webhook import create_webhook_config, WEBHOOK_EVENTS
    
    event_list = [e.strip() for e in events.split(",")]
    
    # Validate events
    invalid = [e for e in event_list if e not in WEBHOOK_EVENTS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_events",
                "message": f"Geçersiz event tipleri: {invalid}",
                "valid_events": WEBHOOK_EVENTS
            }
        )
    
    try:
        config_id = create_webhook_config(
            db=db,
            tenant_id=tenant_id,
            url=url,
            events=event_list,
            secret=secret
        )
        
        return {
            "id": config_id,
            "url": url,
            "events": event_list,
            "message": "Webhook oluşturuldu"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/webhooks", response_model=List[dict])
async def list_webhooks(
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """Tenant'ın webhook konfigürasyonlarını listele."""
    from .services.webhook import get_webhook_configs
    
    configs = get_webhook_configs(db, tenant_id, active_only=False)
    
    return [
        {
            "id": c.id,
            "url": c.url,
            "events": c.events,
            "is_active": c.is_active == 1,
            "success_count": c.success_count,
            "failure_count": c.failure_count,
            "last_triggered_at": c.last_triggered_at.isoformat() if c.last_triggered_at else None,
            "created_at": c.created_at.isoformat()
        }
        for c in configs
    ]


@app.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """Webhook konfigürasyonunu sil."""
    from .database import WebhookConfig
    
    config = db.query(WebhookConfig).filter(
        WebhookConfig.id == webhook_id,
        WebhookConfig.tenant_id == tenant_id
    ).first()
    
    if not config:
        raise HTTPException(status_code=404, detail="Webhook bulunamadı")
    
    db.delete(config)
    db.commit()
    
    return {"status": "ok", "message": "Webhook silindi"}


@app.put("/webhooks/{webhook_id}/toggle")
async def toggle_webhook(
    webhook_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """Webhook'u aktif/pasif yap."""
    from .database import WebhookConfig
    
    config = db.query(WebhookConfig).filter(
        WebhookConfig.id == webhook_id,
        WebhookConfig.tenant_id == tenant_id
    ).first()
    
    if not config:
        raise HTTPException(status_code=404, detail="Webhook bulunamadı")
    
    config.is_active = 0 if config.is_active == 1 else 1
    db.commit()
    
    return {
        "status": "ok",
        "is_active": config.is_active == 1,
        "message": f"Webhook {'aktif' if config.is_active else 'pasif'} yapıldı"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Audit Log Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/audit-logs", response_model=List[dict])
async def list_audit_logs(
    action: Optional[str] = Query(default=None, description="Aksiyon filtresi"),
    target_type: Optional[str] = Query(default=None, description="Hedef tipi: invoice, offer, customer"),
    target_id: Optional[str] = Query(default=None, description="Hedef ID"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """
    Audit logları listele.
    
    Kim ne zaman ne yaptı - tüm önemli aksiyonlar loglanır.
    """
    from .services.audit import get_audit_logs
    from .models import AuditAction
    
    action_enum = None
    if action:
        try:
            action_enum = AuditAction(action)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Geçersiz action: {action}. Geçerli değerler: {[a.value for a in AuditAction]}"
            )
    
    logs = get_audit_logs(
        db=db,
        tenant_id=tenant_id,
        action=action_enum,
        target_type=target_type,
        target_id=target_id,
        skip=skip,
        limit=limit
    )
    
    return [
        {
            "id": log.id,
            "action": log.action.value,
            "actor_type": log.actor_type,
            "actor_id": log.actor_id,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "details": log.details_json,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat()
        }
        for log in logs
    ]


@app.get("/audit-logs/stats", response_model=dict)
async def get_audit_stats(
    db: Session = Depends(get_db),
    _: None = Depends(require_api_key),
    tenant_id: str = Header(alias="X-Tenant-Id", default="default")
):
    """Audit log istatistikleri."""
    from sqlalchemy import func
    from .database import AuditLog
    from datetime import datetime, timedelta
    
    # Son 24 saat
    since = datetime.utcnow() - timedelta(hours=24)
    
    # Action bazlı sayılar
    action_counts = db.query(
        AuditLog.action,
        func.count(AuditLog.id)
    ).filter(
        AuditLog.tenant_id == tenant_id,
        AuditLog.created_at >= since
    ).group_by(AuditLog.action).all()
    
    # Toplam
    total = db.query(func.count(AuditLog.id)).filter(
        AuditLog.tenant_id == tenant_id
    ).scalar()
    
    return {
        "total_logs": total,
        "last_24h": {action.value: count for action, count in action_counts},
        "period": "24h"
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: PİYASA REFERANS FİYATLARI (PTF/YEKDEM)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/market-prices")
async def list_market_prices(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key),
    page: int = Query(default=1, ge=1, description="Sayfa numarası (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Sayfa başına kayıt sayısı"),
    sort_by: str = Query(default="period", description="Sıralama alanı: period, ptf_tl_per_mwh, status, updated_at"),
    sort_order: str = Query(default="desc", description="Sıralama yönü: asc veya desc"),
    price_type: Optional[str] = Query(default=None, description="Fiyat tipi filtresi (PTF, SMF, vb.)"),
    status: Optional[str] = Query(default=None, description="Status filtresi: provisional veya final"),
    from_period: Optional[str] = Query(default=None, description="Başlangıç dönemi (YYYY-MM)"),
    to_period: Optional[str] = Query(default=None, description="Bitiş dönemi (YYYY-MM)"),
):
    """
    Piyasa referans fiyatlarını listele (pagination + filtering).
    
    Requires: X-Admin-Key header
    
    Query Parameters:
        page: Sayfa numarası (default: 1)
        page_size: Sayfa başına kayıt (default: 20, max: 100)
        sort_by: Sıralama alanı (default: period)
        sort_order: Sıralama yönü (default: desc)
        price_type: Fiyat tipi filtresi
        status: Status filtresi (provisional/final)
        from_period: Başlangıç dönemi (YYYY-MM)
        to_period: Bitiş dönemi (YYYY-MM)
    
    Returns:
        Paginated PTF kayıt listesi with total count
    
    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
    """
    from .market_price_admin_service import get_market_price_admin_service
    
    # Validate sort_by against allowed fields
    allowed_sort_fields = {"period", "ptf_tl_per_mwh", "status", "updated_at"}
    if sort_by not in allowed_sort_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_SORT_FIELD",
                "message": f"Geçersiz sıralama alanı: {sort_by}. İzin verilen: {', '.join(sorted(allowed_sort_fields))}",
            }
        )
    
    # Validate sort_order
    if sort_order not in ("asc", "desc"):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_SORT_ORDER",
                "message": f"Geçersiz sıralama yönü: {sort_order}. İzin verilen: asc, desc",
            }
        )
    
    service = get_market_price_admin_service()
    
    # Convert page/page_size to offset/limit
    offset = (page - 1) * page_size
    
    # Wrapper ile DB çağrısını sar (read path → retry aktif)
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        result = await wrapper.call(
            _asyncio.to_thread,
            service.list_prices,
            db=db,
            price_type=price_type,
            status=status,
            period_from=from_period,
            period_to=to_period,
            limit=page_size,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
            is_write=False,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    
    return {
        "status": "ok",
        "total": result.total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "period": p.period,
                "ptf_value": p.ptf_tl_per_mwh,
                "status": p.status,
                "captured_at": p.captured_at.isoformat() if p.captured_at else None,
                "is_locked": bool(p.is_locked),
                "updated_by": p.updated_by,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in result.items
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: AUDIT HISTORY (Değişiklik Geçmişi)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/market-prices/history")
async def get_price_history(
    period: str = Query(..., description="Dönem (YYYY-MM format)"),
    price_type: str = Query(default="PTF", description="Fiyat tipi (PTF, SMF, YEKDEM)"),
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key),
):
    """
    Belirli bir dönem+fiyat tipi için değişiklik geçmişini döndür.
    
    Requires: X-Admin-Key header
    
    Returns:
        200: { status, period, price_type, history: [...] }
        404: Kayıt bulunamadı (period+price_type mevcut değil)
    
    Feature: audit-history, Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
    """
    import re
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_PERIOD",
                "message": f"Geçersiz dönem formatı: {period}. Beklenen: YYYY-MM",
                "field": "period",
            }
        )
    
    from .market_price_admin_service import get_market_price_admin_service
    from .ptf_metrics import get_ptf_metrics
    service = get_market_price_admin_service()
    ptf_metrics = get_ptf_metrics()
    
    ptf_metrics.inc_history_query()

    # Read path → wrapper ile sar
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        with ptf_metrics.time_history_query():
            history = await wrapper.call(
                _asyncio.to_thread,
                service.get_history,
                db=db, period=period, price_type=price_type,
                is_write=False,
            )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    
    if history is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "error_code": "RECORD_NOT_FOUND",
                "message": f"{period} / {price_type} için kayıt bulunamadı.",
            }
        )
    
    return {
        "status": "ok",
        "period": period,
        "price_type": price_type,
        "history": [
            {
                "id": h.id,
                "action": h.action,
                "old_value": h.old_value,
                "new_value": h.new_value,
                "old_status": h.old_status,
                "new_status": h.new_status,
                "change_reason": h.change_reason,
                "updated_by": h.updated_by,
                "source": h.source,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DEPRECATED GET ALIASES (must be registered BEFORE /{period} to avoid path conflict)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/admin/market-prices/legacy",
    deprecated=True,
    summary="[DEPRECATED] List market prices (no pagination)",
    description=(
        "DEPRECATED: This endpoint will be removed in 2 releases. "
        "Use GET /admin/market-prices with pagination support instead."
    ),
)
async def deprecated_list_market_prices_legacy(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key),
):
    """
    [DEPRECATED] List market prices without pagination.

    This alias returns all records (max 100) and forwards to the new paginated endpoint.
    Deprecation and Sunset headers are included in the response.

    Requirements: 1.6 (Backward Compatibility)
    """
    _deprecated_alias_usage["get_legacy"] += 1
    logger.warning(
        "[DEPRECATION] Legacy GET /admin/market-prices/legacy used. "
        f"Total usage: {_deprecated_alias_usage['get_legacy']}. "
        "Migrate to new paginated endpoint."
    )

    from .market_price_admin_service import get_market_price_admin_service
    from fastapi.responses import JSONResponse

    service = get_market_price_admin_service()
    result = service.list_prices(
        db=db,
        price_type=None,
        status=None,
        period_from=None,
        period_to=None,
        limit=100,
        offset=0,
        sort_by="period",
        sort_order="desc",
    )

    return JSONResponse(
        content={
            "status": "ok",
            "total": result.total,
            "items": [
                {
                    "period": p.period,
                    "ptf_value": float(p.ptf_tl_per_mwh) if p.ptf_tl_per_mwh is not None else None,
                    "status": p.status,
                    "captured_at": p.captured_at.isoformat() if p.captured_at else None,
                    "is_locked": bool(p.is_locked),
                    "updated_by": p.updated_by,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in result.items
            ],
        },
        headers=_deprecation_headers(),
    )


@app.get(
    "/admin/market-prices/deprecation-stats",
    summary="Deprecated endpoint usage statistics",
    description="Returns usage counts for deprecated alias endpoints.",
)
async def get_deprecation_stats(
    _: str = Depends(require_admin_key),
):
    """
    Deprecated endpoint usage statistics.

    Returns:
        {status: "ok", alias_usage_total: {post_form: int, get_legacy: int}}
    """
    return {
        "status": "ok",
        "alias_usage_total": get_alias_usage_total(),
    }


@app.get("/admin/market-prices/{period}")
async def get_market_price(
    period: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key)
):
    """
    Belirli dönem için piyasa fiyatlarını getir.
    
    Requires: X-Admin-Key header
    
    Args:
        period: Dönem (YYYY-MM format)
    """
    from .market_prices import get_market_prices_or_default
    
    # Wrapper ile DB çağrısını sar (read path)
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        prices = await wrapper.call(
            _asyncio.to_thread,
            get_market_prices_or_default,
            db, period,
            is_write=False,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    
    return {
        "status": "ok",
        "period": prices.period,
        "ptf_tl_per_mwh": prices.ptf_tl_per_mwh,
        "yekdem_tl_per_mwh": prices.yekdem_tl_per_mwh,
        "source": prices.source,
        "is_locked": prices.is_locked
    }


@app.post("/admin/market-prices")
async def upsert_market_price(
    request: Request,
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key),
):
    """
    Piyasa fiyatı ekle veya güncelle (JSON body).
    
    Requires: X-Admin-Key header
    
    JSON Body:
        period: Dönem (YYYY-MM format) - zorunlu
        value: PTF değeri (TL/MWh) - zorunlu
        price_type: Fiyat tipi (default: PTF)
        status: Status (default: provisional)
        source_note: Kaynak notu (opsiyonel)
        change_reason: Değişiklik nedeni (opsiyonel, güncelleme için zorunlu)
        force_update: Final kayıt güncelleme izni (default: false)
    
    Returns:
        {status: "ok", action: "created"|"updated", period: "YYYY-MM", warnings: []}
    
    Error Response:
        {status: "error", error_code: "...", message: "...", field: "...", row_index: null, details: {}}
    
    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
    """
    from .market_price_admin_service import get_market_price_admin_service
    from .market_price_validator import MarketPriceValidator
    
    # Parse JSON body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_JSON",
                "message": "Geçersiz JSON body.",
                "field": None,
                "row_index": None,
                "details": {},
            }
        )
    
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_JSON",
                "message": "JSON body bir obje olmalı.",
                "field": None,
                "row_index": None,
                "details": {},
            }
        )
    
    # Extract fields with defaults
    period = body.get("period")
    value = body.get("value")
    price_type = body.get("price_type", "PTF")
    status_val = body.get("status", "provisional")
    source_note = body.get("source_note")
    change_reason = body.get("change_reason")
    force_update = body.get("force_update", False)
    
    # Required field checks
    if period is None:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "INVALID_PERIOD_FORMAT",
                "message": "Period alanı zorunludur.",
                "field": "period",
                "row_index": None,
                "details": {},
            }
        )
    
    if value is None:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "VALUE_REQUIRED",
                "message": "PTF değeri zorunludur.",
                "field": "value",
                "row_index": None,
                "details": {},
            }
        )
    
    # Validate using MarketPriceValidator
    validator = MarketPriceValidator()
    validation_result, normalized = validator.validate_entry(
        period=str(period),
        value=value,
        status=str(status_val),
        price_type=str(price_type),
    )
    
    if not validation_result.is_valid:
        # Return first validation error in standard error format
        first_error = validation_result.errors[0]
        
        # Map validation error codes to HTTP status codes
        business_rule_codes = {"PERIOD_LOCKED", "FINAL_RECORD_PROTECTED", "STATUS_DOWNGRADE_FORBIDDEN"}
        http_status = 409 if first_error.error_code.value in business_rule_codes else 400
        
        raise HTTPException(
            status_code=http_status,
            detail={
                "status": "error",
                "error_code": first_error.error_code.value,
                "message": first_error.message,
                "field": first_error.field,
                "row_index": None,
                "details": {},
            }
        )
    
    # Set source_note and change_reason on normalized input
    normalized.source_note = source_note
    normalized.change_reason = change_reason
    
    # Determine updated_by from admin key context
    updated_by = "admin"  # Default; in production, extract from auth token
    
    # Upsert via service (write path → wrapper ile sar, retry kapalı)
    service = get_market_price_admin_service()
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        result = await wrapper.call(
            _asyncio.to_thread,
            service.upsert_price,
            db=db,
            normalized=normalized,
            updated_by=updated_by,
            source="epias_manual",
            change_reason=change_reason,
            force_update=force_update,
            is_write=True,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    
    if not result.success:
        # Map service error codes to HTTP status codes
        error_code = result.error.error_code.value
        conflict_codes = {"PERIOD_LOCKED", "FINAL_RECORD_PROTECTED", "STATUS_DOWNGRADE_FORBIDDEN"}
        http_status = 409 if error_code in conflict_codes else 400
        
        raise HTTPException(
            status_code=http_status,
            detail={
                "status": "error",
                "error_code": error_code,
                "message": result.error.message,
                "field": result.error.field,
                "row_index": None,
                "details": {},
            }
        )
    
    # Record upsert metric
    from .ptf_metrics import get_ptf_metrics
    get_ptf_metrics().inc_upsert(normalized.status)
    
    # Determine action
    if result.created:
        action = "created"
    elif result.changed:
        action = "updated"
    else:
        action = "updated"  # no-op still counts as "updated" in response
    
    # Combine validation warnings with service warnings
    all_warnings = validation_result.warnings + result.warnings
    
    return {
        "status": "ok",
        "action": action,
        "period": normalized.period,
        "warnings": all_warnings,
    }


@app.post("/admin/market-prices/{period}/lock")
async def lock_market_price(
    period: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key)
):
    """
    Dönem fiyatlarını kilitle (geçmiş dönem koruması).
    
    Requires: X-Admin-Key header
    """
    from .market_prices import lock_market_prices
    
    # Write path → wrapper ile sar, retry kapalı
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        success, message = await wrapper.call(
            _asyncio.to_thread,
            lock_market_prices,
            db, period,
            is_write=True,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    logger.info(f"[ADMIN] Period locked: {period}")
    return {"status": "ok", "message": message}


@app.post("/admin/market-prices/{period}/unlock")
async def unlock_market_price(
    period: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key)
):
    """
    Dönem kilidini kaldır (dikkatli kullanın!).
    
    Requires: X-Admin-Key header
    """
    from .database import MarketReferencePrice
    
    # Write path → wrapper ile sar, retry kapalı
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")

    def _unlock():
        record = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.period == period
        ).first()
        if not record:
            return None
        record.is_locked = 0
        db.commit()
        return record

    try:
        record = await wrapper.call(_asyncio.to_thread, _unlock, is_write=True)
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)

    if not record:
        raise HTTPException(status_code=404, detail=f"Dönem {period} bulunamadı")
    
    logger.warning(f"[ADMIN] Period unlocked: {period}")
    return {"status": "ok", "message": f"Dönem {period} kilidi kaldırıldı"}


@app.post("/admin/market-prices/import/preview")
async def import_preview(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key),
    file: UploadFile = File(...),
    price_type: str = Form(default="PTF"),
    force_update: bool = Form(default=False),
):
    """
    Import preview: CSV/JSON dosyasını parse et ve önizleme döndür.

    Requires: X-Admin-Key header

    Multipart Form:
        file: CSV veya JSON dosyası
        price_type: Fiyat tipi (default: PTF)
        force_update: Final kayıt güncelleme izni (default: false)

    Returns:
        {status: "ok", preview: {total_rows, valid_rows, invalid_rows,
         new_records, updates, unchanged, final_conflicts, errors: [...]}}

    Requirements: 6.1, 6.2, 6.3, 6.4
    """
    from .bulk_importer import get_bulk_importer, ParseError

    content_bytes = await file.read()

    # Empty file check
    if not content_bytes or not content_bytes.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "EMPTY_FILE",
                "message": "Yüklenen dosya boş.",
                "field": "file",
                "row_index": None,
                "details": {},
            },
        )

    content = content_bytes.decode("utf-8", errors="replace")

    # Detect file type from filename extension
    filename = (file.filename or "").lower()
    if filename.endswith(".json"):
        file_type = "json"
    elif filename.endswith(".csv"):
        file_type = "csv"
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "PARSE_ERROR",
                "message": "Desteklenmeyen dosya formatı. CSV veya JSON dosyası yükleyin.",
                "field": "file",
                "row_index": None,
                "details": {},
            },
        )

    importer = get_bulk_importer()

    # Parse file
    try:
        if file_type == "csv":
            rows = importer.parse_csv(content)
        else:
            rows = importer.parse_json(content)
    except ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "PARSE_ERROR",
                "message": str(exc),
                "field": "file",
                "row_index": None,
                "details": {"row_errors": exc.row_errors} if exc.row_errors else {},
            },
        )

    # Generate preview (read path → wrapper ile sar)
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")
    try:
        preview = await wrapper.call(
            _asyncio.to_thread,
            importer.preview,
            db=db,
            rows=rows,
            price_type=price_type,
            force_update=force_update,
            is_write=False,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)

    return {
        "status": "ok",
        "preview": {
            "total_rows": preview.total_rows,
            "valid_rows": preview.valid_rows,
            "invalid_rows": preview.invalid_rows,
            "new_records": preview.new_records,
            "updates": preview.updates,
            "unchanged": preview.unchanged,
            "final_conflicts": preview.final_conflicts,
            "errors": preview.errors,
        },
    }


@app.post("/admin/market-prices/import/apply")
async def import_apply(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key),
    file: UploadFile = File(...),
    price_type: str = Form(default="PTF"),
    force_update: bool = Form(default=False),
    strict_mode: bool = Form(default=False),
):
    """
    Import apply: CSV/JSON dosyasını parse et, doğrula ve kaydet.

    Requires: X-Admin-Key header

    Multipart Form:
        file: CSV veya JSON dosyası
        price_type: Fiyat tipi (default: PTF)
        force_update: Final kayıt güncelleme izni (default: false)
        strict_mode: Herhangi bir satır hatalıysa tüm batch'i reddet (default: false)

    Returns:
        {status: "ok", result: {success, imported_count, skipped_count,
         error_count, details: [...]}}

    Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
    """
    from .bulk_importer import get_bulk_importer, ParseError

    content_bytes = await file.read()

    # Empty file check
    if not content_bytes or not content_bytes.strip():
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "EMPTY_FILE",
                "message": "Yüklenen dosya boş.",
                "field": "file",
                "row_index": None,
                "details": {},
            },
        )

    content = content_bytes.decode("utf-8", errors="replace")

    # Detect file type from filename extension
    filename = (file.filename or "").lower()
    if filename.endswith(".json"):
        file_type = "json"
    elif filename.endswith(".csv"):
        file_type = "csv"
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "PARSE_ERROR",
                "message": "Desteklenmeyen dosya formatı. CSV veya JSON dosyası yükleyin.",
                "field": "file",
                "row_index": None,
                "details": {},
            },
        )

    importer = get_bulk_importer()

    # Parse file
    try:
        if file_type == "csv":
            rows = importer.parse_csv(content)
        else:
            rows = importer.parse_json(content)
    except ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": "PARSE_ERROR",
                "message": str(exc),
                "field": "file",
                "row_index": None,
                "details": {"row_errors": exc.row_errors} if exc.row_errors else {},
            },
        )

    # Apply import (with metrics + wrapper, write path → retry kapalı)
    from .ptf_metrics import get_ptf_metrics
    ptf_metrics = get_ptf_metrics()

    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_primary")

    def _apply_with_timing():
        with ptf_metrics.time_import_apply():
            return importer.apply(
                db=db,
                rows=rows,
                updated_by="admin",
                price_type=price_type,
                force_update=force_update,
                strict_mode=strict_mode,
            )

    try:
        result = await wrapper.call(
            _asyncio.to_thread,
            _apply_with_timing,
            is_write=True,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)

    # Record row-level metrics
    ptf_metrics.inc_import_rows("accepted", result.accepted_count)
    ptf_metrics.inc_import_rows("rejected", result.rejected_count)

    return {
        "status": "ok",
        "result": {
            "success": result.success,
            "imported_count": result.imported_count,
            "skipped_count": result.skipped_count,
            "error_count": result.error_count,
            "details": result.details,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HESAPLAMA İÇİN MARKET PRICE LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/market-prices/{price_type}/{period}")
async def get_market_price_for_calculation(
    price_type: str,
    period: str,
    db: Session = Depends(get_db),
    _: str = Depends(require_api_key),
):
    """
    Hesaplama için piyasa fiyatı getir (calculation lookup).

    Path Parameters:
        price_type: Fiyat tipi (PTF, SMF, YEKDEM vb.)
        period: Dönem (YYYY-MM format)

    Returns:
        {period, value, price_type, status, is_provisional_used}

    Error Responses:
        400: Invalid period format, future period, invalid price type
        404: Period not found

    Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7
    """
    from .market_price_admin_service import get_market_price_admin_service
    from .market_price_validator import MarketPriceValidator

    validator = MarketPriceValidator()

    # Validate price_type
    pt_result = validator.validate_price_type(price_type)
    if not pt_result.is_valid:
        first_error = pt_result.errors[0]
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": first_error.error_code.value,
                "message": first_error.message,
                "field": first_error.field,
                "row_index": None,
                "details": {},
            },
        )

    # Validate period format
    period_result = validator.validate_period(period)
    if not period_result.is_valid:
        first_error = period_result.errors[0]
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "error_code": first_error.error_code.value,
                "message": first_error.message,
                "field": first_error.field,
                "row_index": None,
                "details": {},
            },
        )

    # Lookup via service (read path → wrapper ile sar, DB_REPLICA)
    service = get_market_price_admin_service()
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError
    wrapper = _get_wrapper("db_replica")
    try:
        result, error = await wrapper.call(
            _asyncio.to_thread,
            service.get_for_calculation,
            db=db,
            period=period,
            price_type=price_type,
            is_write=False,
        )
    except (CircuitOpenError, _asyncio.TimeoutError, ConnectionError, OSError) as exc:
        raise _map_wrapper_error_to_http(exc)

    # Record lookup metric
    from .ptf_metrics import get_ptf_metrics
    ptf_metrics = get_ptf_metrics()
    if error is not None:
        ptf_metrics.inc_lookup(hit=False)
        # Map error codes to HTTP status codes
        error_code = error.error_code.value
        if error_code == "PERIOD_NOT_FOUND":
            http_status = 404
        else:
            http_status = 400

        raise HTTPException(
            status_code=http_status,
            detail={
                "status": "error",
                "error_code": error_code,
                "message": error.message,
                "field": error.field,
                "row_index": None,
                "details": {},
            },
        )

    ptf_metrics.inc_lookup(hit=True, status=result.status)

    return {
        "period": result.period,
        "value": float(result.value),
        "price_type": result.price_type,
        "status": result.status,
        "is_provisional_used": result.is_provisional_used,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DEPRECATED ALIASES (Backward Compatibility)
# Plan: Bu endpoint'ler 2 release sonra kaldırılacaktır.
# Yeni endpoint'leri kullanın:
#   POST /admin/market-prices (JSON body)
#   GET /admin/market-prices (pagination destekli)
# ═══════════════════════════════════════════════════════════════════════════════

# Simple in-memory counter for deprecated endpoint usage tracking
_deprecated_alias_usage: dict[str, int] = {
    "post_form": 0,
    "get_legacy": 0,
}


def get_alias_usage_total() -> dict[str, int]:
    """Return current deprecated alias usage counts."""
    return dict(_deprecated_alias_usage)


def _deprecation_headers() -> dict[str, str]:
    """Standard deprecation headers for deprecated endpoints."""
    return {
        "Deprecation": "true",
        "Sunset": "2025-12-31",
        "X-Deprecation-Notice": "This endpoint is deprecated and will be removed in 2 releases. Use the new JSON-based endpoints instead.",
    }


@app.post(
    "/admin/market-prices/form",
    deprecated=True,
    summary="[DEPRECATED] Form-based piyasa fiyatı ekle/güncelle",
    description=(
        "⚠️ DEPRECATED: Bu endpoint 2 release sonra kaldırılacaktır. "
        "Yeni JSON-based POST /admin/market-prices endpoint'ini kullanın.\n\n"
        "Form-based (multipart) giriş → JSON-based endpoint'e yönlendirir."
    ),
)
async def deprecated_upsert_market_price_form(
    request: Request,
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key),
    period: str = Form(..., description="Dönem (YYYY-MM format)"),
    value: float = Form(..., description="PTF değeri (TL/MWh)"),
    price_type: str = Form(default="PTF", description="Fiyat tipi"),
    status: str = Form(default="provisional", description="Status: provisional veya final"),
    source_note: Optional[str] = Form(default=None, description="Kaynak notu"),
    change_reason: Optional[str] = Form(default=None, description="Değişiklik nedeni"),
    force_update: bool = Form(default=False, description="Final kayıt güncelleme izni"),
):
    """
    [DEPRECATED] Form-based piyasa fiyatı ekle/güncelle.

    ⚠️ Bu endpoint 2 release sonra kaldırılacaktır.
    Yeni JSON-based POST /admin/market-prices endpoint'ini kullanın.

    Bu alias, form-data'yı JSON body'ye dönüştürüp yeni endpoint'e yönlendirir.
    Deprecation ve Sunset header'ları response'a eklenir.

    Requirements: 1.6 (Backward Compatibility)
    """
    _deprecated_alias_usage["post_form"] += 1
    logger.warning(
        "[DEPRECATION] Form-based POST /admin/market-prices/form used. "
        f"Total usage: {_deprecated_alias_usage['post_form']}. "
        "Migrate to new JSON-based endpoint."
    )

    # Build JSON body from form fields and forward to the new JSON-based endpoint
    from .market_price_admin_service import get_market_price_admin_service
    from .market_price_validator import MarketPriceValidator

    # Validate using MarketPriceValidator
    validator = MarketPriceValidator()
    validation_result, normalized = validator.validate_entry(
        period=str(period),
        value=value,
        status=str(status),
        price_type=str(price_type),
    )

    if not validation_result.is_valid:
        first_error = validation_result.errors[0]
        business_rule_codes = {"PERIOD_LOCKED", "FINAL_RECORD_PROTECTED", "STATUS_DOWNGRADE_FORBIDDEN"}
        http_status = 409 if first_error.error_code.value in business_rule_codes else 400
        raise HTTPException(
            status_code=http_status,
            detail={
                "status": "error",
                "error_code": first_error.error_code.value,
                "message": first_error.message,
                "field": first_error.field,
                "row_index": None,
                "details": {},
            },
        )

    normalized.source_note = source_note
    normalized.change_reason = change_reason

    updated_by = "admin"
    service = get_market_price_admin_service()
    result = service.upsert_price(
        db=db,
        normalized=normalized,
        updated_by=updated_by,
        source="epias_manual",
        change_reason=change_reason,
        force_update=force_update,
    )

    if not result.success:
        error_code = result.error.error_code.value
        conflict_codes = {"PERIOD_LOCKED", "FINAL_RECORD_PROTECTED", "STATUS_DOWNGRADE_FORBIDDEN"}
        http_status = 409 if error_code in conflict_codes else 400
        raise HTTPException(
            status_code=http_status,
            detail={
                "status": "error",
                "error_code": error_code,
                "message": result.error.message,
                "field": result.error.field,
                "row_index": None,
                "details": {},
            },
        )

    if result.created:
        action = "created"
    elif result.changed:
        action = "updated"
    else:
        action = "updated"

    all_warnings = validation_result.warnings + result.warnings

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content={
            "status": "ok",
            "action": action,
            "period": normalized.period,
            "warnings": all_warnings,
        },
        headers=_deprecation_headers(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# EPİAŞ ENTEGRASYONU
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/epias/sync/{period}")
async def sync_period_from_epias(
    period: str,
    force_refresh: bool = False,
    use_mock: bool = False,
    db: Session = Depends(get_db)
):
    """
    EPİAŞ'tan belirli dönem için PTF/YEKDEM verilerini çek ve cache'le.
    
    Args:
        period: Dönem (YYYY-MM format, örn: 2025-01)
        force_refresh: True ise mevcut cache'i yoksay
        use_mock: True ise mock veri kullan (test/demo için)
    
    Returns:
        {
            "status": "ok" | "error",
            "period": "2025-01",
            "ptf_tl_per_mwh": 2974.1,
            "yekdem_tl_per_mwh": 364.0,
            "source": "epias" | "mock",
            "message": "EPİAŞ'tan alındı ve cache'lendi"
        }
    """
    from .market_prices import fetch_and_cache_from_epias
    
    # Period format kontrolü
    if not period or len(period) != 7 or period[4] != '-':
        raise HTTPException(
            status_code=400, 
            detail="Geçersiz dönem formatı. YYYY-MM kullanın (örn: 2025-01)"
        )
    
    try:
        success, prices, message = await fetch_and_cache_from_epias(db, period, force_refresh, use_mock)
        
        if not success:
            return {
                "status": "error",
                "period": period,
                "message": message
            }
        
        return {
            "status": "ok",
            "period": period,
            "ptf_tl_per_mwh": prices.ptf_tl_per_mwh,
            "yekdem_tl_per_mwh": prices.yekdem_tl_per_mwh,
            "source": prices.source,
            "message": message
        }
        
    except Exception as e:
        logger.error(f"EPİAŞ sync hatası: {e}")
        raise HTTPException(status_code=500, detail=f"EPİAŞ API hatası: {str(e)}")


@app.get("/api/epias/prices/{period}")
async def get_prices_with_epias_fallback(
    period: str,
    auto_fetch: bool = True,
    db: Session = Depends(get_db)
):
    """
    Dönem için piyasa fiyatlarını al - DB yoksa EPİAŞ'tan çek.
    
    Öncelik sırası:
    1. DB'deki kayıt
    2. EPİAŞ API (auto_fetch=True ise)
    3. Default değerler
    
    Args:
        period: Dönem (YYYY-MM format)
        auto_fetch: EPİAŞ'tan otomatik çek (default: True)
    
    Returns:
        {
            "period": "2025-01",
            "ptf_tl_per_mwh": 2974.1,
            "yekdem_tl_per_mwh": 364.0,
            "source": "epias",
            "source_description": "EPİAŞ API: EPİAŞ'tan alındı ve cache'lendi"
        }
    """
    from .market_prices import get_market_prices_with_epias_fallback
    
    prices, source_desc = get_market_prices_with_epias_fallback(db, period, auto_fetch)
    
    return {
        "period": prices.period,
        "ptf_tl_per_mwh": prices.ptf_tl_per_mwh,
        "yekdem_tl_per_mwh": prices.yekdem_tl_per_mwh,
        "source": prices.source,
        "source_description": source_desc,
        "is_locked": prices.is_locked
    }


@app.get("/api/epias/missing-periods")
async def get_missing_periods(
    months_back: int = 12,
    db: Session = Depends(get_db)
):
    """
    Sync edilmesi gereken dönemleri listele.
    
    DB'de kaydı olmayan veya source="default" olan dönemler.
    
    Args:
        months_back: Kaç ay geriye bak (default: 12)
    
    Returns:
        {
            "missing_periods": ["2025-01", "2024-12", ...],
            "count": 5
        }
    """
    from .market_prices import get_periods_needing_sync
    
    missing = get_periods_needing_sync(db, months_back)
    
    return {
        "missing_periods": missing,
        "count": len(missing)
    }


@app.post("/admin/epias/sync-all")
async def sync_all_missing_from_epias(
    months_back: int = 12,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key)
):
    """
    Eksik tüm dönemler için EPİAŞ'tan veri çek.
    
    Requires: X-Admin-Key header
    
    Args:
        months_back: Kaç ay geriye bak (default: 12)
        force_refresh: Mevcut cache'i yoksay
    
    Returns:
        {
            "status": "ok",
            "synced": {"2025-01": true, "2024-12": false, ...},
            "success_count": 10,
            "error_count": 2
        }
    """
    from .market_prices import get_periods_needing_sync, sync_multiple_periods_from_epias
    
    # Eksik dönemleri bul
    if force_refresh:
        # Tüm dönemleri sync et
        from datetime import datetime
        current = datetime.now()
        periods = []
        for i in range(months_back):
            year = current.year
            month = current.month - i
            while month <= 0:
                month += 12
                year -= 1
            periods.append(f"{year}-{month:02d}")
    else:
        periods = get_periods_needing_sync(db, months_back)
    
    if not periods:
        return {
            "status": "ok",
            "message": "Sync edilecek dönem yok",
            "synced": {},
            "success_count": 0,
            "error_count": 0
        }
    
    # Sync et
    results = await sync_multiple_periods_from_epias(db, periods, force_refresh)
    
    synced = {period: success for period, (success, _) in results.items()}
    success_count = sum(1 for s in synced.values() if s)
    error_count = len(synced) - success_count
    
    logger.info(f"[ADMIN] EPİAŞ bulk sync: {success_count} başarılı, {error_count} hatalı")
    
    return {
        "status": "ok",
        "synced": synced,
        "details": {period: msg for period, (_, msg) in results.items()},
        "success_count": success_count,
        "error_count": error_count
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: DAĞITIM TARİFELERİ (EPDK)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/distribution-tariffs")
async def list_distribution_tariffs(
    db: Session = Depends(get_db),
    _: str = Depends(require_admin_key)
):
    """
    Tüm EPDK dağıtım tarifelerini listele (DB'den).
    
    Requires: X-Admin-Key header
    """
    from .database import DistributionTariffDB
    
    records = db.query(DistributionTariffDB).order_by(
        DistributionTariffDB.tariff_group,
        DistributionTariffDB.voltage_level,
        DistributionTariffDB.term_type
    ).all()
    
    tariffs = [
        {
            "id": r.id,
            "tariff_group": r.tariff_group,
            "voltage_level": r.voltage_level,
            "term_type": r.term_type,
            "unit_price_tl_per_kwh": r.unit_price_tl_per_kwh,
            "key": f"{r.tariff_group}/{r.voltage_level}/{r.term_type}",
            "valid_from": r.valid_from,
            "valid_to": r.valid_to,
            "source_note": r.source_note
        }
        for r in records
    ]
    
    return {
        "status": "ok",
        "count": len(tariffs),
        "tariffs": tariffs,
        "note": "DB'den (EPDK tarifeleri)"
    }


@app.get("/admin/distribution-tariffs/lookup")
async def lookup_distribution_tariff(
    tariff_group: str = Query(..., description="Tarife grubu (Sanayi, Kamu, Ticarethane, vb.)"),
    voltage_level: str = Query(..., description="Gerilim (AG, OG)"),
    term_type: str = Query(..., description="Terim tipi (Tek Terim, Çift Terim)"),
    _: str = Depends(require_admin_key)
):
    """
    Tarife bilgilerine göre dağıtım birim fiyatını getir.
    
    Requires: X-Admin-Key header
    """
    from .distribution_tariffs import get_distribution_unit_price
    
    result = get_distribution_unit_price(tariff_group, voltage_level, term_type)
    
    return {
        "status": "ok" if result.success else "error",
        "success": result.success,
        "unit_price_tl_per_kwh": result.unit_price,
        "tariff_key": result.tariff_key,
        "normalized": {
            "group": result.normalized_group,
            "voltage": result.normalized_voltage,
            "term": result.normalized_term
        },
        "error_message": result.error_message
    }


@app.get("/admin/distribution-tariffs/parse")
async def parse_tariff_string(
    tariff_string: str = Query(..., description="Tam tarife string'i (örn: 'SANAYİ OG ÇİFT TERİM')")
):
    """
    Tam tarife string'inden dağıtım birim fiyatını getir.
    """
    from .distribution_tariffs import get_distribution_from_tariff_string
    
    result = get_distribution_from_tariff_string(tariff_string)
    
    return {
        "status": "ok" if result.success else "error",
        "input": tariff_string,
        "success": result.success,
        "unit_price_tl_per_kwh": result.unit_price,
        "tariff_key": result.tariff_key,
        "error_message": result.error_message
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INCIDENT ENDPOINTS (Sprint 3)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/incidents")
async def list_incidents(
    status: Optional[str] = Query(default=None, description="Filtre: OPEN, ACK, RESOLVED"),
    severity: Optional[str] = Query(default=None, description="Filtre: S1, S2, S3, S4"),
    category: Optional[str] = Query(default=None, description="Filtre: PARSE_FAIL, TARIFF_MISSING, vb."),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key)
):
    """
    Incident listesi getir.
    
    Filtreler:
    - status: OPEN, ACK, RESOLVED
    - severity: S1 (kritik), S2 (yüksek), S3 (orta), S4 (düşük)
    - category: PARSE_FAIL, TARIFF_MISSING, PRICE_MISSING, MISMATCH, OUTLIER, vb.
    """
    from .incident_service import get_incidents
    
    incidents = get_incidents(
        db=db,
        tenant_id="default",
        status=status,
        severity=severity,
        category=category,
        limit=limit
    )
    
    return {
        "status": "ok",
        "count": len(incidents),
        "incidents": incidents
    }


@app.get("/admin/incidents/{incident_id}")
async def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key)
):
    """
    Tek incident detayı getir.
    """
    from .database import Incident
    
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident bulunamadı")
    
    return {
        "id": incident.id,
        "trace_id": incident.trace_id,
        "tenant_id": incident.tenant_id,
        "invoice_id": incident.invoice_id,
        "offer_id": incident.offer_id,
        "severity": incident.severity,
        "category": incident.category,
        "message": incident.message,
        "details": incident.details_json,
        "status": incident.status,
        "resolution_note": incident.resolution_note,
        "resolved_by": incident.resolved_by,
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None
    }


@app.patch("/admin/incidents/{incident_id}")
async def update_incident(
    incident_id: int,
    status: str = Form(..., description="Yeni durum: OPEN, ACK, RESOLVED"),
    resolution_note: Optional[str] = Form(default=None, description="Çözüm notu"),
    resolved_by: Optional[str] = Form(default=None, description="Çözen kişi"),
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key)
):
    """
    Incident durumunu güncelle.
    
    Status değerleri:
    - OPEN: Açık (yeni)
    - ACK: Kabul edildi (inceleniyor)
    - RESOLVED: Çözüldü
    """
    from .incident_service import update_incident_status
    
    valid_statuses = ["OPEN", "ACK", "RESOLVED"]
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Geçersiz status. Geçerli değerler: {valid_statuses}"
        )
    
    success = update_incident_status(
        db=db,
        incident_id=incident_id,
        status=status,
        resolution_note=resolution_note,
        resolved_by=resolved_by
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Incident bulunamadı")
    
    return {
        "status": "ok",
        "message": f"Incident #{incident_id} durumu '{status}' olarak güncellendi"
    }


@app.get("/admin/incidents/stats")
async def get_incident_stats(
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key)
):
    """
    Incident istatistikleri.
    """
    from .database import Incident
    from sqlalchemy import func
    
    # Status bazlı sayılar
    status_counts = db.query(
        Incident.status,
        func.count(Incident.id)
    ).filter(
        Incident.tenant_id == "default"
    ).group_by(Incident.status).all()
    
    # Severity bazlı sayılar
    severity_counts = db.query(
        Incident.severity,
        func.count(Incident.id)
    ).filter(
        Incident.tenant_id == "default"
    ).group_by(Incident.severity).all()
    
    # Category bazlı sayılar
    category_counts = db.query(
        Incident.category,
        func.count(Incident.id)
    ).filter(
        Incident.tenant_id == "default"
    ).group_by(Incident.category).all()
    
    return {
        "status": "ok",
        "by_status": {s: c for s, c in status_counts},
        "by_severity": {s: c for s, c in severity_counts},
        "by_category": {c: cnt for c, cnt in category_counts},
        "total": sum(c for _, c in status_counts)
    }


@app.get("/admin/system-health")
async def get_system_health(
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key),
    reference_date: Optional[str] = Query(default=None, description="Referans tarih (YYYY-MM-DD)"),
    period_days: int = Query(default=7, description="Dönem uzunluğu (gün)")
):
    """
    Sistem sağlık raporu (Sprint 8.6).
    
    İçerik:
    - Dönem istatistikleri (mismatch, S1/S2, OCR suspect)
    - Drift alerts (triple guard: n>=20 AND delta>=5 AND rate>=2x)
    - Top offenders (provider bazlı mismatch RATE)
    - Mismatch ratio histogram
    - Action class dağılımı
    """
    from datetime import date as date_type
    from .incident_metrics import generate_system_health_report
    
    # Parse reference date
    ref_date = None
    if reference_date:
        try:
            ref_date = date_type.fromisoformat(reference_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format: {reference_date}. Use YYYY-MM-DD."
            )
    
    report = generate_system_health_report(
        db=db,
        tenant_id="default",
        reference_date=ref_date,
        period_days=period_days,
    )
    
    return {
        "status": "ok",
        "report": report.to_dict(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.7: FEEDBACK LOOP ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@app.patch("/admin/incidents/{incident_id}/feedback")
async def submit_incident_feedback(
    incident_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key),
):
    """
    Submit feedback for a resolved incident (Sprint 8.7).
    
    UPSERT semantics: each submission overwrites previous feedback.
    Both feedback_at and updated_at are always updated.
    
    Request body:
    {
        "action_taken": "VERIFIED_OCR" | "VERIFIED_LOGIC" | "ACCEPTED_ROUNDING" | "ESCALATED" | "NO_ACTION_REQUIRED",
        "was_hint_correct": true | false,
        "actual_root_cause": "optional string (max 200 char)",
        "resolution_time_seconds": 120
    }
    
    Error codes:
    - incident_not_found (404): Incident does not exist
    - incident_not_resolved (400): Incident is not in RESOLVED status
    - invalid_feedback_action (400): action_taken is not a valid enum value
    - invalid_feedback_data (400): Validation error (missing was_hint_correct, negative time, etc.)
    """
    from .incident_metrics import submit_feedback, FeedbackValidationError
    
    # Parse request body
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_feedback_data", "message": "Invalid JSON body"}
        )
    
    # Get user_id from auth context (admin_key for now)
    # In production, this would come from JWT/session
    user_id = f"admin:{admin_key[:8]}..." if admin_key else "unknown"
    
    try:
        incident = submit_feedback(
            db=db,
            incident_id=incident_id,
            payload=payload,
            user_id=user_id,
        )
        return {
            "status": "ok",
            "incident_id": incident.id,
            "feedback": incident.feedback_json,
        }
    except ValueError as e:
        # Incident not found
        raise HTTPException(
            status_code=404,
            detail={"code": "incident_not_found", "message": str(e)}
        )
    except FeedbackValidationError as e:
        # Validation error (state guard, invalid action, etc.)
        raise HTTPException(
            status_code=400,
            detail={"code": e.code, "message": e.message}
        )


@app.get("/admin/feedback-stats")
async def get_feedback_stats(
    db: Session = Depends(get_db),
    admin_key: str = Depends(require_admin_key),
    start_date: Optional[str] = Query(default=None, description="Başlangıç tarihi (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(default=None, description="Bitiş tarihi (YYYY-MM-DD)"),
):
    """
    Get feedback calibration metrics (Sprint 8.7).
    
    Returns:
    - hint_accuracy_rate: was_hint_correct=true / total_feedback
    - action_class_accuracy: Per action class accuracy rates
    - avg_resolution_time_by_class: Average resolution time per action class
    - feedback_coverage: resolved_with_feedback / resolved_total
    - total_feedback_count: Total number of feedback submissions
    
    All rates are null-safe: return 0.0 when denominator is 0.
    """
    from datetime import date as date_type
    from .incident_metrics import get_feedback_stats as get_stats
    
    # Parse dates
    start = None
    end = None
    
    if start_date:
        try:
            start = date_type.fromisoformat(start_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid start_date format: {start_date}. Use YYYY-MM-DD."
            )
    
    if end_date:
        try:
            end = date_type.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid end_date format: {end_date}. Use YYYY-MM-DD."
            )
    
    stats = get_stats(
        db=db,
        tenant_id="default",
        start_date=start,
        end_date=end,
    )
    
    return {
        "status": "ok",
        "stats": stats.to_dict(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# OPS-GUARD ADMIN API (Feature: ops-guard, Task 4.2)
# ═══════════════════════════════════════════════════════════════════════════════

from pydantic import BaseModel as PydanticBaseModel


class KillSwitchUpdateRequest(PydanticBaseModel):
    enabled: bool
    reason: str | None = None


@app.get("/admin/ops/kill-switches")
async def list_kill_switches(
    _: str = Depends(require_admin_key),
):
    """List all kill-switch states. Requires admin key."""
    from .kill_switch import KillSwitchManager
    from .guard_config import get_guard_config
    from .ptf_metrics import get_ptf_metrics

    manager = _get_kill_switch_manager()
    return {"status": "ok", "switches": manager.get_all_switches()}


@app.put("/admin/ops/kill-switches/{switch_name}")
async def update_kill_switch(
    switch_name: str,
    body: KillSwitchUpdateRequest,
    request: Request,
    admin_key: str = Depends(require_admin_key),
):
    """Update a kill-switch state. Requires admin key."""
    manager = _get_kill_switch_manager()
    actor = admin_key if admin_key != "admin-bypass" else "admin"
    result = manager.set_switch(switch_name, body.enabled, actor)
    return {"status": "ok", "switch": result}


@app.get("/admin/ops/status")
async def get_ops_status(
    _: str = Depends(require_admin_key),
):
    """Guard status summary. Requires admin key."""
    from .guard_config import get_guard_config

    manager = _get_kill_switch_manager()
    config = get_guard_config()

    return {
        "status": "ok",
        "guard_config": {
            "schema_version": config.schema_version,
            "config_version": config.config_version,
            "config_hash": config.config_hash,
        },
        "kill_switches": manager.get_all_switches(),
    }


# ── Kill-switch singleton ────────────────────────────────────────────────────

_kill_switch_manager = None


def _get_kill_switch_manager():
    """Lazy singleton for KillSwitchManager."""
    global _kill_switch_manager
    if _kill_switch_manager is None:
        from .kill_switch import KillSwitchManager
        from .guard_config import get_guard_config
        from .ptf_metrics import get_ptf_metrics
        _kill_switch_manager = KillSwitchManager(get_guard_config(), get_ptf_metrics())
    return _kill_switch_manager


# ── CB Registry singleton (Feature: dependency-wrappers, Task 6) ──────────────

_cb_registry = None


def _get_cb_registry():
    """Lazy singleton for CircuitBreakerRegistry."""
    global _cb_registry
    if _cb_registry is None:
        from .guards.circuit_breaker import CircuitBreakerRegistry
        from .guard_config import get_guard_config
        from .ptf_metrics import get_ptf_metrics
        _cb_registry = CircuitBreakerRegistry(get_guard_config(), get_ptf_metrics())
    return _cb_registry


# ── Dependency Wrapper Factory (Feature: dependency-wrappers, Task 10) ────────

def _get_wrapper(dependency_name: str):
    """
    Dependency adına göre wrapper oluştur.

    Tüm dependency çağrıları bu factory üzerinden geçmeli.
    Doğrudan client kullanımı yasak (bypass koruması).

    Args:
        dependency_name: Dependency enum value (db_primary, external_api, vb.)

    Returns:
        DependencyWrapper instance
    """
    from .guards.circuit_breaker import Dependency
    from .guards.dependency_wrapper import create_wrapper
    from .guard_config import get_guard_config
    from .ptf_metrics import get_ptf_metrics

    dep = Dependency(dependency_name)
    return create_wrapper(dep, _get_cb_registry(), get_guard_config(), get_ptf_metrics())


def _map_wrapper_error_to_http(exc: Exception) -> HTTPException:
    """
    Wrapper exception → HTTP response mapping.

    Error Mapping Tablosu (sabit):
        CircuitOpenError  → 503 CIRCUIT_OPEN
        TimeoutError      → 504 DEPENDENCY_TIMEOUT
        ConnectionError   → 502 DEPENDENCY_UNAVAILABLE
        OSError           → 502 DEPENDENCY_UNAVAILABLE
        Diğer CB failure  → 502 DEPENDENCY_ERROR
    """
    import asyncio as _asyncio
    from .guards.dependency_wrapper import CircuitOpenError

    if isinstance(exc, CircuitOpenError):
        return HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "error_code": "CIRCUIT_OPEN",
                "message": f"Servis geçici olarak kullanılamıyor ({exc.dependency}).",
            },
        )

    if isinstance(exc, (_asyncio.TimeoutError, TimeoutError)):
        return HTTPException(
            status_code=504,
            detail={
                "status": "error",
                "error_code": "DEPENDENCY_TIMEOUT",
                "message": "Bağımlılık zaman aşımına uğradı.",
            },
        )

    if isinstance(exc, (ConnectionError, ConnectionRefusedError, OSError)):
        return HTTPException(
            status_code=502,
            detail={
                "status": "error",
                "error_code": "DEPENDENCY_UNAVAILABLE",
                "message": "Bağımlılık erişilemez durumda.",
            },
        )

    # Diğer CB failure (5xx response vb.)
    return HTTPException(
        status_code=502,
        detail={
            "status": "error",
            "error_code": "DEPENDENCY_ERROR",
            "message": f"Bağımlılık hatası: {type(exc).__name__}",
        },
    )
