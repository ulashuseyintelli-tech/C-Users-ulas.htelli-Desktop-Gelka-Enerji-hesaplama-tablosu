# Phase A — Operational Acceptance — RUN-001

> **Run ID:** RUN-001
> **Started:** 2026-05-17
> **Operator:** ulas.htelli
> **Backend SHA:** `3c7c4c97` (working tree dirty: `frontend/src/recon/ReconPage.tsx`, `frontend/src/recon/types.ts`)
> **Backend port:** `8000` (Electron's `gelka-backend.exe` stopped before run)
> **Frontend:** http://localhost:3000 (Vite dev server, `npm run dev`)
> **API_BASE:** `http://127.0.0.1:8000` (frontend `api.ts` UNTOUCHED)
> **Dataset:** `Cansu Saatlik Tuketim Ocak-Nisan 2026.xlsx` (SHA-256 `ADB85E7B...`, 117440 bytes, 2715 rows × 9 cols)
> **Production DB SHA-256 baseline (pre-run):** `D14531875DB2547101FD025CA557B72041FBB1C2B8DF5CC263079CB5710D9AEA` (size 1232896, mtime 2026-05-14T11:57:33)

---

## DB Mode policy

| Mode | DB | Senaryolar | DATABASE_URL |
|---|---|---|---|
| **A — production** | `backend/gelka_enerji.db` (mevcut prod, READ-ONLY beklenir) | S-01..S-05, S-08..S-14 | `sqlite:///./gelka_enerji.db` |
| **B — isolated empty** | `.kiro/operational-acceptance/phase-a/test_db/empty_market_data.db` (her başlatmada silinip yeniden create_all) | S-06, S-07 | absolute path, repo dışında izole değil ama prod path'inden FARKLI |

**Production DB safety:** Mode B switch'i prod DB'ye yazma yapamaz çünkü uvicorn farklı bir DATABASE_URL ile başlatılır. Run sonunda prod DB SHA-256 yeniden alınır ve baseline ile karşılaştırılır.

**Switch protocol (Mode A → Mode B → Mode A):**
1. Mode A backend'i durdur (uvicorn Ctrl+C)
2. Mode B start script'i çalıştır (empty DB delete + create_all)
3. S-06/S-07 yürüt
4. Mode B backend'i durdur
5. Mode A start script'i tekrar çalıştır
6. Diğer senaryolar veya verification için kullanmaya devam et

---

## Run protocol (her senaryo için)

Her satıra şu alanları doldur:
- **Result:** ✅ PASS / ❌ FAIL / ⛔ BLOCKED / ⏭ SKIPPED
- **Backend port:** `8000` (sabit)
- **DB mode:** `A` (production) veya `B` (isolated_empty)
- **Dataset:** dosya adı (Cansu Excel ya da geçici test dosyası)
- **Elapsed (s):** algılanan süre, yaklaşık
- **UI status color:** 🟢 / 🟡 / 🔴 / —
- **API status:** HTTP code + response.status (ok/partial/error/—)
- **Notes / screenshot:** kısa observation, gerekirse `screenshots/S-XX.png`

FAIL durumunda **bug-list.md** dosyasına entry ekle.

Status="partial" senaryoları (S-06, S-07) **mutlaka ekran görüntüsü** ile freeze edilir.

---

## Pre-flight (manuel)

| # | Adım | Beklenen | Result | Note |
|---|---|---|---|---|
| P0 | Electron `gelka-backend.exe` kapatılmış olmalı | Port 8000 free | ☑ DONE | `STOPPING_PID=12832, 22068; ALL_KILLED; PORT_8000_FREE` |
| P1 | Mode A backend script çalışıyor: `start_backend_mode_a.ps1` | "Application startup complete" | ☑ DONE | terminal #5; uvicorn 8000; DATABASE_URL=sqlite:///./gelka_enerji.db |
| P2 | `GET http://localhost:8000/health` | 200 OK `{"status":"ok"}` | ☑ DONE | confirmed |
| P3 | `GET http://localhost:8000/openapi.json` içinde `/api/recon/analyze` | mevcut | ☑ DONE | confirmed |
| P4 | Frontend `npm run dev` çalışıyor | Vite serving on `http://localhost:3000` | ☑ DONE | terminal #6; VITE v5.4.21 |
| P5 | Tarayıcıda http://localhost:3000 → FileText (Recon) butonu | ReconPage render | ☐ | (operatör) |

---

## Senaryolar — Mode A (production DB)

> Backend port: 8000, DB mode: A (production), Dataset: `Cansu Saatlik Tuketim Ocak-Nisan 2026.xlsx`

### Kategori A — Happy path

#### S-01 — Excel upload, no invoice values
**Aksiyon:** ReconPage'de Cansu xlsx seç, "Invoice values" alanını boş bırak, Submit
**Beklenen:**
- API: 200, response.status="ok"
- 4 period card (2026-01..2026-04), T1/T2/T3 totals her ay için
- reconciliation_items boş, cost_comparison boş veya null
- UI: 🟢 yeşil "ok"

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

#### S-02 — Excel + invoice tek ay (Ocak), tolerance içi sapma
**Aksiyon:** Cansu xlsx, invoice values: `{"2026-01": {"total_kwh": <Cansu Ocak total ± %0.5>}}`
**Beklenen:**
- API: 200, status="ok"
- 2026-01 period'da reconciliation_item, severity LOW
- delta_kwh, delta_pct gösteriliyor
- UI: 🟢

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

#### S-03 — Excel + 4 ay tam invoice (PTF/YEKDEM mevcut)
**Aksiyon:** Tüm 4 ay için doğru invoice values
**Beklenen:**
- API: 200, status="ok"
- 4 reconciliation_items, hepsi LOW
- cost_comparison render (PTF/YEKDEM mevcut)
- savings/quote bilgisi
- UI: 🟢

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

### Kategori B — Severity ladder

#### S-04 — WARNING severity (orta sapma)
**Aksiyon:** invoice value Ocak için Cansu Ocak total'in **+%5** sapması
**Beklenen:**
- API: 200, status="ok"
- reconciliation_item.severity = "WARNING"
- UI: top-level 🟢, item içinde amber
- savings/quote production tarafında üretilebiliyorsa render edilir

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

#### S-05 — CRITICAL severity (büyük sapma)
**Aksiyon:** invoice value Ocak için Cansu Ocak total'in **+%20** sapması
**Beklenen:**
- API: 200, status="ok"
- reconciliation_item.severity = "CRITICAL"
- UI: top-level 🟢 (yapısal başarı), item içinde red

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

### Kategori D — Hata yolları

#### S-08 — Geçersiz dosya tipi (.txt)
**Aksiyon:** ReconPage'e `.txt` dosya seç (örn: bir test.txt)
**Beklenen:**
- Client-side validation: "Sadece .xlsx ve .xls"
- 🔴 KIRMIZI hata, server'a istek gitmiyor (network tab boş)

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | test.txt (mock) | __ | __ | — | ☐ | |

#### S-09 — Boş .xlsx
**Aksiyon:** Boş bir xlsx oluştur, upload et
**Beklenen:**
- API: 400 (server-side validation)
- 🔴 KIRMIZI mesaj: "Excel dosyasında veri bulunamadı" veya benzer
- UI freeze YOK

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | empty.xlsx | __ | __ | __ | ☐ | |

#### S-10 — Bozuk JSON request_body
**Aksiyon:** Invoice values alanına `{invalid json` yaz, submit (UI buna izin veriyorsa)
**Beklenen:**
- Client-side veya server-side validation
- 🔴 anlamlı hata mesajı (raw stack trace değil)

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx + bad JSON | __ | __ | __ | ☐ | |

#### S-11 — Backend down
**Aksiyon:** Mode A backend'i durdur (Ctrl+C terminal #5), upload dene
**Beklenen:**
- 🔴 Network error: "Sunucuya bağlanılamadı" veya benzer
- UI freeze YOK
- Backend yeniden başlatılınca retry çalışıyor

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 (down) | A | Cansu xlsx | __ | __ | NETWORK_ERR | ☐ | |

> Not: S-11 sonrası **Mode A backend yeniden başlatılır** (script ile), sonra S-12'ye devam.

### Kategori E — UX & operational

#### S-12 — Loading state görünürlüğü
**Aksiyon:** Cansu submit, response gelene kadar gözle. (Opsiyonel: DevTools → Network throttle Slow 3G)
**Beklenen:**
- Loading indicator GÖRÜNÜR
- Submit butonu disabled (double-submit prevention)
- Throttle ile freeze YOK

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | 200 | ☐ | |

#### S-13 — Türkçe karakter / locale
**Aksiyon:** S-01 veya S-03 sonucunu incele
**Beklenen:**
- "Şubat", "Ocak" doğru, mojibake yok
- kWh 3 desimal, TL 2 desimal tutarlı
- Decimal separator tutarlı

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | (S-01/03'ten) | __ | (200) | ☐ | |

#### S-14 — Çoklu submit / state cleanup
**Aksiyon:** S-01 sonucu görüldü → farklı invoice value ile **tekrar** submit (sayfa reload yok)
**Beklenen:**
- Eski sonuç temizleniyor, yeni sonuç render
- Loading yeniden tetikleniyor
- Eski error/warning kalmamış

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | A | Cansu xlsx | __ | __ | __ | ☐ | |

---

## Senaryolar — Mode B (isolated empty DB)

> **DB switch protocol:**
> 1. Mode A backend'i durdur (Ctrl+C terminal #5)
> 2. Çalıştır: `powershell -ExecutionPolicy Bypass -File .kiro/operational-acceptance/phase-a/scripts/start_backend_mode_b_empty.ps1`
> 3. Script empty DB'yi yeniden create eder (PTF/YEKDEM kesinlikle BOŞ)
> 4. S-06 ve S-07 yürüt
> 5. Mode B backend'i durdur
> 6. Mode A backend'i tekrar başlat (S-08+ için)

#### S-06 — PTF eksik dönem (KRİTİK)
**Aksiyon:** Mode B backend ile Cansu xlsx + tüm 4 ay invoice values
**Beklenen:**
- API: **200** (NOT 4xx, NOT 5xx)
- response.status = "**partial**"
- response.quote_blocked = **true**
- reconciliation_items hâlâ render (parse + recon başarılı)
- cost_comparison = null veya empty
- **Hiçbir savings/quote message YOK**
- UI: 🟡 amber "partial" — 🔴 KIRMIZI HATA RENGİ KESİNLİKLE OLMAYACAK
- Warning: "Saatlik PTF/YEKDEM verisi eksik..." veya benzer

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | B | Cansu xlsx | __ | __ | 200/partial | ☐ | screenshot: `screenshots/S-06-partial-render.png` |

> 🔴 ZORUNLU: ekran görüntüsü al

#### S-07 — Tamamen veri eksik (worst case)
**Aksiyon:** Mode B (DB tamamen boş, market verisi yok), Cansu xlsx + invoice values
**Beklenen:**
- API: 200, status="partial", quote_blocked=true
- reconciliation_items dolu (Excel parse + classify çalıştı)
- Net mesaj: "tasarruf hesabı yapılamıyor" veya benzer
- UI: 🟡 amber, savings yok, "ne yapmalı?" yönlendirmesi

| Backend port | DB mode | Dataset | Elapsed (s) | UI | API | Result | Notes |
|---|---|---|---|---|---|---|---|
| 8000 | B | Cansu xlsx | __ | __ | 200/partial | ☐ | screenshot: `screenshots/S-07-no-market-data.png` |

> 🔴 ZORUNLU: ekran görüntüsü al

> **Mode B sonrası:** backend'i durdur, Mode A script'i ile tekrar başlat. Sonraki senaryolar Mode A'da.

---

## Run summary (run sonunda doldur)

| Metric | Değer |
|---|---|
| Total | 14 |
| ✅ PASS | __ |
| ❌ FAIL | __ |
| ⛔ BLOCKED | __ |
| ⏭ SKIPPED | __ |
| **Pass rate** | __ % |
| Total elapsed | __ min |

### Production DB integrity (post-run)

> Çalıştır: `powershell -ExecutionPolicy Bypass -File .tmp\check_prod_db_unchanged.ps1`
> (Mode A backend durdurulmuş olmalı, yoksa SQLite lock dosyayı okutturmaz.)

- Expected SHA-256: `D14531875DB2547101FD025CA557B72041FBB1C2B8DF5CC263079CB5710D9AEA`
- Actual SHA-256 (post-run): __
- Result: ☐ OK / ☐ FAIL — **FAIL ise**: prod DB'ye yazma sızdı, run yeniden investigate

### Verdict

- ☐ **GO Phase B** (bugfix sprint)
- ☐ **STOP — re-engineering needed** (kategori C partial FAIL veya prod DB integrity FAIL)

### Sign-off

- Operator: ulas.htelli
- Completed at: ___
- Bug list: `bug-list.md`
- Screenshots: `screenshots/`
