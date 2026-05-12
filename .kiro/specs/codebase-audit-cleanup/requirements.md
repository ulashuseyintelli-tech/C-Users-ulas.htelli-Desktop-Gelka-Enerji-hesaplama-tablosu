# Requirements — Codebase Audit & Cleanup (Gelka Enerji)

## Giriş

Gelka Enerji, iç kullanımlı bir Türk enerji fiyatlama aracıdır. Son oturumda kritik bir metodolojik hata yaşandı: YEKDEM verisinin varlığı sadece `monthly_yekdem_prices` tablosuna bakılarak değerlendirildi ve "veri yok" sonucuna varıldı. Oysa aynı veri `market_reference_prices` tablosunda 21 dönem için mevcuttu; üçüncü bir tablo olan `hourly_market_prices` da benzer türde veri tutuyordu. Üç tablo birbirinden habersizdi ve farklı modüller farklı kaynaklardan okuyordu. 20 dönem için mirror + fallback uygulanarak semptom giderildi; ancak kök neden olan **sessiz duplikasyon** problemi muhtemelen kod tabanının başka yerlerinde de mevcuttur.

Bu spec bir özellik değil bir **denetim metodolojisi ve temizleme yol haritası** üretir. Çıktı kod değil bir rapordur: `.kiro/specs/codebase-audit-cleanup/audit-report.md`. Rapor sessiz duplikasyonları, veri akış zincirlerini, canlı/ölü modülleri ve dönem bazlı veri bütünlüğünü **kanıtla** tespit eder ve P0/P1/P2/P3 önceliklendirmesiyle bir cleanup roadmap'i sunar.

Audit sırasında bulunan problemler **hibrit fix modeli** ile ele alınır: küçük ve geri alınabilir bulgular agent tarafından inline düzeltilir; büyük veya mimari değişim gerektiren bulgular kullanıcıya rapor edilir ve onayla ilerlenir.

## Roller

- **Audit Agent**: Kanıt toplayan, duplikasyon tespit eden, bulguları sınıflandıran otomatik aktör
- **Sistem Mühendisi (kullanıcı)**: Büyük bulgularda karar verici
- **Gelecek Kiro Oturumları**: Raporu tüketen, cleanup görevlerini uygulayan

## Kısaltmalar

- **EARS**: Easy Approach to Requirements Syntax (WHEN/WHILE/WHERE/IF ... THEN ... SHALL ...)
- **SoT**: Source of Truth (canonical veri kaynağı)
- **P0..P3**: Öncelik seviyeleri (P0 en kritik, P3 kozmetik)

---

## Requirement 1 — Kanıt Standardı Zorunluluğu

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit raporundaki her bulgunun kanıta dayanması gerekir ki bulgular tartışmasız kabul edilebilir olsun ve önceki "YEKDEM yok" tipi yüzeysel analiz hataları tekrarlanmasın.

### Acceptance Criteria

1. WHEN audit agent bir bulgu raporladığında THEN bulgu SHALL en az bir kanıt türü içermeli: (a) `dosya:satır` referansı, (b) çalıştırılmış SQL sorgusu + ham çıktı, (c) canlı API çağrısı + tam HTTP response.
2. IF bir bulgu yalnızca çıkarım veya sezgiye dayanıyorsa THEN bulgu rapora dahil edilmeyecek OR "hipotez" olarak işaretlenip ayrı bir bölüme yazılacak SHALL.
3. WHEN kanıt bir SQL sorgusu ise THEN rapor SHALL hem sorgunun kendisini hem de döndürdüğü satır sayısını ve ilk 3-20 satırını içermeli.
4. WHEN kanıt bir API çağrısı ise THEN rapor SHALL HTTP method, URL, request body ve response status + body'yi içermeli.
5. IF rapor "tahmin", "muhtemelen", "sanırım", "olabilir" gibi belirsizlik ifadeleri içeriyorsa THEN bu bulgu SHALL "düşük güven" olarak etiketlenmeli ve kanıt eksikliği açıkça belirtilmeli.

---

## Requirement 2 — Veritabanı Tablolarının Yazıcı/Okuyucu Haritası

**Kullanıcı Hikayesi:** Bir audit agent olarak, her DB tablosu için "kim yazıyor, kim okuyor" haritasını kanıtla çıkarmak istiyorum ki şizofrenik tablolar (farklı modüllerin farklı anlamlarla yazdığı) tespit edilebilsin.

### Acceptance Criteria

1. WHEN audit çalıştığında THEN agent SHALL `gelka_enerji.db` içindeki tüm tabloları `sqlite_master` sorgusuyla listelemeli ve her biri için satır sayısını kaydetmeli.
2. WHEN bir tablo incelendiğinde THEN agent SHALL o tabloyu INSERT/UPDATE eden tüm Python modüllerini (grep `FROM tablo_adı` + `INTO tablo_adı` + ORM model referansları) dosya:satır referansıyla listelemeli.
3. WHEN bir tablo incelendiğinde THEN agent SHALL o tabloyu SELECT eden tüm modülleri aynı kanıt standardıyla listelemeli.
4. IF bir tablo birden fazla modül tarafından yazılıyorsa AND yazılan kolon kümeleri farklıysa THEN agent SHALL bu tabloyu "potansiyel şizofrenik tablo" olarak işaretlemeli.
5. WHEN tablo haritası tamamlandığında THEN her satırda {tablo_adı, satır_sayısı, yazıcılar, okuyucular, notlar} içeren bir matris audit-report.md'ye eklenmeli SHALL.

