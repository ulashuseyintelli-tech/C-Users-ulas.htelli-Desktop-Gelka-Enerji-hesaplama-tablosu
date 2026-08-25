"""
S5-R02A — Deterministik SENTETİK saatlik tüketim workbook fixture'ı.

NEDEN VAR
─────────
Golden recon testleri (`test_recon_cansu_golden.py`, `test_recon_v2_golden.py`)
repo kökünde İZLENMEYEN, KİŞİ ADLI gerçek bir müşteri Excel'ine bağımlıydı
("Cansu … .xlsx"). Owner kararı (R02A Bölüm 2/6): kişi adlı gerçek workbook
test bağımlılığı olamaz, commit edilemez, evidence'a alınamaz.

Bu modül aynı biçimsel sözleşmeyi (recon parser "Format B":
`Tarih` + `Aktif Çekiş`, metin tarih `DD/MM/YYYY HH:MM:SS`) TAMAMEN SENTETİK
ve programatik olarak üretir:

- Gerçek kişi/şirket/vergi/telefon/e-posta İÇERMEZ.
- Gerçek workbook'tan tek bir hücre/satır KOPYALANMAMIŞTIR; takvim aralığı
  (2026-01 .. 2026-04 kısmi) test sözleşmesinin kendisidir
  (bkz. TestDataQuality.test_*_complete), veri değerleri değildir.
- Workbook BELLEKTE üretilir (bytes) — diske dosya yazılmaz, temizlik
  gerektirmez.
- Beklentiler ("golden") ayrı bir donmuş dosyadan değil, ÜRETİM
  MANİFESTİNDEN ANALİTİK olarak türetilir: helper tam olarak hangi saate
  hangi kWh'yi yazdığını bildiği için dönem toplamları/T1-T2-T3 bölüşümü
  matematiksel kesinlikle hesaplanır. Parser davranışı değişirse testler
  bu analitik beklentiye karşı kırılır — donmuş-dosya kapısından daha
  güçlü bir regresyon kapısıdır.

T1/T2/T3 SAAT KURALI (beklenti hesabı için)
───────────────────────────────────────────
Ürünün TEK tanımı `app.pricing.time_zones.classify_hour`'dur; beklentiler
tautolojiye düşmemek için üretim fonksiyonu ÇAĞRILMADAN, ürün belgesindeki
yayınlı kuraldan bağımsız olarak kurulur:
    T1 Gündüz 06:00–16:59 (11 saat) · T2 Puant 17:00–21:59 (5 saat)
    T3 Gece  22:00–05:59 (8 saat)

Çağrıldığı yerler:
- tests/test_recon_cansu_golden.py  (sentetik kaynak + analitik beklenti)
- tests/test_recon_v2_golden.py     (sentetik kaynak)
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import NamedTuple

from openpyxl import Workbook

# ── Takvim sözleşmesi (test paketinin pinlediği şekil) ─────────────────────
# 2026-01: 744 saat (31 gün, tam)      2026-02: 672 saat (28 gün, tam)
# 2026-03: 744 saat (31 gün, tam)      2026-04: 553 saat (23 gün + 24/04 00:00)
_DONEMLER: tuple[tuple[int, int, int], ...] = (
    # (ay, tam_gun, ek_saat)
    (1, 31, 0),
    (2, 28, 0),
    (3, 31, 0),
    (4, 23, 1),   # 24/04 00:00 tek ek saati — "553 = 23×24 + 1" sözleşmesi
)
_YIL = 2026

# Dönem başına SABİT kWh — binary-tam temsil edilebilir değerler seçildi ki
# Decimal→float köprüsünde yuvarlama artefaktı oluşmasın. Nisan değeri,
# reconciliation testinin "Excel > fatura beyanı (T1=2211)" beklentisini
# sentetik veride de sağlamak için 15.0'dır (T1 253 saat × 15 = 3795 > 2211).
_KWH: dict[int, Decimal] = {
    1: Decimal("10.5"),
    2: Decimal("11.25"),
    3: Decimal("9.75"),
    4: Decimal("15.0"),
}

# Yayınlı saat kuralı (üretim fonksiyonu ÇAĞRILMADAN, bağımsız sabitler)
_T1_SAATLER = frozenset(range(6, 17))    # 06..16
_T2_SAATLER = frozenset(range(17, 22))   # 17..21
# T3 = geri kalan (22..23, 0..5)

_GUN_UZUNLUKLARI = {1: 31, 2: 28, 3: 31, 4: 30}


class DonemBeklenti(NamedTuple):
    record_count: int
    total_kwh: float
    t1_kwh: float
    t2_kwh: float
    t3_kwh: float
    missing_hours: int


def _saat_akisi():
    """(ay, gun, saat) uclulerini takvim sozlesmesine gore uretir."""
    for ay, tam_gun, ek_saat in _DONEMLER:
        for gun in range(1, tam_gun + 1):
            for saat in range(24):
                yield ay, gun, saat
        for saat in range(ek_saat):
            yield ay, tam_gun + 1, saat


def build_sentetik_workbook_bytes(*, hatali_satir_sayisi: int = 0) -> bytes:
    """
    Format B sentetik workbook'u BELLEKTE üretir ve bytes döndürür.

    Args:
        hatali_satir_sayisi: Sona eklenecek BOZUK TARİHLİ satır sayısı.
            Parser'ın hata yolunu sınamak isteyen testler için; golden
            akışta 0'dır (TestDataQuality `failed_rows == 0` pinler).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "SENTETIK TUKETIM"
    ws.append(["Tarih", "Aktif Çekiş"])

    for ay, gun, saat in _saat_akisi():
        tarih = f"{gun:02d}/{ay:02d}/{_YIL} {saat:02d}:00:00"
        ws.append([tarih, float(_KWH[ay])])

    for i in range(hatali_satir_sayisi):
        ws.append([f"BOZUK-TARIH-{i}", 1.0])

    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def beklenen_manifest(*, hatali_satir_sayisi: int = 0) -> dict:
    """
    Üretim manifestinden ANALİTİK beklentiler.

    `test_recon_cansu_golden.py`'nin kullandığı golden-snapshot şekliyle
    birebir aynı anahtarları döndürür; donmuş JSON dosyasına gerek kalmaz.
    """
    donemler: dict[str, DonemBeklenti] = {}
    for ay, tam_gun, ek_saat in _DONEMLER:
        kwh = _KWH[ay]
        t1 = t2 = t3 = 0
        for gun in range(1, tam_gun + 1):
            t1 += len(_T1_SAATLER)
            t2 += len(_T2_SAATLER)
            t3 += 24 - len(_T1_SAATLER) - len(_T2_SAATLER)
        for saat in range(ek_saat):
            if saat in _T1_SAATLER:
                t1 += 1
            elif saat in _T2_SAATLER:
                t2 += 1
            else:
                t3 += 1
        kayit = tam_gun * 24 + ek_saat
        # `validate_period_completeness` donemi TAM AY takvimine gore olcer:
        # kismi ayda eksik saat = (ay_gunu × 24) − kayit. Nisan icin
        # 720 − 553 = 167 — gercek dosyanin donmus golden'inda da boyleydi.
        donemler[f"{_YIL}-{ay:02d}"] = DonemBeklenti(
            record_count=kayit,
            total_kwh=float(kwh * kayit),
            t1_kwh=float(kwh * t1),
            t2_kwh=float(kwh * t2),
            t3_kwh=float(kwh * t3),
            missing_hours=_GUN_UZUNLUKLARI[ay] * 24 - kayit,
        )

    toplam_veri_satiri = sum(d.record_count for d in donemler.values())
    return {
        "format_detected": "format_b",
        "total_rows": toplam_veri_satiri + hatali_satir_sayisi,
        "successful_rows": toplam_veri_satiri,
        "failed_rows": hatali_satir_sayisi,
        "multiplier_metadata": None,
        "periods": {
            ad: {
                "record_count": d.record_count,
                "total_kwh": d.total_kwh,
                "t1_kwh": d.t1_kwh,
                "t2_kwh": d.t2_kwh,
                "t3_kwh": d.t3_kwh,
                "missing_hours": d.missing_hours,
            }
            for ad, d in donemler.items()
        },
    }
