"""
S4 — Prospecting.

Prospect keşfi/doğrulama/qualification/Customer'a dönüştürme modülü.
PROSPECT ≠ CUSTOMER (owner kararı) — bu paket app/database.py'deki
Customer/Offer/Contract/Activity/Task tablolarını YALNIZ conversion
anında (service.convert_to_customer) ve pre-conversion dedup kontrolünde
(mevcut GET /customers?search= reuse edilerek) kullanır; hiçbir aşamada
doğrudan yazmaz.

Alt modüller:
- security.py  — SSRF-safe outbound fetch (HIGH PRIORITY, tek giriş noktası)
- normalize.py — domain/isim/telefon/email normalization
- dedup.py     — ProspectCompany + Customer dedup/identity eşleştirme
- enrichment.py — bounded website crawl + email/telefon extraction (deterministic, LLM YOK)
- discovery.py — ProspectDiscoveryProvider arayüzü + V1 provider'ları
- schemas.py   — Pydantic request/response şemaları
- service.py   — iş mantığı (CRUD, verify/qualify/disqualify/convert)
- router.py    — REST endpoint'leri (app/main.py'de include edilir)
"""