---

## Requirement 3 — Sessiz Duplikasyon Tespiti ve SoT Ataması

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, aynı iş sorumluluğu için birden fazla kaynak (tablo, modül, fonksiyon) kullanan durumları tespit etmek ve her biri için tek bir "doğru kaynak"a karar vermek istiyorum.

### Acceptance Criteria

1. WHEN iki veya daha fazla tablo aynı domain verisini (PTF, YEKDEM, dağıtım tarifesi, tüketim profili vb.) tutuyorsa THEN agent SHALL bunu "sessiz duplikasyon" olarak raporlamalı ve dönem kesişimi ile değer farklılıklarını SQL çıktısıyla göstermeli.
2. WHEN iki veya daha fazla backend fonksiyonu aynı hesaplamayı (örn. KDV, YEKDEM, toplam maliyet) yapıyorsa THEN agent SHALL her iki fonksiyonun imzasını, çağrılan yerleri ve en az bir örnek girdi için çıktı farkını raporlamalı.
3. WHEN bir hesaplama hem backend'de hem frontend'de tekrar ediyorsa THEN agent SHALL iki implementasyonu kod bloku olarak raporda yan yana göstermeli ve aynı girdiyle çıktı farkını test etmeli.
4. FOR EACH tespit edilen duplikasyon THEN rapor SHALL "Önerilen SoT" alanında hangi kaynağın canonical kabul edilmesi gerektiğini gerekçesiyle (satır sayısı, güncellik, kullanıcı sayısı, referans miktarı) belirtmeli.
5. IF SoT ataması belirsizse THEN bulgu SHALL "user-decision" (kullanıcı onayı) olarak işaretlenmeli ve en az iki seçenek artı/eksi listesiyle sunulmalı.

---

## Requirement 4 — Uçtan Uca Veri Akış Haritası

**Kullanıcı Hikayesi:** Bir audit agent olarak, her kritik iş akışı için Frontend fetch → Backend endpoint → DB tablosu zincirini kanıtla çıkarmak istiyorum ki "hangi ekran hangi tabloyu kullanıyor" sorusu tartışmasız cevaplanabilsin.

### Acceptance Criteria

1. WHERE kritik iş akışları listesi (manuel fiyat hesaplama, risk analizi, fatura yükleme, teklif PDF üretimi, admin PTF/YEKDEM yönetimi, dağıtım tarifesi seçimi) tanımlıdır THE agent SHALL her biri için tam zinciri çıkarmalı.
2. WHEN bir akış izlendiğinde THEN agent SHALL (a) frontend'deki fetch çağrısını `dosya:satır` ile, (b) çağrılan backend endpoint'in tanımını `dosya:satır` ile, (c) endpoint içinde okunan DB tablolarını SQL seviyesinde raporlamalı.
3. WHEN mümkünse agent SHALL her akışı gerçek backend'e canlı bir HTTP isteği göndererek doğrulamalı ve response'u rapora eklemeli.
4. IF bir frontend fetch çağrısı hiçbir backend endpoint'i ile eşleşmiyorsa THEN bu "kırık bağ" olarak raporlanmalı.
5. IF bir backend endpoint hiçbir frontend fetch'inden çağrılmıyorsa THEN bu "çağrılmayan endpoint" olarak Requirement 8 kapsamında işaretlenmeli.

---

## Requirement 5 — Canlı vs Ölü Modül Tespiti

**Kullanıcı Hikayesi:** Bir audit agent olarak, `backend/app/main.py`'den başlayan import zincirini çıkararak hangi modüllerin üretim yolunda olduğunu, hangilerinin sadece kendi testleri tarafından import edildiğini kanıtla belirlemek istiyorum.

### Acceptance Criteria

1. WHEN modül analizi çalıştığında THEN agent SHALL `backend/app/main.py`'yi kök kabul edip transitive import kapanışını çıkarmalı (lazy import'lar dahil).
2. WHEN bir modül import kapanışında DEĞİLSE AND sadece `backend/tests/` altındaki dosyalar tarafından import ediliyorsa THEN bu "test-only / dead production path" olarak raporlanmalı.
3. WHEN bir modül hiçbir dosya tarafından import edilmiyorsa THEN "orphan" olarak raporlanmalı SHALL.
4. WHERE `guard_config.py` veya benzeri feature flag'ler `False` default'luysa THE flag'e bağlı modüller "dormant" olarak ayrı listelenmeli SHALL.
5. FOR EACH dormant/dead modül THEN rapor SHALL satır sayısını, son commit tarihini ve önerilen eylemi (sil / arşivle / üretime bağla) içermeli.

---

## Requirement 6 — Frontend–Backend Hesaplama Tutarlılığı

**Kullanıcı Hikayesi:** Bir audit agent olarak, aynı hesaplamanın (KDV, YEKDEM dahil birim fiyat, ters hesap, dengesizlik, net marj) hem frontend'de hem backend'de yapıldığı durumları tespit edip aynı girdiyle aynı çıktıyı verdiğini kanıtlamak istiyorum.

### Acceptance Criteria

