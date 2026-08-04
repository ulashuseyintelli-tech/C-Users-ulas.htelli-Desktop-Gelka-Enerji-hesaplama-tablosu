"""
Offer yaşam döngüsü — durum geçiş kuralları + audit (tek doğruluk kaynağı).

VALID_OFFER_TRANSITIONS, main.py'deki PUT /offers/{id}/status endpoint'inin
yerel değişkeniydi; burada modül seviyesine taşındı ki hem o endpoint hem de
app/contracts/service.py aynı kuralları paylaşsın (aksi halde iki yerde ayrı
ayrı, zamanla birbirinden sapabilecek geçiş kuralı olurdu).

NOT: Bu modül webhook GÖNDERMEZ — mevcut PUT /offers/{id}/status endpoint'i
zaten webhook gönderiyor (main.py, dokunulmadı). Sözleşme akışının tetiklediği
geçişler (contracting/completed) için audit log yeterli kabul edildi; şu an
hiçbir tenant'ın kayıtlı webhook'u yok, webhook'suz bırakmak gerçek bir
davranış kaybı yaratmıyor. İleride gerekirse eklenebilir.

Çağrıldığı yerler:
- app/main.py update_offer_status() → VALID_OFFER_TRANSITIONS (kural kaynağı)
- app/contracts/service.py → transition_offer_status() (contracting/completed)
"""
from typing import Optional

from sqlalchemy.orm import Session

from .. import database as db_models
from ..models import AuditAction
from .audit import log_action

VALID_OFFER_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["sent", "expired", "contracting"],
    "sent": ["viewed", "accepted", "rejected", "expired"],
    "viewed": ["accepted", "rejected", "expired"],
    "accepted": ["contracting", "rejected"],
    "contracting": ["completed", "rejected"],
    "rejected": [],  # Terminal state
    "completed": [],  # Terminal state
    "expired": [],  # Terminal state
}


def transition_offer_status(
    db: Session,
    offer: "db_models.Offer",
    new_status: str,
    notes: Optional[str] = None,
    actor_type: str = "system",
) -> None:
    """Offer.status'u kural doğrulayarak değiştirir ve audit log yazar (webhook YOK, bkz. modül notu)."""
    old_status = offer.status
    if old_status and new_status not in VALID_OFFER_TRANSITIONS.get(old_status, [new_status]):
        raise ValueError(f"'{old_status}' -> '{new_status}' geçişi geçersiz")

    offer.status = new_status
    db.commit()

    log_action(
        db,
        AuditAction.OFFER_STATUS_CHANGED,
        tenant_id=offer.tenant_id,
        actor_type=actor_type,
        target_type="offer",
        target_id=str(offer.id),
        details={"old_status": old_status, "new_status": new_status, "notes": notes},
    )
