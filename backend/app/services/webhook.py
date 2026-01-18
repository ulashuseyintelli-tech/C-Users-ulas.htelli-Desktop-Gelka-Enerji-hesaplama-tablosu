"""
Webhook Service - Event-driven webhook delivery system

Events:
- invoice.uploaded: Fatura yÃ¼klendi
- invoice.extracted: Fatura analiz edildi
- invoice.validated: Fatura doÄŸrulandÄ±
- invoice.failed: Fatura iÅŸleme baÅŸarÄ±sÄ±z
- offer.created: Teklif oluÅŸturuldu
- offer.status_changed: Teklif durumu deÄŸiÅŸti
- offer.sent: Teklif gÃ¶nderildi
- offer.viewed: Teklif gÃ¶rÃ¼ntÃ¼lendi
- offer.accepted: Teklif kabul edildi
- offer.rejected: Teklif reddedildi
- offer.contracting: Teklif sÃ¶zleÅŸme aÅŸamasÄ±nda
- offer.completed: Teklif tamamlandÄ±
- offer.expired: Teklif sÃ¼resi doldu
- customer.created: MÃ¼ÅŸteri oluÅŸturuldu
- customer.updated: MÃ¼ÅŸteri gÃ¼ncellendi
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timedelta, UTC
from typing import Optional, Any, List
from enum import Enum

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Desteklenen webhook event'leri
WEBHOOK_EVENTS = [
    "invoice.uploaded",
    "invoice.extracted",
    "invoice.validated",
    "invoice.failed",
    "offer.created",
    "offer.sent",
    "offer.viewed",
    "offer.accepted",
    "offer.rejected",
    "offer.contracting",
    "offer.completed",
    "offer.expired",
    "offer.draft",
    "customer.created",
    "customer.updated",
]

# Configuration
WEBHOOK_TIMEOUT = float(os.getenv("WEBHOOK_TIMEOUT", "10.0"))  # seconds
WEBHOOK_MAX_RETRIES = int(os.getenv("WEBHOOK_MAX_RETRIES", "3"))
WEBHOOK_RETRY_DELAYS = [60, 300, 900]  # 1min, 5min, 15min


class WebhookEvent(str, Enum):
    """Desteklenen webhook event'leri"""
    INVOICE_UPLOADED = "invoice.uploaded"
    INVOICE_EXTRACTED = "invoice.extracted"
    INVOICE_VALIDATED = "invoice.validated"
    OFFER_CREATED = "offer.created"
    OFFER_STATUS_CHANGED = "offer.status_changed"
    OFFER_ACCEPTED = "offer.accepted"
    OFFER_REJECTED = "offer.rejected"


class WebhookDeliveryStatus(str, Enum):
    """Webhook delivery durumlarÄ±"""
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"


def generate_signature(payload: dict, secret: str) -> str:
    """
    Webhook payload iÃ§in HMAC-SHA256 imza oluÅŸtur.
    Header: X-Webhook-Signature
    """
    payload_bytes = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    signature = hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    return f"sha256={signature}"


def verify_signature(payload: dict, signature: str, secret: str) -> bool:
    """Webhook imzasÄ±nÄ± doÄŸrula"""
    expected = generate_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


async def send_webhook(
    url: str,
    event: str,
    payload: dict,
    secret: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: float = WEBHOOK_TIMEOUT
) -> tuple[bool, int, str]:
    """
    Webhook gÃ¶nder.
    
    Returns:
        (success, status_code, response_body)
    """
    # Payload'Ä± event wrapper'a sar
    webhook_payload = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat() + "Z",
        "data": payload
    }
    
    # Headers hazÄ±rla
    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Gelka-Webhook/1.0",
        "X-Webhook-Event": event,
    }
    
    # Custom headers ekle
    if headers:
        request_headers.update(headers)
    
    # Ä°mza ekle (secret varsa)
    if secret:
        signature = generate_signature(webhook_payload, secret)
        request_headers["X-Webhook-Signature"] = signature
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                json=webhook_payload,
                headers=request_headers
            )
            
            success = 200 <= response.status_code < 300
            
            if success:
                logger.info(f"Webhook delivered: event={event}, url={url[:50]}..., status={response.status_code}")
            else:
                logger.warning(f"Webhook failed: event={event}, url={url[:50]}..., status={response.status_code}")
            
            return success, response.status_code, response.text[:1000]
            
    except httpx.TimeoutException:
        logger.error(f"Webhook timeout: event={event}, url={url[:50]}...")
        return False, 0, "Timeout"
        
    except httpx.RequestError as e:
        logger.error(f"Webhook request error: event={event}, url={url[:50]}..., error={str(e)}")
        return False, 0, str(e)
        
    except Exception as e:
        logger.error(f"Webhook unexpected error: event={event}, url={url[:50]}..., error={str(e)}")
        return False, 0, str(e)