1. WHEN bir hesaplama fonksiyonu backend'de tespit edildiğinde THEN agent SHALL frontend'de aynı matematiksel dönüşümü yapan kod bloğunu aramalı (grep + semantic).
2. IF iki implementasyon bulunursa THEN agent SHALL en az 3 temsili girdi için her iki tarafı çalıştırıp çıktı farkını ondalık basamak düzeyinde karşılaştırmalı.
3. WHEN fark bulunursa THEN bu P0 olarak sınıflandırılmalı SHALL çünkü fiyatlama çıktısını doğrudan etkiler.
4. IF hesaplama sadece frontend'de yapılıyorsa AND backend'de yoksa THEN rapor SHALL bunu "tek taraflı hesaplama" olarak işaretlemeli ve backend'e taşıma önerisini eklemeli.
5. WHERE frontend'de hardcoded tarife, katsayı veya fiyat değeri varsa THE agent SHALL bunu listeleyip backend karşılığıyla karşılaştırmalı.

---

## Requirement 7 — Dönem Bazlı Veri Bütünlüğü

**Kullanıcı Hikayesi:** Bir audit agent olarak, PTF / YEKDEM / dağıtım tarifesi / perakende tarifesi gibi dönem bazlı verilerin her dönem için eksiksiz mevcut olduğunu kontrol etmek istiyorum ki "dönem seçildi ama veri yok" senaryoları önceden görülebilsin.

### Acceptance Criteria

1. WHERE dönem bazlı veri tutan tablolar (PTF, YEKDEM, dağıtım tarifesi, perakende tarifesi, tüketim profili) listelenmiştir THE agent SHALL her tablo için mevcut dönemleri (period) listelemeli.
2. WHEN uygulanabilir dönem aralığı (örn. 2025-01..2026-12) tanımlandığında THEN agent SHALL her kaynakta eksik dönemleri SQL ile tespit edip tabloya dökmeli.
3. IF bir dönem sadece bir kaynakta varsa AND ilgili iş akışı diğer kaynağı okuyorsa THEN bu "görünmez eksik" olarak P0/P1 işaretlenmeli.
4. WHEN aynı dönem için farklı kaynaklarda farklı değerler varsa THEN agent SHALL farkları |fark| > 0 olan tüm dönemler için raporlamalı.
5. FOR EACH eksik dönem THEN rapor SHALL veri eksikliği bir hesaplama bloğuna neden olup olmadığını ("YEKDEM yok → analiz 0 YEKDEM ile devam ediyor" vb.) belirtmeli.

---

## Requirement 8 — Endpoint Çağrılma Durumu

**Kullanıcı Hikayesi:** Bir audit agent olarak, backend'deki tüm FastAPI endpoint'lerinin frontend'den gerçekten çağrılıp çağrılmadığını kanıtla belirlemek istiyorum.

### Acceptance Criteria

1. WHEN agent çalıştığında THEN FastAPI decorator'larını (`@app.get`, `@app.post`, `@router.*`) tarayıp tüm endpoint'leri (method + path) listelemeli SHALL.
2. WHEN endpoint listesi çıktıktan sonra THEN agent SHALL frontend'de `fetch(`, `axios`, `api.ts` çağrılarını tarayıp her endpoint için kullanım sayısını çıkarmalı.
3. IF bir endpoint hiçbir frontend dosyasından çağrılmıyorsa AND sadece test'lerden çağrılıyorsa THEN "ölü endpoint" olarak işaretlenmeli.
4. IF endpoint hiç çağrılmıyorsa (test dahil) THEN "orphan endpoint" olarak P2 işaretlenmeli SHALL.
5. WHEN hem frontend'den çağrılıyor hem backend'de mevcutsa THEN "canlı endpoint" olarak eşleşme tablosuna eklenmeli SHALL.

---

## Requirement 9 — 38 Spec'in Implementasyon Durumu

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, `.kiro/specs/` altındaki her spec'in gerçekten implementasyona dökülüp dökülmediğini, hangi spec'lerin yarım bırakıldığını görmek istiyorum.

### Acceptance Criteria

1. WHEN agent çalıştığında THEN `.kiro/specs/` altındaki tüm alt klasörleri listelemeli ve her biri için requirements/design/tasks/bugfix dosyalarının varlığını tablolamalı SHALL.
2. WHEN bir spec'in `tasks.md`'si varsa THEN agent SHALL tamamlanmış (`[x]`) / açık (`[ ]`) task sayılarını saymalı.
3. IF bir spec'in tasks.md'si %0 tamamlandıysa THEN "başlanmamış" olarak işaretlenmeli SHALL.
4. IF bir spec %1..%99 arasındaysa THEN "yarım" olarak işaretlenmeli; kalan task'ların özeti rapora çıkarılmalı.
5. WHEN bir spec mimarinin kullanılmayan bir alanını kapsıyorsa (örn. SLO, chaos, governance — gerçek kullanıcı sayısı ile orantısız) THEN "spec enflasyonu" kategorisine alınıp arşivleme önerisi yapılmalı SHALL.

---

## Requirement 10 — Hibrit Fix Modeli: Inline Fix Kriterleri

**Kullanıcı Hikayesi:** Bir audit agent olarak, küçük ve geri alınabilir bulguları **anında** kullanıcı onayı beklemeden düzeltmek istiyorum ki raporun kendisi zaten temizlenmiş bir kod tabanına karşı yazılsın.

### Acceptance Criteria

