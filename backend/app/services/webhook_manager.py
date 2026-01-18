"""
Webhook Manager - Database-backed webhook configuration and delivery management

Bu modül webhook konfigürasyonlarını ve delivery'leri yönetir.
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Optional, Any
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .webhook import (
    WebhookEvent,
    WebhookDeliveryStatus,
    send_webhook_sync,
    calculate_next_retry,
)

logger = logging.getLogger(__name__)


class WebhookManager:
    """
    Webhook yönetim sınıfı.
    
    Kullanım:
        manager = WebhookManager(db_session)
        await manager.trigger_event("invoice.extracted", payload, tenant_id="default")
    """
    
    def __init__(self, db_session: Optional[Any] = None):
        """
        Args:
            db_session: SQLAlchemy async session (opsiyonel - in-memory mode için None)
        """
        self.db = db_session
        self._in_memory_configs: list[dict] = []  # DB yoksa in-memory
    
    def add_config(
        self,
        url: str,
        events: list[str],
        secret: Optional[str] = None,
        headers: Optional[dict] = None,
        tenant_id: str = "default"
    ) -> dict:
        """
        Webhook konfigürasyonu ekle (in-memory mode).
        
        Returns:
            Config dict
        """
        config = {
            "id": len(self._in_memory_configs) + 1,
            "tenant_id": tenant_id,
            "url": url,
            "events": events,
            "secret": secret,
            "headers_json": headers,
            "is_active": True,
            "success_count": 0,
            "failure_count": 0,
            "created_at": datetime.now(UTC),
        }
        self._in_memory_configs.append(config)
        logger.info(f"Webhook config added: url={url[:50]}..., events={events}")
        return config
    
    def get_configs_for_event(self, event: str, tenant_id: str = "default") -> list[dict]:
        """
        Belirli bir event için aktif webhook konfigürasyonlarını getir.
        """
        return [
            c for c in self._in_memory_configs
            if c["is_active"] and c["tenant_id"] == tenant_id and event in c["events"]
        ]
    
    def trigger_event(
        self,
        event: str,
        payload: dict,
        tenant_id: str = "default"
    ) -> list[dict]:
        """
        Event tetikle ve ilgili webhook'lara gönder.
        
        Returns:
            List of delivery results
        """
        configs = self.get_configs_for_event(event, tenant_id)
        
        if not configs:
            logger.debug(f"No webhook configs for event={event}, tenant={tenant_id}")
            return []
        
        results = []
        
        for config in configs:
            success, status_code, response = send_webhook_sync(
                url=config["url"],
                event=event,
                payload=payload,
                secret=config.get("secret"),
                headers=config.get("headers_json"),
            )
            
            # Update counts
            if success:
                config["success_count"] = config.get("success_count", 0) + 1
            else:
                config["failure_count"] = config.get("failure_count", 0) + 1
            
            config["last_triggered_at"] = datetime.now(UTC)
            
            result = {
                "config_id": config["id"],
                "url": config["url"],
                "event": event,
                "success": success,
                "status_code": status_code,
                "response": response[:200] if response else None,
            }
            results.append(result)
            
            if success:
                logger.info(f"Webhook delivered: config_id={config['id']}, event={event}")
            else:
                logger.warning(f"Webhook failed: config_id={config['id']}, event={event}, status={status_code}")
        
        return results
    
    def remove_config(self, config_id: int) -> bool:
        """Webhook konfigürasyonunu kaldır"""
        for i, c in enumerate(self._in_memory_configs):
            if c["id"] == config_id:
                self._in_memory_configs.pop(i)
                logger.info(f"Webhook config removed: id={config_id}")
                return True
        return False
    
    def list_configs(self, tenant_id: str = "default") -> list[dict]:
        """Tüm webhook konfigürasyonlarını listele"""
        return [c for c in self._in_memory_configs if c["tenant_id"] == tenant_id]
    
    def get_stats(self, tenant_id: str = "default") -> dict:
        """Webhook istatistiklerini getir"""
        configs = self.list_configs(tenant_id)
        return {
            "total_configs": len(configs),
            "active_configs": sum(1 for c in configs if c["is_active"]),
            "total_success": sum(c.get("success_count", 0) for c in configs),
            "total_failure": sum(c.get("failure_count", 0) for c in configs),
        }


# Global instance (singleton pattern)
_webhook_manager: Optional[WebhookManager] = None


def get_webhook_manager() -> WebhookManager:
    """Global webhook manager instance'ı getir"""
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager()
    return _webhook_manager


def trigger_webhook(event: str, payload: dict, tenant_id: str = "default") -> list[dict]:
    """
    Convenience function - webhook event tetikle.
    
    Kullanım:
        from app.services.webhook_manager import trigger_webhook
        
        trigger_webhook("invoice.extracted", {
            "invoice_id": "123",
            "vendor": "enerjisa",
            ...
        })
    """
    manager = get_webhook_manager()
    return manager.trigger_event(event, payload, tenant_id)


def register_webhook(
    url: str,
    events: list[str],
    secret: Optional[str] = None,
    tenant_id: str = "default"
) -> dict:
    """
    Convenience function - webhook kaydet.
    
    Kullanım:
        from app.services.webhook_manager import register_webhook
        
        register_webhook(
            url="https://example.com/webhook",
            events=["invoice.extracted", "offer.created"],
            secret="my-secret-key"
        )
    """
    manager = get_webhook_manager()
    return manager.add_config(url, events, secret, tenant_id=tenant_id)
