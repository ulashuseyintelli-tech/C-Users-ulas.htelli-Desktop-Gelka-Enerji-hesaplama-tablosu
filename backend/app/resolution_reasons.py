"""
Resolution Reasons - Sprint 8.1 + 8.8 (Config)

Sabit resolution reason değerleri.
KPI ve metrikler için enum-like string sabitler.

KONTRAT:
- resolution_reason DB'de string saklanır ama SADECE bu değerler yazılır
- Custom text yok (gerekirse resolution_details json alanı kullanılır)
- RECLASSIFIED bir "çözüm" değil, bir "durum olayı"
"""

from .config import THRESHOLDS


class ResolutionReason:
    """
    Sabit resolution reason değerleri.
    
    Çözüm türleri (status=RESOLVED ile birlikte):
    - RECOMPUTE_RESOLVED: Recompute ile çözüldü (flags kalmadı)
    - MANUAL_RESOLVED: Manuel çözüm (operatör)
    - AUTO_RESOLVED: FALLBACK_OK ile otomatik çözüm
    
    Kapanış türleri (status=OPEN ile birlikte):
    - RECOMPUTE_LIMIT_EXCEEDED: Sonsuz döngü guard tetiklendi
    - RETRY_EXHAUSTED: 4 retry sonrası exhaust
    
    Durum olayları (status değişmez):
    - RECLASSIFIED: Primary flag değişti (çözüm değil!)
    """
    
    # Çözüm türleri (RESOLVED)
    RECOMPUTE_RESOLVED = "recompute_resolved"
    MANUAL_RESOLVED = "manual_resolved"
    AUTO_RESOLVED = "auto_resolved"
    
    # Kapanış türleri (OPEN - manual review gerekli)
    RECOMPUTE_LIMIT_EXCEEDED = "recompute_limit_exceeded"
    RETRY_EXHAUSTED = "retry_exhausted"
    
    # Durum olayları (status değişmez)
    RECLASSIFIED = "reclassified"
    
    # Tüm geçerli değerler
    ALL_VALUES = {
        RECOMPUTE_RESOLVED,
        MANUAL_RESOLVED,
        AUTO_RESOLVED,
        RECOMPUTE_LIMIT_EXCEEDED,
        RETRY_EXHAUSTED,
        RECLASSIFIED,
    }
    
    # Çözüm sayılan değerler (MTTR hesabı için)
    RESOLVED_SET = {
        RECOMPUTE_RESOLVED,
        MANUAL_RESOLVED,
        AUTO_RESOLVED,
    }
    
    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Değer geçerli mi?"""
        return value in cls.ALL_VALUES
    
    @classmethod
    def is_resolved(cls, value: str) -> bool:
        """Çözüm sayılan bir değer mi?"""
        return value in cls.RESOLVED_SET


# Stuck recovery sabitleri - NOW FROM CONFIG
# DEPRECATED: Use THRESHOLDS.Recovery.STUCK_MINUTES directly
STUCK_THRESHOLD_MINUTES = THRESHOLDS.Recovery.STUCK_MINUTES

# NOT: STUCK sadece PENDING_RECOMPUTE için geçerli
# - PENDING_RETRY: eligible_at scheduler ile yönetilir
# - REPORTED: issue pipeline ayrı