def send_webhook_sync(
    url: str,
    event: str,
    payload: dict,
    secret: Optional[str] = None,
    headers: Optional[dict] = None,
    timeout: float = WEBHOOK_TIMEOUT
) -> tuple[bool, int, str]:
    """
    Webhook gÃ¶nder (sync versiyon).
    
    Returns:
        (success, status_code, response_body)
    """
    # Payload'Ä± event wrapper'a sar
    webhook_payload = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat() + "Z",
        "data": payload
    }
    
    # Headers hazÄ±rla
    request_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Gelka-Webhook/1.0",
        "X-Webhook-Event": event,
    }
    
    # Custom headers ekle
    if headers:
        request_headers.update(headers)
    
    # Ä°mza ekle (secret varsa)
    if secret:
        signature = generate_signature(webhook_payload, secret)
        request_headers["X-Webhook-Signature"] = signature
    
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                url,
                json=webhook_payload,
                headers=request_headers
            )
            
            success = 200 <= response.status_code < 300
            
            if success:
                logger.info(f"Webhook delivered: event={event}, url={url[:50]}..., status={response.status_code}")
            else:
                logger.warning(f"Webhook failed: event={event}, url={url[:50]}..., status={response.status_code}")
            
            return success, response.status_code, response.text[:1000]
            
    except httpx.TimeoutException:
        logger.error(f"Webhook timeout: event={event}, url={url[:50]}...")
        return False, 0, "Timeout"
        
    except httpx.RequestError as e:
        logger.error(f"Webhook request error: event={event}, url={url[:50]}..., error={str(e)}")
        return False, 0, str(e)
        
    except Exception as e:
        logger.error(f"Webhook unexpected error: event={event}, url={url[:50]}..., error={str(e)}")
        return False, 0, str(e)


def calculate_next_retry(attempt_count: int) -> Optional[datetime]:
    """Sonraki retry zamanÄ±nÄ± hesapla"""
    if attempt_count >= WEBHOOK_MAX_RETRIES:
        return None
    
    delay_seconds = WEBHOOK_RETRY_DELAYS[min(attempt_count, len(WEBHOOK_RETRY_DELAYS) - 1)]
    return datetime.now(UTC) + timedelta(seconds=delay_seconds)


# Event payload builders
def build_invoice_uploaded_payload(invoice_id: str, filename: str, file_size: int) -> dict:
    """invoice.uploaded event payload"""
    return {
        "invoice_id": invoice_id,
        "filename": filename,
        "file_size": file_size,
    }


def build_invoice_extracted_payload(
    invoice_id: str,
    vendor: str,
    period: str,
    consumption_kwh: Optional[float],
    total_amount: Optional[float]
) -> dict:
    """invoice.extracted event payload"""
    return {
        "invoice_id": invoice_id,
        "vendor": vendor,
        "period": period,
        "consumption_kwh": consumption_kwh,
        "total_amount": total_amount,
    }


def build_invoice_validated_payload(
    invoice_id: str,
    is_ready: bool,
    missing_fields: list[str],
    error_count: int,
    warning_count: int
) -> dict:
    """invoice.validated event payload"""
    return {
        "invoice_id": invoice_id,
        "is_ready_for_pricing": is_ready,
        "missing_fields": missing_fields,
        "error_count": error_count,
        "warning_count": warning_count,
    }