1. A bulgu **tümü** sağlanırsa ancak o zaman inline-fix olarak sınıflandırılır SHALL:
   (a) tek dosyada değişiklik yapılacak,
   (b) davranışı değiştirmeyecek veya değiştirirse net bug'dan net doğruya geçiş olacak,
   (c) public API / endpoint imzasını değiştirmeyecek,
   (d) DB şeması / migration gerektirmeyecek,
   (e) mevcut test suite'i kırmayacak (agent değişiklikten sonra ilgili testleri çalıştırmalı).
2. WHEN bir bulgu inline-fix kriterlerini karşıladığında THEN agent SHALL düzeltmeyi yapıp raporda "inline-fix applied" bölümüne {dosya, diff özeti, çalışan test} bilgisi yazmalı.
3. WHEN inline-fix sonrası testler kırılırsa THEN agent SHALL değişikliği geri almalı AND bulguyu "user-decision"a yükseltmeli.
4. WHERE örnekler tanımlıdır THE inline-fix kapsamında olanlar: hardcoded tarife ≠ DB değeri düzeltmesi, unused import silme, typo fix, tek satır fallback ekleme, deprecated alias kaldırma.
5. FOR EACH inline-fix THEN rapor SHALL "geri alma" komutunu (örn. `git revert <sha>`) açıkça belirtmeli.

---

## Requirement 11 — Hibrit Fix Modeli: User-Decision Eskalasyon Kriterleri

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, mimari etkisi olan veya davranış değişimi riski taşıyan bulguların **kararımı beklemeden uygulanmamasını** istiyorum ki sessizce kritik değişiklikler yapılmasın.

### Acceptance Criteria

1. A bulgu **herhangi biri** geçerliyse user-decision olarak sınıflandırılır SHALL:
   (a) birden fazla dosyada değişiklik,
   (b) DB şeması / migration / tablo silme,
   (c) public API / endpoint imzası değişimi,
   (d) iki SoT seçeneği arası tercih,
   (e) >200 satırlık kod silme,
   (f) frontend → backend hesaplama taşıma.
2. WHEN bulgu user-decision ise THEN rapor SHALL en az iki alternatif + her birinin artı/eksi listesini sunmalı.
3. WHEN bulgu user-decision ise THEN rapor SHALL önerilen eylemin "blast radius" (etkilenen dosya/endpoint/kullanıcı akışı) tahminini içermeli.
4. IF agent bir user-decision bulgusunda eylem almak isterse THEN rapor üzerinden kullanıcı onayı alınana kadar değişiklik yapılmayacak SHALL.
5. FOR EACH user-decision bulgusu THEN rapor SHALL "Önerilen Plan" başlığı altında adım adım bir uygulama senaryosu sunmalı.

---

## Requirement 12 — Öncelik Sınıflandırması (P0/P1/P2/P3)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, bulguları iş etkisine göre sıralı görmek istiyorum ki sınırlı zamanda en çok değeri üreten temizliklere odaklanabileyim.

### Acceptance Criteria

1. P0 IS DEFINED AS üretimde yanlış fiyat/yanlış fatura/yanlış rapor üreten veya güvenlik riski oluşturan bulgular SHALL.
2. P1 IS DEFINED AS sessiz duplikasyon, aktif iki SoT, yarım implementasyon, yakın vadede P0 üretebilecek mimari riskler SHALL.
3. P2 IS DEFINED AS dead code, orphan endpoint, spec enflasyonu, maintenance yükü SHALL.
4. P3 IS DEFINED AS kozmetik (naming, docs, formatting) SHALL.
5. FOR EACH bulgu THEN rapor SHALL öncelik atamasının kısa gerekçesini (1-2 cümle) ve etkilenen iş akışını belirtmeli.

---

## Requirement 13 — Cleanup Roadmap Çıktısı

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit raporunun sonunda hangi sırayla hangi temizliklerin yapılacağını gösteren uygulanabilir bir yol haritası görmek istiyorum.

### Acceptance Criteria

