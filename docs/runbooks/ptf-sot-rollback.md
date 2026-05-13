# PTF SoT Rollback Runbook — Phase 1

**Spec:** `ptf-sot-unification` Phase 1
**Freeze tag:** `phase1-ptf-sot-freeze` @ `dee54a8`
**Baseline:** `baselines/2026-05-13_post-phase1-ptf-sot_baseline.json`

---

## Ne Zaman Bu Runbook?

Phase 1 (write lock + read dispatcher) deploy edildikten sonra üretimde:

- `/api/pricing/analyze` veya `/api/pricing/simulate` 404 / 5xx oranı yükseldi
- Müşterilere giden teklif fiyatları beklenmedik şekilde değişti (canonical hourly veriden kaynaklı)
- `hourly_market_prices` tablosu kontamine olduğundan şüphelenildi
- Acil rollback gereken herhangi bir senaryo

**Karar süresi hedefi:** ≤ 10 dakika. Bu rollback **veri kaybı** değildir; sadece okuma kaynağını eski (legacy aylık ortalama) tabloya çevirir.

---

## Rollback Mekanizması

Phase 1 mimarisi tek bir env flag ile kontrol edilir:

```
OPS_GUARD_USE_LEGACY_PTF=true
```

Bu flag aktif olduğunda:

- `_load_market_records()` → `_load_market_records_legacy()` çağırır
- Cache key namespace `ptf_source=legacy` olur — canonical cache stale dönmez
- Tüm `/api/pricing/*` endpoint'leri `market_reference_prices`'tan okur
- Legacy yazma yolları **hâlâ kapalı** (T1.5 lock); sadece okuma rollback

> ⚠️ **DİKKAT:** Legacy okuma = aylık ortalamanın 744 saate flat dağıtılması. Saatlik PTF varyasyonu yok. Bu **finansal olarak eşdeğer değil** ama acil durumlar için tolere edilebilir bir geri dönüş.

---

## Adım Adım Rollback

### 1. Karar verin

- Sorun gerçekten PTF kaynaklı mı? (Grafana panel: `ptf_legacy_fallback_total`, `ptf_drift_observed_total` Phase 2'de aktif olur — Phase 1'de logs.)
- Geri dönüş **canonical veri düzeltilene kadar** geçici çözüm; kalıcı değil.

### 2. Env flag'i ayarlayın

Production env config'inde:

```bash
OPS_GUARD_USE_LEGACY_PTF=true
```

(Docker compose / systemd / Kubernetes env'i — deployment topolojinize göre.)

### 3. **Worker'ı yeniden başlatın** — ZORUNLU

```bash
# Docker compose örneği
docker compose restart backend

# systemd örneği
systemctl restart gelka-backend
```

> ⚠️ Worker reload **şart**. `guard_config` singleton'ı load-time'da bir kez okunur. Env'i değiştirip worker'ı reload etmezseniz hiçbir şey değişmez. Tripwire test bunu kayıt altına alır: `test_kill_switch_requires_config_reload` (test_guard_config.py).

### 4. Doğrulayın

- `/api/pricing/analyze` request'i 200 dönüyor mu?
- Response'taki `weighted_ptf_tl_per_mwh` legacy değerle uyumlu mu? (eski analitik raporlarla karşılaştırın)
- Hata oranı düştü mü?

Doğrulama için manuel test:

```bash
curl -X POST http://localhost:8000/api/pricing/analyze \
  -H "Content-Type: application/json" \
  -d '{"period":"2026-03","multiplier":1.10,"dealer_commission_pct":0,
       "imbalance_params":{"forecast_error_rate":0.05,
       "imbalance_cost_tl_per_mwh":150,"smf_based_imbalance_enabled":false},
       "use_template":false,"t1_kwh":25000,"t2_kwh":12500,"t3_kwh":12500,
       "voltage_level":"og"}'
```

### 5. Sonraki adımlar

- Olayı kayda geçirin: ne oldu, ne yapıldı, ne zaman
- Canonical veriyi düzeltin (kontaminasyonu temizleyin / yeniden import)
- Doğrulama sonrası `OPS_GUARD_USE_LEGACY_PTF=false` (veya unset) → worker reload
- Post-mortem: drift log (Phase 2 aktif olduğunda) verisini analiz edin

---

## Forward Recovery (Rollback Sonrası)

Canonical veri düzeltilip canonical mode'a dönüldüğünde:

1. `OPS_GUARD_USE_LEGACY_PTF` kaldır (veya `false`)
2. Worker reload
3. Cache'in karışmaması garantili — `ptf_source=canonical` namespace ayrı
4. İlk birkaç request'i izleyin

> Mevcut analyze cache'ini boşaltmaya gerek **yok**. Cache key'i `ptf_source` boyutu içerir; canonical mode kendi namespace'inden okur.

---

## Phase 1 Garantileri (Test-Proven)

| Garanti | Test |
|---|---|
| Default canonical, switch ON legacy okur | `test_default_uses_canonical_reader`, `test_legacy_switch_uses_legacy_reader` |
| Canonical boş + switch OFF → 404 (silent fallback yok) | `test_analyze_canonical_missing_returns_409`, `test_canonical_reader_missing_returns_empty_list` |
| Cache key `ptf_source` ile namespace ayrı | `test_cache_key_namespace_changes_with_switch` |
| Switch toggle stale cache döndürmez | `test_switch_toggle_does_not_reuse_stale_cache` |
| Legacy rollback deterministic shape | `test_legacy_rollback_returns_deterministic_shape` |
| Env değişimi worker reload gerektirir (no-op mid-flight) | `test_kill_switch_requires_config_reload` |
| Manuel PTF yazma yolları kapalı | `test_manual_ptf_write_disabled.py` |
| YEKDEM eksikse analyze hard-block | `test_yekdem_hard_block.py` |

Tüm testler `phase1-ptf-sot-freeze` tag'inde yeşil.

---

## Bilinen Sınırlar

- Phase 1 rollback yalnızca **okuma** tarafıdır. Legacy yazma yolları açılmaz; legacy tabloya yeni veri ekleyemezsiniz.
- Legacy okuma flat monthly avg → saatlik PTF varyasyonunu yansıtmaz. **Geçici** çözüm.
- Phase 4'te (hard delete) bu rollback yolu silinir. O zamana kadar canonical veri kalitesi güvene alınmış olmalı.

---

## İletişim

Acil rollback ihtiyacında:

1. Bu runbook'u takip et (10 dakika hedef)
2. Olayı `#incidents` kanalına yaz (timestamp + ne yaptın)
3. Phase 2'ye geçmeden önce post-mortem yap