def build_offer_created_payload(
    offer_id: str,
    invoice_id: str,
    current_total: float,
    offer_total: float,
    savings_ratio: float
) -> dict:
    """offer.created event payload"""
    return {
        "offer_id": offer_id,
        "invoice_id": invoice_id,
        "current_total_tl": current_total,
        "offer_total_tl": offer_total,
        "savings_ratio": savings_ratio,
    }


def build_offer_status_changed_payload(
    offer_id: str,
    old_status: str,
    new_status: str,
    changed_by: Optional[str] = None
) -> dict:
    """offer.status_changed event payload"""
    return {
        "offer_id": offer_id,
        "old_status": old_status,
        "new_status": new_status,
        "changed_by": changed_by,
    }


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Database-backed Webhook Functions
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def create_webhook_config(
    db: Session,
    tenant_id: str,
    url: str,
    events: List[str],
    secret: Optional[str] = None,
    headers: Optional[dict] = None
) -> int:
    """
    Yeni webhook konfigÃ¼rasyonu oluÅŸtur.
    
    Returns:
        Config ID
    """
    from ..database import WebhookConfig
    
    config = WebhookConfig(
        tenant_id=tenant_id,
        url=url,
        events=events,
        secret=secret,
        headers_json=headers,
        is_active=1,
        success_count=0,
        failure_count=0,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    
    logger.info(f"Webhook config created: id={config.id}, url={url[:50]}..., events={events}")
    return config.id


def get_webhook_configs(
    db: Session,
    tenant_id: str,
    active_only: bool = True
) -> List[Any]:
    """
    Tenant'Ä±n webhook konfigÃ¼rasyonlarÄ±nÄ± getir.
    """
    from ..database import WebhookConfig
    
    query = db.query(WebhookConfig).filter(WebhookConfig.tenant_id == tenant_id)
    
    if active_only:
        query = query.filter(WebhookConfig.is_active == 1)
    
    return query.order_by(WebhookConfig.created_at.desc()).all()


def get_configs_for_event(
    db: Session,
    tenant_id: str,
    event: str
) -> List[Any]:
    """
    Belirli bir event iÃ§in aktif webhook konfigÃ¼rasyonlarÄ±nÄ± getir.
    """
    from ..database import WebhookConfig
    
    configs = db.query(WebhookConfig).filter(
        WebhookConfig.tenant_id == tenant_id,
        WebhookConfig.is_active == 1
    ).all()
    
    # Filter by event (events is a JSON array)
    return [c for c in configs if event in (c.events or [])]


async def send_webhook(
    db: Session,
    tenant_id: str,
    event_type: str,
    payload: dict
) -> List[dict]:
    """
    Event tetikle ve ilgili webhook'lara gÃ¶nder (async).
    
    Args:
        db: Database session
        tenant_id: Tenant ID
        event_type: Event tipi (Ã¶rn: "offer.accepted")
        payload: Event payload
    
    Returns:
        List of delivery results
    """
    from ..database import WebhookConfig, WebhookDelivery
    
    configs = get_configs_for_event(db, tenant_id, event_type)
    
    if not configs:
        logger.debug(f"No webhook configs for event={event_type}, tenant={tenant_id}")
        return []
    
    results = []
    
    for config in configs:
        # Webhook payload'Ä± hazÄ±rla
        webhook_payload = {
            "event": event_type,
            "timestamp": datetime.now(UTC).isoformat() + "Z",
            "data": payload
        }
        
        # Headers hazÄ±rla
        request_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Gelka-Webhook/1.0",
            "X-Webhook-Event": event_type,
        }
        
        # Custom headers ekle
        if config.headers_json:
            request_headers.update(config.headers_json)
        
        # Ä°mza ekle (secret varsa)
        if config.secret:
            signature = generate_signature(webhook_payload, config.secret)
            request_headers["X-Webhook-Signature"] = signature
        
        # Delivery kaydÄ± oluÅŸtur
        delivery = WebhookDelivery(
            webhook_config_id=config.id,
            event_type=event_type,
            payload_json=webhook_payload,
            status="pending",
            attempt_count=0,
        )
        db.add(delivery)
        db.commit()
        db.refresh(delivery)
        
        # Webhook gÃ¶nder
        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                response = await client.post(
                    config.url,
                    json=webhook_payload,
                    headers=request_headers
                )
                
                success = 200 <= response.status_code < 300
                
                # Delivery gÃ¼ncelle
                delivery.status = "success" if success else "failed"
                delivery.response_status_code = response.status_code
                delivery.response_body = response.text[:1000]
                delivery.attempt_count = 1
                if success:
                    delivery.delivered_at = datetime.now(UTC)
                
                # Config istatistiklerini gÃ¼ncelle
                if success:
                    config.success_count = (config.success_count or 0) + 1
                    logger.info(f"Webhook delivered: config_id={config.id}, event={event_type}")
                else:
                    config.failure_count = (config.failure_count or 0) + 1
                    logger.warning(f"Webhook failed: config_id={config.id}, event={event_type}, status={response.status_code}")
                
                config.last_triggered_at = datetime.now(UTC)
                db.commit()
                
                results.append({
                    "config_id": config.id,
                    "delivery_id": delivery.id,
                    "url": config.url,
                    "event": event_type,
                    "success": success,
                    "status_code": response.status_code,
                })
                
        except httpx.TimeoutException:
            delivery.status = "failed"
            delivery.error_message = "Timeout"
            delivery.attempt_count = 1
            delivery.next_retry_at = calculate_next_retry(1)
            config.failure_count = (config.failure_count or 0) + 1
            config.last_triggered_at = datetime.now(UTC)
            db.commit()
            
            logger.error(f"Webhook timeout: config_id={config.id}, event={event_type}")
            results.append({
                "config_id": config.id,
                "delivery_id": delivery.id,
                "url": config.url,
                "event": event_type,
                "success": False,
                "status_code": 0,
                "error": "Timeout"
            })
            
        except Exception as e:
            delivery.status = "failed"
            delivery.error_message = str(e)[:1000]
            delivery.attempt_count = 1
            delivery.next_retry_at = calculate_next_retry(1)
            config.failure_count = (config.failure_count or 0) + 1
            config.last_triggered_at = datetime.now(UTC)
            db.commit()
            
            logger.error(f"Webhook error: config_id={config.id}, event={event_type}, error={str(e)}")
            results.append({
                "config_id": config.id,
                "delivery_id": delivery.id,
                "url": config.url,
                "event": event_type,
                "success": False,
                "status_code": 0,
                "error": str(e)
            })
    
    return results