1. WHEN audit tamamlandığında THEN rapor SHALL P0 → P3 sırasında bulguları listeleyen bir roadmap bölümü içermeli.
2. FOR EACH roadmap maddesi THEN {başlık, öncelik, tahmini efor (S/M/L), bağımlılıklar, ilgili bulgu ID'leri} alanları dolu olmalı SHALL.
3. WHEN bir roadmap maddesi başka bir maddeyi gerektiriyorsa THEN bağımlılık açıkça belirtilmeli (örn. "M7 — M3'ten sonra").
4. WHEN roadmap yazıldığında THEN her P0 maddesi için "bu yapılmazsa ne kötü olur" 1 cümlelik risk notu eklenmeli SHALL.
5. IF bir bulgu inline-fix olarak zaten çözüldüyse THEN roadmap'te "✅ uygulandı" olarak gösterilip roadmap sayım dışı bırakılmalı SHALL.

---

## Requirement 14 — Rapor Formatı ve Yapısı

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit raporunun tutarlı, taranabilir ve diff'lenebilir olmasını istiyorum ki gelecek audit'lerle karşılaştırma yapılabilsin.

### Acceptance Criteria

1. THE rapor dosyası `.kiro/specs/codebase-audit-cleanup/audit-report.md` yolunda olmalı SHALL.
2. THE rapor şu bölümleri sırayla içermeli SHALL: (1) Yönetici özeti, (2) Metodoloji ve kanıt standardı, (3) DB tablosu haritası, (4) Veri akış haritası, (5) Sessiz duplikasyon bulguları, (6) Canlı/ölü modül haritası, (7) Frontend-backend tutarlılık, (8) Dönem bütünlüğü, (9) Endpoint çağrılma durumu, (10) Spec implementasyon durumu, (11) Inline-fix log, (12) User-decision bekleyen bulgular, (13) Cleanup roadmap.
3. WHEN bir bulgu raporlandığında THEN format SHALL `### F<numara> — <başlık>` + {Öncelik, Tip (inline/user-decision), Kanıt, Bulgu, Öneri} alanlarını içermeli.
4. THE rapor SHALL Türkçe yazılmalı; teknik kısaltmalar ve kod/SQL bloğu içerikleri orijinal dilinde bırakılabilir.
5. WHEN rapor tamamlandığında THEN dosyanın başında {oluşturma tarihi, commit sha, toplam bulgu sayısı, P0/P1/P2/P3 dağılımı} metadata bloğu olmalı SHALL.

---

## Requirement 15 — Audit Pipeline Tekrarlanabilirliği

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit'in bir kerelik değil, gelecekte kolayca yeniden çalıştırılabilir olmasını istiyorum ki code tabanı büyüdükçe yeni sessiz duplikasyonlar tespit edilebilsin.

### Acceptance Criteria

1. WHEN audit adımları tanımlandığında THEN design.md SHALL her adım için (a) komut veya SQL, (b) beklenen çıktı formatı, (c) başarı kriteri tanımlamalı.
2. WHEN audit tekrar çalıştırıldığında THEN rapor yeni çalışma ile eskisini karşılaştıracak bir "diff" bölümü üretebilir olmalı SHALL (yeni bulgular, çözülen bulgular).
3. WHERE audit scriptleri tanımlanıyorsa THE scriptler `.kiro/specs/codebase-audit-cleanup/scripts/` altında tutulmalı SHALL AND prod kod tabanını kirletmemeli.
4. IF bir audit adımı elle çalıştırma gerektiriyorsa (örn. canlı API çağrısı) THEN gereksinim rapora "manuel adım" olarak yazılmalı SHALL.
5. THE audit çalışması 60 dakikadan kısa sürmeli OR süre aşımında bölümlenebilir olmalı SHALL.

---

## Requirement 16 — Güvenlik ve Yan Etki Sınırları

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit agent'ının DB'yi bozmamasını, kritik veri silmemesini ve production'ı etkilememesini istiyorum.

### Acceptance Criteria

1. WHEN agent SQL çalıştıracağında THEN sadece `SELECT` / `PRAGMA` / `EXPLAIN` izinli olmalı SHALL; `DROP`, `DELETE`, `TRUNCATE`, `ALTER` kullanıcı onayı olmadan çalıştırılamayacak.
2. WHERE audit için geçici dosya gerekiyorsa THE dosyalar `.kiro/specs/codebase-audit-cleanup/tmp/` altında tutulmalı SHALL AND audit bitiminde temizlenmeli.
3. IF agent `.db` dosyasını değiştirme ihtiyacı duyarsa (örn. veri mirror) THEN bu inline-fix DEĞİL user-decision olarak sınıflandırılmalı SHALL.
4. WHEN agent canlı API çağrıları yapacağında THEN sadece GET metodu idempotent endpoint'ler üzerinde izinli olmalı SHALL; POST/PUT/DELETE audit adımları kullanıcı onayı gerektirir.
5. IF agent `.env` veya gizli anahtar içeren bir dosya okursa THEN raporda içerik değil sadece anahtar isimleri referanslanmalı SHALL.

---

## Requirement 17 — Kapsam Dışı (Explicit Non-Goals)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit'in neyi **yapmayacağını** da net görmek istiyorum ki scope creep olmasın.

### Acceptance Criteria

1. THE audit SHALL yeni özellik geliştirmez, sadece mevcut durumu denetler.
2. THE audit SHALL performance tuning / query optimization önerisi üretmez (ayrı bir spec konusu).
3. THE audit SHALL UI/UX iyileştirme önerisi üretmez.
4. THE audit SHALL iş kuralı (fiyatlama formülü, KDV oranı, vs.) doğruluğunu denetlemez; yalnızca **kaynak tutarlılığını** denetler. "%18 KDV doğru mu?" değil, "KDV hesaplaması iki yerde aynı mı?" sorusu kapsamdadır.
5. THE audit SHALL test coverage ölçümü yapmaz; yalnızca test-only modülleri tespit eder.

---

## Requirement 18 — Tamamlama Kriterleri (Definition of Done)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit'in "bitti" diyebilmesi için hangi koşulların sağlanması gerektiğini net görmek istiyorum.

### Acceptance Criteria

1. THE audit "tamamlandı" sayılabilmesi için Requirement 2–9 kapsamındaki 8 denetim alanının her biri için en az bir kanıtlı bulgu veya "bu alanda bulgu yok" teyidi raporda yer almalı SHALL.
2. THE audit-report.md dosyası mevcut AND metadata bloğu dolu olmalı SHALL.
3. THE inline-fix log bölümü (0 uygulandıysa bile) mevcut olmalı SHALL.
4. THE cleanup roadmap bölümü en az 1 P0, 1 P1 maddesi içermeli OR yoksa gerekçesi yazılmalı SHALL.
5. WHEN audit tamamlandığında THEN kullanıcıya özet mesaj olarak {toplam bulgu, P0/P1/P2/P3 dağılımı, inline-fix sayısı, bekleyen user-decision sayısı} sunulmalı SHALL.

---

## Requirement 19 — Cache Audit ve Versioned Cache Invalidation

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, fiyatlama/analiz cache'lerinin stale veri servis etmediğinden emin olmak istiyorum çünkü önceki YEKDEM düzeltmesinde `analysis_cache` eski yanlış sonucu gösterdi ve ancak manuel `DELETE` ile temizlendi.

### Acceptance Criteria

1. WHEN audit çalıştığında THEN agent SHALL sistemdeki tüm cache katmanlarını listelemeli: (a) backend in-memory cache (LRU, dict, functools.lru_cache), (b) DB-backed cache (`analysis_cache`, `price_change_history` benzeri), (c) frontend cache (localStorage, sessionStorage, React Query cache, SWR), (d) PDF artifact cache (`pdf_artifact_store`), (e) HTTP response cache (middleware / CDN katmanı).
2. FOR EACH cache katmanı THEN rapor SHALL {cache_tipi, key şeması, TTL, invalidation tetikleyici, bağlı olduğu SoT veri kaynağı} alanlarını içermeli.
3. IF bir cache key'i altında yatan veri (PTF, YEKDEM, tarife, katsayı) version/hash'ini içermiyorsa THEN bu P0 bulgu olarak raporlanmalı SHALL çünkü stale cache silent wrong-output üretir.
4. WHEN kaynak veri güncellendiğinde (INSERT/UPDATE ilgili tabloya) THEN bağlı cache entry'leri otomatik geçersiz olmalı SHALL; manuel `DELETE FROM cache` gerektiren akışlar "P0 invalidation bug" olarak işaretlenmeli.
5. THE önerilen cache key formatı SHALL şunu içermeli: `<domain>:<period>:<input_hash>:<source_version>` — böylece kaynak veri version'ı değiştiğinde eski key doğal olarak miss olur.

---

## Requirement 20 — Tek Doğruluk Kaynağı (SoT) Matrisi

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, hangi veri parçasının hangi kaynakta "canonical" olduğunu tek bir matriste görmek istiyorum ki gelecek geliştirmede "bu veri nereden?" sorusu tartışmasız cevaplansın ve yeni sessiz duplikasyonlar önlensin.

### Acceptance Criteria

1. WHEN audit tamamlandığında THEN rapor SHALL şu alanları içeren bir SoT matrisi üretmeli: {veri_adı, canonical_kaynak (tablo + kolon VEYA modül + fonksiyon), yazıcı, okuyucular, deprecated_kaynaklar, geçiş_durumu (canonical/mirror/legacy/orphan)}.
2. THE matris EN AZ şu veri öğelerini kapsamalı SHALL: PTF (saatlik + aylık ağırlıklı), YEKDEM (aylık), dağıtım tarifesi (dönemsel + abone grubu), perakende tarifesi, BTV oranı, KDV oranı, bayi payı/komisyon, ters hesaplama katsayısı, tüketim profilleri.
3. FOR EACH SoT seçimi THEN rapor SHALL "niyet analizi" içermeli: (a) ilgili tabloyu oluşturan migration/commit, (b) tablonun amacı — "legacy dump" mı "yeni canonical" mi belirsizse git log kanıtı, (c) SoT seçim gerekçesi.
4. IF iki kaynaktan hangisinin canonical olacağı belirsizse THEN bulgu user-decision olarak işaretlenmeli AND her iki seçenek için migration maliyeti + okuyucu sayısı + veri eksiksizliği karşılaştırması sunulmalı.
5. WHERE SoT seçildikten sonra THE canonical olmayan kaynaklar için "deprecation planı" (kalsın/yönlendirsin/silinsin) raporda belirtilmeli SHALL.

---

## Requirement 21 — FE/BE Hesap Input Parametre Eşleşmesi

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, aynı hesaplamaya giren parametrelerin frontend ve backend tarafında birebir aynı anlam ve değerleri taşıdığını doğrulamak istiyorum çünkü input drift çıktı drift'ine yol açar ve çoğunlukla sessizce gerçekleşir.

### Acceptance Criteria

1. WHEN aynı hesaplama FE ve BE'de yapılıyorsa THEN agent SHALL her iki tarafın input parametre listesini çıkarıp ad-ad eşleştirmeli (örn: FE `ptfPrice` ↔ BE `weighted_ptf`, FE `yekdemPrice` ↔ BE `yekdem_tl_per_mwh`).
2. IF bir parametre sadece bir tarafta varsa OR isimler farklı anlamlar taşıyorsa THEN "parametre drift" olarak raporlanmalı SHALL.
3. FOR EACH eşleşmeyen parametre THEN rapor SHALL default değer farkını, birim farkını (TL/MWh vs kr/kWh vs TL/kWh) ve dönüşüm faktörünü kanıtla göstermeli.
4. WHEN FE hardcoded bir değer kullanıyorsa (tarife, katsayı, fallback) THEN agent SHALL BE'deki canonical değerle karşılaştırıp farkı P0 işaretlemeli.
5. THE nihai eşleşme tablosu audit raporunda "Input Matching Matrix" başlığı altında her hesaplama akışı için ayrı bölüm olmalı SHALL.

---

## Requirement 22 — Hesap Sonucu Drift Testi (Golden Baseline)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, cleanup sırasında veya sonrasında hesaplama sonuçlarının sessizce değişmediğinden emin olmak istiyorum çünkü "temizlik" yaparken yanlışlıkla fiyat formülünü bozmak en tehlikeli senaryodur.

### Acceptance Criteria

1. BEFORE cleanup başlamadan ÖNCE audit agent SHALL temsili bir dönem seti için (örn: 2025-12, 2026-01, 2026-02, 2026-03, 2026-04) mevcut durumun çıktılarını "golden baseline" olarak kaydetmeli.
2. THE baseline EN AZ şunları içermeli SHALL: (a) manuel mod hesap sonucu, (b) backend `/full-process` response'u, (c) risk analizi (`pricing/analyze`) çıktısı, (d) teklif PDF'inin sayısal alanları, (e) faturadan geri çözümlenen birim fiyat.
3. AFTER herhangi bir inline-fix veya user-decision cleanup uygulandığında THEN agent SHALL aynı dönemler için aynı input'larla yeniden çalıştırıp baseline ile karşılaştırmalı.
4. IF çıktılarda 0.01 (1 kuruş) toleransını aşan fark bulunursa THEN cleanup SHALL durdurulmalı, değişiklik geri alınmalı AND rapor P0 bulgu olarak işaretlenmeli.
5. WHEN drift testi başarılı (fark ≤ tolerans) ise THEN rapor SHALL her dönem için {input_hash, baseline_output, post_cleanup_output, diff} satırını içeren "Drift Test Log" bölümüne sonuçları yazmalı.
6. THE golden baseline JSON formatında `.kiro/specs/codebase-audit-cleanup/baselines/` altında commit edilmeli SHALL ki gelecekteki cleanup turları da referans alabilsin.

---

## Requirement 23 — Şema-Gerçeklik Drift Ön Kontrolü

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, audit başlamadan önce canlı DB şemasının Alembic migration HEAD'i ile senkronize olduğundan emin olmak istiyorum çünkü manuel `ALTER TABLE` veya kayıp migration varsa audit'in okuduğu şema "gerçek" değildir ve bulgular yanıltıcı olur.

### Acceptance Criteria

1. BEFORE audit başlamadan önce agent SHALL `alembic current` çalıştırıp mevcut migration revision'ını kaydetmeli.
2. WHEN agent SHALL `alembic heads` ile karşılaştırıp current = head olduğunu doğrulamalı; eşit değilse audit başlamadan önce kullanıcıya bildirmeli.
3. THE agent SHALL canlı DB şemasını (`sqlite_master` + `PRAGMA table_info`) Alembic model tanımlarıyla kolon-kolon karşılaştırmalı; fark varsa "schema drift" olarak raporlanmalı.
4. IF schema drift tespit edilirse THEN audit SHALL drift giderilene kadar duraklamalı (kullanıcı onayıyla "drift kabul edilmiş durumu denetle" modu açılabilir).
5. THE drift raporu şu formatta olmalı SHALL: {tablo, eksik_kolon_modelde, fazla_kolon_dbde, farklı_tip_tanımı, önerilen_migration_komutu}.

---

## Ek Yapısal Prensipler (Kesitsel — Tüm Requirement'lara Uygulanır)

### P-A: Prevention Steering
`.kiro/steering/source-of-truth.md` dosyası oluşturulacak ve `inclusion: always` olarak işaretlenecek SHALL. Bu dosya R20'de üretilen SoT matrisinin özetini, her yeni agent oturumunun otomatik göreceği formda içerecek. Amaç: gelecek oturumların "YEKDEM nerede?" sorusunu sorarken yanlış tabloya bakmasını önlemek.

### P-B: Audit Kapsam Sınırı
Audit kapsam olarak şunlarla sınırlı SHALL: fiyatlama çıktısına dokunan akışlar (invoice, offer, pricing/risk, admin/market-prices, admin/distribution-tariffs). SLO, chaos, governance, telemetry spec'leri için **yalnızca** "main.py'den import zincirinde var mı, endpoint canlı mı?" seviyesinde binary check yapılır; iç mantıkları denetlenmez.

### P-C: Acil Durum Exit
Audit sırasında aktif üretimde yanlış fiyat üreten bir bulgu tespit edilirse audit duraklatılacak, bulgu P0 olarak inline-fix veya user-decision protokolüyle giderilecek, sonra audit kaldığı yerden devam edecek SHALL. Completeness, correctness'tan sonra gelir.

### P-D: Invariant Testleri (Post-Audit Yaşayan Guard)
Audit bittikten sonra şu testler `backend/tests/test_invariants.py` altında CI'da sürekli koşacak SHALL:
1. FE hesap = BE hesap (tolerance 0.01) — aynı dönem + aynı input için
2. SoT haricinde YEKDEM/PTF/tarife yazan yeni kod yok (grep guard)
3. Cache key'ler source version suffix içermek zorunda (regex guard)

---

## Requirement 24 — Paralel Hesap Yolu Tespiti

**Kullanıcı Hikayesi:** Bir audit agent olarak, aynı ticari kavram için (PTF, YEKDEM, toplam maliyet, satış fiyatı) birbirinden habersiz çalışan iki farklı hesap yolunu tespit etmek istiyorum; çünkü sessiz duplikasyon dead code'dan değil **yarı-canlı habersiz koddan** doğar ve müşteriye iki farklı fiyat gösterir.

### Acceptance Criteria

1. WHEN aynı ticari kavram (PTF, YEKDEM, tarife, toplam) iki farklı endpoint'ten hesaplanıyorsa AND her endpoint farklı bir DB kaynağı okuyorsa THEN bu "paralel hesap yolu" olarak raporlanmalı SHALL.
2. WHEN paralel hesap yolu tespit edildiğinde AND her iki endpoint aynı iş akışında (örn. teklif üretimi) birleşiyorsa THEN öncelik OTOMATİK olarak **P0** (şartlı P0) atanmalı SHALL.
3. WHEN paralel yol iki ayrı iş akışında (örn. salt görüntüleme + teklif motoru) kullanılıyor AND çıktıları aynı müşteriye aynı anda gösterilmiyor ise THEN öncelik **P1** atanmalı SHALL.
4. FOR EACH paralel hesap yolu THEN rapor SHALL şu alanları içermeli: {kavram, yol_A (endpoint + kaynak + formül), yol_B (endpoint + kaynak + formül), örnek dönem için çıktı farkı, birleştiği iş akışı, önerilen SoT, önerilen deprecation planı}.
5. THE F-PTF bulgusu (manuel mod `market_reference_prices.ptf_tl_per_mwh` vs risk engine `hourly_market_prices.ptf_tl_per_mwh`) bu requirement'ın ilk doğrulanmış örneği olarak audit-report.md'ye P0 seviyesinde işlenmeli SHALL.

---

## Requirement 25 — Model Consistency Enforcement

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, aynı input'un (dönem + tüketim profili + çarpan + tarife) hangi endpoint'ten hesaplanırsa hesaplansın aynı çıktıyı vermesini garanti altına almak istiyorum; aksi halde hangi ekranın "doğru" olduğu belirsizleşir.

### Acceptance Criteria

1. FOR EACH ticari hesap çıktısı (weighted_ptf, yekdem_inclusive_unit_price, total_cost, sales_price, net_margin) THE sistemde yalnızca **tek bir canonical hesap fonksiyonu** olmalı SHALL.
2. WHEN farklı endpoint'ler aynı çıktıyı döndürüyorsa THEN hepsi aynı canonical fonksiyonu çağırmalı; kendi paralel implementasyonlarını tutmayacak SHALL.
3. THE canonical fonksiyonlar `backend/app/pricing/pricing_engine.py` + `backend/app/pricing/time_zones.py` içinde tanımlı olmalı SHALL; bu dosyalar dışında aynı matematiği tekrar eden kod **invariant test tarafından reddedilmeli**.
4. WHEN yeni bir endpoint eklenirken aynı hesap tekrar implement edilirse THEN `test_invariants.py::test_no_parallel_calc_path` kırılmalı SHALL (grep guard + aynı input → aynı çıktı property testi).
5. WHERE frontend kendi replika hesabını yapıyorsa THE replika çıktısı backend canonical'a karşı **her persist (teklif oluştur / PDF indir) öncesi doğrulanmalı**; tolerans 0.01, aşılırsa işlem reddedilmeli.

---

## Requirement 26 — Fallback Transparency (Hybrid-C Policy)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, eksik veri durumunda yapılan fallback'lerin hem response payload'unda hem UI'da hem PDF çıktısında açıkça işaretlenmesini istiyorum; aksi halde "aylık ortalama PTF" ile "saatlik ağırlıklı PTF" arasındaki sessiz model değişikliği fark edilmeden teklif üretir.

### Acceptance Criteria

1. THE PTF veri gereksinimi şu matrise göre tanımlanır SHALL:

   | Durum | Karar | offer_allowed | pdf_allowed |
   |---|---|---|---|
   | Saatlik PTF mevcut | Teklif üretilir (canonical yol) | `true` | `true` |
   | Saatlik yok, aylık referans PTF var | Sadece read-only preview | `false` | `false` |
   | Hiçbir PTF kaynağı yok | Tüm işlem reddedilir | `false` | `false` |

2. WHEN fallback mode aktifse THEN backend response body SHALL zorunlu şu alanları içermeli:
   ```json
   {
     "fallback_mode": true,
     "read_only_preview": true,
     "model_used": "monthly_reference_only" | "hourly_canonical",
     "offer_allowed": false,
     "pdf_allowed": false,
     "fallback_reason": "hourly_data_missing_for_period"
   }
   ```
3. WHEN `offer_allowed == false` ise THEN teklif oluşturma endpoint'leri (`/api/offers/create`, `/api/full-process` vb.) SHALL HTTP 409 Conflict dönmeli; "Bu dönem için saatlik veri yok, teklif üretilemez" mesajıyla.
4. WHEN `pdf_allowed == false` ise THEN PDF üretim endpoint'leri aynı şekilde 409 döndürmeli SHALL.
5. THE frontend SHALL fallback_mode=true aldığında kullanıcıya görünür kırmızı banner göstermeli: "⚠ Bu dönem için sadece aylık referans fiyat mevcut. Teklif/PDF üretimi için admin panelden saatlik EPİAŞ verisi yüklenmelidir."
6. THE PDF üretimi SHALL fallback_mode aktifken bloklanmalı; "read-only preview"ı PDF'e bastırmak yasak.
7. WHEN invariant test `test_no_silent_fallback` çalıştığında THEN sistemde fallback varsa yukarıdaki alanların HEPSİNİN response'ta olduğu doğrulanmalı SHALL.
