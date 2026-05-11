# Uygulama Planı: Pricing Frontend Completion

## Genel Bakış

Bu plan, `pricing-consistency-fixes` bugfix spec'inin kapatılması için gereken 4 zorunlu frontend görevini kapsar: TypeScript nullability düzeltmeleri, test dosyası tip hataları, Risk Buffer UI, Template API entegrasyonu ve build/typecheck geçiş kriteri. Tüm değişiklikler `frontend/src/` dizininde yapılır; backend değişikliği yoktur.

**Dil:** TypeScript / React (TSX)

## Görevler

- [x] 1. TypeScript tip tanımlarını güncelle ve nullability hatalarını gider
  - [x] 1.1 `api.ts` — `PricingTemplatesResponse` arayüzünü genişlet
    - Mevcut `items` dizisindeki anonim tip yerine `TemplateItem` arayüzü oluştur
    - `TemplateItem` arayüzüne `t1_pct: number`, `t2_pct: number`, `t3_pct: number`, `risk_level: string`, `risk_buffer_pct: number` alanlarını ekle
    - `PricingTemplatesResponse.items` tipini `TemplateItem[]` olarak güncelle
    - _Gereksinimler: 4.1_

  - [x] 1.2 `App.tsx` — Tüm TS18047 ("is possibly null") hatalarını gider
    - `result` değişkenine erişen tüm noktalarda optional chaining (`?.`) ekle
    - Sayısal fallback: `?? 0`, string fallback: `?? ''`, boolean fallback: `?? false`
    - Mevcut çalışma zamanı davranışını değiştirme — yalnızca tip güvenliği sağla
    - _Gereksinimler: 1.1, 1.2, 1.4_

  - [x] 1.3 `App.tsx` — TS2339 ("Property does not exist") hatalarını gider
    - Eksik alanları ilgili TypeScript arayüzüne ekle veya güvenli optional chaining ile eriş
    - `riskTemplates` state tipini `TemplateItem[]` olarak güncelle (mevcut anonim tip yerine)
    - _Gereksinimler: 1.3, 1.4_

- [x] 2. Test dosyalarındaki TypeScript tip hatalarını gider
  - [x] 2.1 jest-dom tip tanımını TypeScript yapılandırmasına ekle
    - `frontend/src/` altında `test-setup.d.ts` dosyası oluştur: `/// <reference types="@testing-library/jest-dom" />`
    - Alternatif: tsconfig.json'a `types` dizisi eklemek yerine `.d.ts` dosyası tercih et (diğer otomatik tip çözümlemelerini etkilememesi için)
    - `@testing-library/jest-dom` paketinin `devDependencies`'de mevcut olduğunu doğrula; yoksa ekle
    - _Gereksinimler: 2.1, 2.2, 5.4_

  - [x] 2.2 `market-prices/__tests__/` dizinindeki tüm TS2339 hatalarının çözüldüğünü doğrula
    - `toBeInTheDocument`, `toHaveAttribute`, `toHaveValue`, `toHaveTextContent`, `toBeDisabled` matcher'larının tip tanımlarının artık mevcut olduğunu kontrol et
    - Mevcut test davranışının değişmediğini doğrula
    - _Gereksinimler: 2.1, 2.3_

- [x] 3. Checkpoint — TypeScript derlemesini doğrula
  - `frontend/` dizininde `tsc --noEmit` çalıştır, sıfır hata olmalı
  - `tsconfig.json`'da `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true` ayarlarının korunduğunu doğrula
  - Sorun varsa kullanıcıya sor

- [x] 4. Risk Buffer UI ve Template bilgi gösterimini uygula
  - [x] 4.1 `App.tsx` — Risk Buffer hesaplama mantığını ekle
    - `baseMarginPct = (multiplier - 1) * 100` hesaplamasını ekle
    - `selectedTemplate` memo'sunu oluştur: `riskTemplates.find(t => t.name === riskTemplateName)`
    - `riskBufferPct = selectedTemplate?.risk_buffer_pct ?? 0` değerini türet
    - `recommendedMarginPct = baseMarginPct + riskBufferPct` hesaplamasını ekle
    - _Gereksinimler: 3.1, 3.2, 3.3, 4.4_

  - [x] 4.2 `App.tsx` — Risk Buffer bilgi kartını Risk Paneli içine ekle
    - "Baz Marj: %X | Risk Tamponu: %Y | Önerilen: %Z" formatında göster
    - `base_margin_pct < recommended_margin_pct` durumunda uyarı göster: "Seçilen katsayı önerilen marjın altında — risk tamponu karşılanmıyor"
    - `risk_buffer_pct = 0` durumunda "Tampon: %0 (düşük riskli profil)" göster
    - Risk Buffer kartını yalnızca `riskResult` mevcut ve şablon seçili olduğunda göster
    - _Gereksinimler: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 4.3 `App.tsx` — Template profil bilgisi gösterimini ekle
    - Şablon seçildiğinde T1/T2/T3 dağılımını "T1: %X | T2: %Y | T3: %Z" formatında göster
    - Risk seviyesini Türkçe etiketle göster: `low` → "Düşük", `medium` → "Orta", `high` → "Yüksek", `very_high` → "Çok Yüksek"
    - `RISK_LEVEL_LABELS` mapping objesini tanımla
    - Bilinmeyen risk seviyesi değerlerinde ham değeri göster (fallback)
    - _Gereksinimler: 4.2, 4.3, 4.5_

  - [ ]* 4.4 Risk Buffer hesaplama ve risk level mapping için birim testleri yaz
    - `(multiplier - 1) * 100` formülünü test et (örn: 1.15 → %15)
    - `base + buffer = recommended` formülünü test et
    - `RISK_LEVEL_LABELS` mapping'ini test et (low→Düşük, medium→Orta, high→Yüksek, very_high→Çok Yüksek)
    - _Gereksinimler: 3.1, 3.3, 4.3_

- [x] 5. Mevcut davranış korunmasını doğrula
  - [x] 5.1 Mevcut test suite'ini çalıştır ve tüm testlerin geçtiğini doğrula
    - `npm run test` (veya `npx vitest --run`) ile tüm mevcut testleri çalıştır
    - Fatura analizi, hesaplama, PDF indirme, bayi rapor akışlarının bozulmadığını doğrula
    - Dual margin hesaplama mantığının (`gross_margin_energy`, `gross_margin_total`, `net_margin`) korunduğunu doğrula
    - Risk flag UI davranışının (LOSS_RISK kırmızı banner, UNPROFITABLE_OFFER sarı uyarı) korunduğunu doğrula
    - _Gereksinimler: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 6. Final checkpoint — Build/typecheck ve test doğrulaması
  - `frontend/` dizininde `tsc --noEmit` çalıştır — çıkış kodu 0 olmalı (sıfır hata)
  - `tsconfig.json`'da `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true` korunmuş olmalı
  - Tüm mevcut testler geçmeli
  - Sorun varsa kullanıcıya sor
  - _Gereksinimler: 5.1, 5.2, 5.3_

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev ilgili gereksinimlere referans verir (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- PBT (property-based testing) bu feature için uygun değildir — değişiklikler trivial UI/config düzeyindedir
- Manuel sanity check (gerçek fatura ile) kullanıcı tarafından ayrıca yapılacaktır