def trigger_webhook_sync(
    db: Session,
    tenant_id: str,
    event_type: str,
    payload: dict
) -> List[dict]:
    """
    Event tetikle ve ilgili webhook'lara gÃ¶nder (sync versiyon).
    Worker'lar iÃ§in kullanÄ±lÄ±r.
    """
    from ..database import WebhookConfig, WebhookDelivery
    
    configs = get_configs_for_event(db, tenant_id, event_type)
    
    if not configs:
        logger.debug(f"No webhook configs for event={event_type}, tenant={tenant_id}")
        return []
    
    results = []
    
    for config in configs:
        success, status_code, response = send_webhook_sync(
            url=config.url,
            event=event_type,
            payload=payload,
            secret=config.secret,
            headers=config.headers_json,
        )
        
        # Delivery kaydÄ± oluÅŸtur
        delivery = WebhookDelivery(
            webhook_config_id=config.id,
            event_type=event_type,
            payload_json={
                "event": event_type,
                "timestamp": datetime.now(UTC).isoformat() + "Z",
                "data": payload
            },
            status="success" if success else "failed",
            response_status_code=status_code,
            response_body=response[:1000] if response else None,
            attempt_count=1,
        )
        
        if success:
            delivery.delivered_at = datetime.now(UTC)
        else:
            delivery.next_retry_at = calculate_next_retry(1)
            delivery.error_message = response[:1000] if response else None
        
        db.add(delivery)
        
        # Config istatistiklerini gÃ¼ncelle
        if success:
            config.success_count = (config.success_count or 0) + 1
        else:
            config.failure_count = (config.failure_count or 0) + 1
        
        config.last_triggered_at = datetime.now(UTC)
        db.commit()
        
        results.append({
            "config_id": config.id,
            "delivery_id": delivery.id,
            "url": config.url,
            "event": event_type,
            "success": success,
            "status_code": status_code,
        })
    
    return results

