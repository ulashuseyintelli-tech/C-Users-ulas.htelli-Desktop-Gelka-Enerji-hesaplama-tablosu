# Tasarım: Endpoint-Class Policy — Risk Sınıfına Göre Kademeli Enforcement

## Genel Bakış

Mevcut Guard Decision Layer'ın tenant-mode çözümlemesinin üzerine endpoint risk sınıfı katmanı eklenir. Tenant mode + risk class birlikte "efektif mod" üretir. Bu, rollout'u gerçek risk seviyesine göre kademelendirmeyi sağlar.

Değişen/eklenen dosyalar:
- `backend/app/guard_config.py` — yeni config alanı
- `backend/app/guards/guard_decision.py` — RiskClass enum, parse/resolve fonksiyonları, snapshot güncellemesi
- `back