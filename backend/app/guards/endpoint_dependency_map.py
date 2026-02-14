"""
Endpoint Dependency Map — statik endpoint→dependency eşlemesi.

Hangi endpoint şablonu hangi Dependency enum değerlerine bağlı.
Bilinmeyen endpoint → boş liste (CB pre-check'ten muaf).

Yalnızca deterministik mapping endpoint'leri burada tanımlıdır.
Koşullu dependency seçimi olan endpoint'ler eklenmez (DW-2 riski).

Feature: dependency-wrappers, Task 5
"""

from .circuit_breaker import Dependency

# Endpoint şablonu → bağımlılık listesi (statik, runtime'da değişmez)
ENDPOINT_DEPENDENCY_MAP: dict[str, list[Dependency]] = {
    # Market prices CRUD — DB primary
    "/admin/market-prices": [Dependency.DB_PRIMARY],
    "/admin/market-prices/{period}": [Dependency.DB_PRIMARY],

    # Import — DB primary + import worker
    "/admin/market-prices/import/preview": [Dependency.DB_PRIMARY, Dependency.IMPORT_WORKER],
    "/admin/market-prices/import/apply": [Dependency.DB_PRIMARY, Dependency.IMPORT_WORKER],

    # Lookup — DB replica + cache
    "/admin/market-prices/lookup": [Dependency.DB_REPLICA, Dependency.CACHE],

    # Invoice analysis — external API
    "/analyze-invoice": [Dependency.EXTERNAL_API],
    "/calculate-offer": [Dependency.DB_PRIMARY],
}


def get_dependencies(endpoint_template: str) -> list[Dependency]:
    """
    Endpoint şablonuna göre bağımlılık listesini döndür.
    Bilinmeyen endpoint → boş liste (CB pre-check'ten muaf).
    """
    return ENDPOINT_DEPENDENCY_MAP.get(endpoint_template, [])
