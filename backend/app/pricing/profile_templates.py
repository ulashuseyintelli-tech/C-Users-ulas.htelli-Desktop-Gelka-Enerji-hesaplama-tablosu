"""
Pricing Risk Engine — Sektörel Profil Şablonları.

12 yerleşik sektörel tüketim profili şablonu tanımlar. Her şablon
24 saatlik normalize ağırlık dizisi içerir (toplam = 1.0). Şablonlar
gerçek sayaç verisi olmayan müşteriler için yaklaşık tüketim profili
üretmek amacıyla kullanılır.

Requirements: 5.1, 5.2, 5.3, 5.4
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from .excel_parser import ParsedConsumptionRecord
from .schemas import ProfileTemplate


# ═══════════════════════════════════════════════════════════════════════════════
# Şablon Tanımları
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class TemplateDefinition:
    """Yerleşik profil şablonu tanımı.

    T1/T2/T3 oranları ve risk metadata:
      t1_pct, t2_pct, t3_pct: Hedef enerji (kWh) dağılım yüzdeleri (toplam=100)
        T1 = Gündüz 06:00-16:59 (11 saat)
        T2 = Puant  17:00-21:59 (5 saat)
        T3 = Gece   22:00-05:59 (8 saat)
      risk_level: low / medium / high / very_high
      risk_buffer_pct: Önerilen katsayıya eklenecek risk tamponu (%)
        Doğrudan katsayıya eklenmez — ayrı gösterilir:
        recommended_margin_pct = base_margin_pct + risk_buffer_pct
    """
    name: str
    display_name: str
    description: str
    hourly_weights: list[float]  # 24 eleman, toplam = 1.0
    # T1/T2/T3 hedef kWh dağılım yüzdeleri
    t1_pct: float = 40.0   # Gündüz 06:00-16:59
    t2_pct: float = 25.0   # Puant  17:00-21:59
    t3_pct: float = 35.0   # Gece   22:00-05:59
    # Risk metadata
    risk_level: str = "medium"        # low / medium / high / very_high
    risk_buffer_pct: float = 2.0      # Önerilen katsayıya eklenecek tampon (%)


def _normalize(weights: list[float]) -> list[float]:
    """Ağırlık dizisini normalize et — toplam tam 1.0 olsun."""
    total = sum(weights)
    if total == 0:
        return [1.0 / 24] * 24
    normalized = [round(w / total, 8) for w in weights]
    # Son elemanda kalan farkı düzelt (floating point)
    diff = 1.0 - sum(normalized)
    normalized[-1] = round(normalized[-1] + diff, 8)
    return normalized


def _build_from_t1t2t3(
    t1_pct: float, t2_pct: float, t3_pct: float,
    shape: list[float],
) -> list[float]:
    """T1/T2/T3 kWh yüzdelerine ve saat-içi şekle göre 24 saatlik ağırlık üret.

    T1 = 06:00-16:59 (saatler 6-16, 11 saat)
    T2 = 17:00-21:59 (saatler 17-21, 5 saat)
    T3 = 22:00-05:59 (saatler 22-23 + 0-5, 8 saat)

    shape: 24 elemanlı ham ağırlık dizisi (saat-içi dağılım şekli).
    Fonksiyon shape'i T1/T2/T3 oranlarına uyacak şekilde ölçekler.
    """
    assert len(shape) == 24
    t1_hours = list(range(6, 17))    # 06-16 (11 saat)
    t2_hours = list(range(17, 22))   # 17-21 (5 saat)
    t3_hours = list(range(22, 24)) + list(range(0, 6))  # 22-23, 00-05 (8 saat)

    # Her dilimin ham toplamını hesapla
    t1_raw = sum(shape[h] for h in t1_hours) or 1.0
    t2_raw = sum(shape[h] for h in t2_hours) or 1.0
    t3_raw = sum(shape[h] for h in t3_hours) or 1.0

    # Ölçekleme faktörleri: hedef oran / ham oran
    t1_scale = (t1_pct / 100.0) / (t1_raw / sum(shape))
    t2_scale = (t2_pct / 100.0) / (t2_raw / sum(shape))
    t3_scale = (t3_pct / 100.0) / (t3_raw / sum(shape))

    scaled = [0.0] * 24
    for h in t1_hours:
        scaled[h] = shape[h] * t1_scale
    for h in t2_hours:
        scaled[h] = shape[h] * t2_scale
    for h in t3_hours:
        scaled[h] = shape[h] * t3_scale

    return _normalize(scaled)


# ═══════════════════════════════════════════════════════════════════════════════
# SAAT-İÇİ ŞEKİL TANIMLARI (shape) — T1/T2/T3 ölçeklemeden ÖNCE
# Her shape saat-içi göreceli yoğunluğu temsil eder.
# _build_from_t1t2t3() bu shape'i hedef T1/T2/T3 oranlarına ölçekler.
# ═══════════════════════════════════════════════════════════════════════════════

# 3 Vardiya: neredeyse düz, vardiya geçişlerinde hafif düşüş
_shape_3_vardiya = [
    4.0, 4.0, 4.0, 4.0, 4.0, 3.8,   # 00-05
    3.5, 4.2, 4.3, 4.3, 4.3, 4.3,   # 06-11
    4.0, 4.3, 4.3, 4.3, 4.3,         # 12-16
    4.2, 3.5, 4.0, 4.0, 3.8,         # 17-21
    3.5, 4.0,                          # 22-23
]

# Tek Vardiya: 07-17 üretim, gece baz yük
_shape_tek_vardiya = [
    1.2, 1.0, 1.0, 1.0, 1.0, 1.2,   # 00-05
    2.0, 3.5, 4.5, 5.0, 5.0, 5.0,   # 06-11
    3.5, 5.0, 5.0, 5.0, 4.5,         # 12-16
    3.5, 2.0, 1.2, 1.0, 1.0,         # 17-21
    1.0, 1.0,                          # 22-23
]

# Ofis: 08-17 mesai, gece sunucu odası baz yükü
_shape_ofis = [
    0.8, 0.7, 0.7, 0.7, 0.7, 0.8,   # 00-05
    1.0, 1.5, 3.0, 4.5, 5.0, 5.0,   # 06-11
    4.0, 5.0, 5.0, 4.5, 4.0,         # 12-16
    3.0, 1.5, 1.0, 0.8, 0.8,         # 17-21
    0.7, 0.7,                          # 22-23
]

# Otel: sabah çift pik (kahvaltı+çamaşırhane) + akşam pik
_shape_otel = [
    3.0, 2.5, 2.5, 2.5, 2.5, 3.0,   # 00-05
    4.5, 5.5, 6.0, 5.0, 4.0, 3.5,   # 06-11
    3.5, 3.0, 3.0, 3.0, 3.5,         # 12-16
    4.0, 5.0, 5.5, 5.0, 4.5,         # 17-21
    4.0, 3.5,                          # 22-23
]

# Restoran: öğle + akşam çift pik, soğutma baz yükü
_shape_restoran = [
    1.5, 1.2, 1.2, 1.2, 1.2, 1.5,   # 00-05
    1.8, 2.0, 2.5, 3.5, 4.5, 5.5,   # 06-11
    6.0, 5.5, 3.0, 2.5, 2.5,         # 12-16
    3.0, 5.0, 6.0, 6.5, 6.0,         # 17-21
    4.0, 2.5,                          # 22-23
]

# Soğuk Hava Deposu: gece pre-cool, gündüz minimal
_shape_soguk_depo = [
    7.0, 7.0, 7.0, 7.0, 7.0, 7.0,   # 00-05
    5.0, 3.0, 2.0, 2.5, 2.5, 2.5,   # 06-11
    2.0, 2.5, 2.5, 2.5, 2.0,         # 12-16
    2.0, 3.0, 3.5, 4.0, 5.0,         # 17-21
    6.0, 7.0,                          # 22-23
]

# Gece Üretim: 22-06 tam üretim, gündüz bakım
_shape_gece_uretim = [
    6.0, 6.0, 6.0, 6.0, 6.0, 5.5,   # 00-05
    3.0, 1.5, 1.2, 1.2, 1.2, 1.2,   # 06-11
    1.0, 1.2, 1.2, 1.2, 1.2,         # 12-16
    1.5, 2.0, 2.5, 3.0, 4.0,         # 17-21
    5.0, 5.5,                          # 22-23
]

# AVM: 10-22 açık, HVAC pre-conditioning, akşam sinema piki
_shape_avm = [
    1.5, 1.2, 1.2, 1.2, 1.2, 1.5,   # 00-05
    1.8, 2.5, 3.5, 4.5, 5.5, 5.5,   # 06-11
    5.5, 5.5, 5.5, 5.5, 5.5,         # 12-16
    5.5, 6.5, 7.0, 6.5, 6.0,         # 17-21
    3.0, 1.8,                          # 22-23
]

# Akaryakıt: 24 saat, sabah/akşam trafik pikleri
_shape_akaryakit = [
    2.5, 2.0, 2.0, 2.0, 2.5, 3.5,   # 00-05
    5.0, 6.0, 5.5, 5.0, 4.5, 4.0,   # 06-11
    4.0, 4.0, 4.0, 4.0, 4.5,         # 12-16
    5.0, 6.0, 5.5, 5.0, 4.0,         # 17-21
    3.0, 2.5,                          # 22-23
]

# Market: soğutma baskın (%40-60), fırın 04:00
_shape_market = [
    2.5, 2.5, 2.5, 2.5, 3.0, 3.5,   # 00-05
    3.5, 3.5, 4.5, 5.0, 5.0, 5.0,   # 06-11
    4.5, 5.0, 5.5, 5.5, 5.5,         # 12-16
    5.5, 5.5, 5.0, 4.5, 4.0,         # 17-21
    3.5, 3.0,                          # 22-23
]

# Hastane: 24 saat, ameliyathane piki 08-12
_shape_hastane = [
    4.0, 3.5, 3.5, 3.5, 3.5, 4.0,   # 00-05
    4.5, 5.0, 6.5, 7.0, 7.0, 6.5,   # 06-11
    5.5, 6.0, 6.0, 5.5, 5.0,         # 12-16
    4.5, 4.0, 3.5, 3.5, 3.5,         # 17-21
    4.0, 4.0,                          # 22-23
]

# Tarımsal Sulama: gece %75+, gündüz minimal
_shape_tarimsal = [
    7.0, 7.0, 7.0, 7.0, 7.0, 6.5,   # 00-05
    3.0, 1.0, 0.5, 0.5, 0.5, 0.5,   # 06-11
    0.3, 0.5, 0.5, 0.5, 0.5,         # 12-16
    0.8, 1.5, 2.0, 3.0, 4.0,         # 17-21
    6.0, 7.0,                          # 22-23
]

# Site Yönetimi: sabah/akşam çift pik (asansör+hidrofor)
_shape_site = [
    2.0, 1.5, 1.2, 1.2, 1.2, 1.8,   # 00-05
    3.5, 6.0, 6.5, 4.0, 3.5, 3.5,   # 06-11
    3.0, 3.0, 3.0, 3.5, 4.0,         # 12-16
    5.5, 7.5, 8.0, 7.0, 5.5,         # 17-21
    4.0, 3.0,                          # 22-23
]

# İki Vardiya: 06-22 üretim, gece baz yük
_shape_iki_vardiya = [
    1.2, 1.0, 1.0, 1.0, 1.0, 1.5,   # 00-05
    3.0, 4.5, 5.0, 5.0, 5.0, 5.0,   # 06-11
    4.5, 5.0, 4.0, 5.0, 5.0,         # 12-16
    5.0, 5.0, 5.0, 4.5, 3.5,         # 17-21
    2.0, 1.5,                          # 22-23
]

# Tekstil: yüksek baz yük, kompresör+boyahane 24 saat
_shape_tekstil = [
    3.5, 3.5, 3.5, 3.5, 3.5, 3.5,   # 00-05
    4.0, 5.0, 5.5, 5.5, 5.5, 5.5,   # 06-11
    5.0, 5.5, 5.5, 5.5, 5.5,         # 12-16
    5.0, 4.5, 4.0, 3.5, 3.5,         # 17-21
    3.5, 3.5,                          # 22-23
]

# Fırın: erken sabah pişirme piki (02-07)
_shape_firin = [
    1.5, 1.5, 3.0, 4.5, 5.5, 6.5,   # 00-05
    7.0, 6.0, 4.5, 3.5, 3.0, 3.0,   # 06-11
    3.0, 3.5, 4.0, 4.5, 4.0,         # 12-16
    3.0, 2.0, 1.5, 1.5, 1.5,         # 17-21
    1.5, 1.5,                          # 22-23
]


# ═══════════════════════════════════════════════════════════════════════════════
# ONAYLANMIŞ T1/T2/T3 REFERANS TABLOSU
# T1=Gündüz(06-16), T2=Puant(17-21), T3=Gece(22-05)
# Risk buffer doğrudan katsayıya eklenmez — ayrı gösterilir:
#   recommended_margin_pct = base_margin_pct + risk_buffer_pct
# ═══════════════════════════════════════════════════════════════════════════════

BUILTIN_TEMPLATES: list[TemplateDefinition] = [
    TemplateDefinition(
        name="3_vardiya_sanayi",
        display_name="3 Vardiya Sanayi",
        description="7/24 kesintisiz üretim. Vardiya geçişlerinde hafif düşüş. Düz profil.",
        hourly_weights=_build_from_t1t2t3(40, 25, 35, _shape_3_vardiya),
        t1_pct=40, t2_pct=25, t3_pct=35,
        risk_level="low", risk_buffer_pct=0,
    ),
    TemplateDefinition(
        name="tek_vardiya_fabrika",
        display_name="Tek Vardiya Fabrika",
        description="07:00-18:00 üretim. Gece %15-25 baz yük (kompresör, havalandırma).",
        hourly_weights=_build_from_t1t2t3(60, 25, 15, _shape_tek_vardiya),
        t1_pct=60, t2_pct=25, t3_pct=15,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="ofis",
        display_name="Ofis",
        description="08:00-18:00 mesai. Gece sunucu odası + güvenlik baz yükü.",
        hourly_weights=_build_from_t1t2t3(70, 25, 5, _shape_ofis),
        t1_pct=70, t2_pct=25, t3_pct=5,
        risk_level="low", risk_buffer_pct=0,
    ),
    TemplateDefinition(
        name="otel",
        display_name="Otel",
        description="24 saat. Sabah çift pik (çamaşırhane+mutfak) + akşam pik. Yüksek gece baz.",
        hourly_weights=_build_from_t1t2t3(40, 35, 25, _shape_otel),
        t1_pct=40, t2_pct=35, t3_pct=25,
        risk_level="high", risk_buffer_pct=5,
    ),
    TemplateDefinition(
        name="restoran",
        display_name="Restoran",
        description="Öğle + akşam çift pik. Soğutma 24 saat baz yük. Akşam 19-21 en yoğun.",
        hourly_weights=_build_from_t1t2t3(30, 45, 25, _shape_restoran),
        t1_pct=30, t2_pct=45, t3_pct=25,
        risk_level="high", risk_buffer_pct=5,
    ),
    TemplateDefinition(
        name="soguk_hava_deposu",
        display_name="Soğuk Hava Deposu",
        description="Gece pre-cool stratejisi. Gündüz termal kütle tutar. T3 ağırlıklı.",
        hourly_weights=_build_from_t1t2t3(35, 20, 45, _shape_soguk_depo),
        t1_pct=35, t2_pct=20, t3_pct=45,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="gece_agirlikli_uretim",
        display_name="Gece Ağırlıklı Üretim",
        description="22:00-06:00 üretim. Ucuz gece tarifesi. Gündüz bakım/kalite kontrol.",
        hourly_weights=_build_from_t1t2t3(15, 15, 70, _shape_gece_uretim),
        t1_pct=15, t2_pct=15, t3_pct=70,
        risk_level="low", risk_buffer_pct=0,
    ),
    TemplateDefinition(
        name="avm",
        display_name="AVM",
        description="10:00-22:00 açık. HVAC pre-conditioning. Akşam sinema+food court piki.",
        hourly_weights=_build_from_t1t2t3(45, 35, 20, _shape_avm),
        t1_pct=45, t2_pct=35, t3_pct=20,
        risk_level="high", risk_buffer_pct=5,
    ),
    TemplateDefinition(
        name="akaryakit_istasyonu",
        display_name="Akaryakıt İstasyonu",
        description="24 saat. Sabah/akşam trafik pikleri. Gece TIR trafiği devam.",
        hourly_weights=_build_from_t1t2t3(40, 30, 30, _shape_akaryakit),
        t1_pct=40, t2_pct=30, t3_pct=30,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="market_supermarket",
        display_name="Market / Süpermarket",
        description="08:00-22:00 açık. Soğutma %40-60 toplam elektrik. Fırın 04:00'te başlar.",
        hourly_weights=_build_from_t1t2t3(50, 30, 20, _shape_market),
        t1_pct=50, t2_pct=30, t3_pct=20,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="hastane",
        display_name="Hastane",
        description="24 saat. Ameliyathane piki 08-12. Gece yoğun bakım+acil baz yükü.",
        hourly_weights=_build_from_t1t2t3(40, 30, 30, _shape_hastane),
        t1_pct=40, t2_pct=30, t3_pct=30,
        risk_level="low", risk_buffer_pct=0,
    ),
    TemplateDefinition(
        name="tarimsal_sulama",
        display_name="Tarımsal Sulama",
        description="Gece 22:00-06:00 ağırlıklı (%75+). Gece tarife indirimi. KDV %10.",
        hourly_weights=_build_from_t1t2t3(15, 10, 75, _shape_tarimsal),
        t1_pct=15, t2_pct=10, t3_pct=75,
        risk_level="low", risk_buffer_pct=0,
    ),
    TemplateDefinition(
        name="site_yonetimi",
        display_name="Site Yönetimi",
        description="Ortak alan. Sabah/akşam asansör+hidrofor pikleri. Profil çok değişken.",
        hourly_weights=_build_from_t1t2t3(45, 35, 20, _shape_site),
        t1_pct=45, t2_pct=35, t3_pct=20,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="iki_vardiya_fabrika",
        display_name="İki Vardiya Fabrika",
        description="06:00-22:00 üretim (2×8 saat). Vardiya geçişi 14:00. Türkiye'de yaygın.",
        hourly_weights=_build_from_t1t2t3(55, 30, 15, _shape_iki_vardiya),
        t1_pct=55, t2_pct=30, t3_pct=15,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="tekstil_fabrikasi",
        display_name="Tekstil Fabrikası",
        description="Yüksek baz yük (%40-50). Kompresör+boyahane 24 saat durdurulamaz.",
        hourly_weights=_build_from_t1t2t3(45, 25, 30, _shape_tekstil),
        t1_pct=45, t2_pct=25, t3_pct=30,
        risk_level="medium", risk_buffer_pct=2,
    ),
    TemplateDefinition(
        name="firin_pastane",
        display_name="Fırın / Pastane",
        description="Erken sabah piki: 02:00 hamur, 05:00-07:00 pişirme. İkinci pişirme 15:00.",
        hourly_weights=_build_from_t1t2t3(35, 20, 45, _shape_firin),
        t1_pct=35, t2_pct=20, t3_pct=45,
        risk_level="medium", risk_buffer_pct=2,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Veritabanı İşlemleri
# ═══════════════════════════════════════════════════════════════════════════════


def seed_profile_templates(db: Session) -> int:
    """Yerleşik profil şablonlarını veritabanına ekle (idempotent).

    Mevcut şablonlar atlanır — sadece yeni şablonlar eklenir.

    Args:
        db: SQLAlchemy oturumu.

    Returns:
        Yeni eklenen şablon sayısı.
    """
    created_count = 0

    for tmpl in BUILTIN_TEMPLATES:
        existing = (
            db.query(ProfileTemplate)
            .filter(ProfileTemplate.name == tmpl.name)
            .first()
        )
        if existing is not None:
            continue

        record = ProfileTemplate(
            name=tmpl.name,
            display_name=tmpl.display_name,
            description=tmpl.description,
            hourly_weights=json.dumps(tmpl.hourly_weights),
            is_builtin=1,
        )
        db.add(record)
        created_count += 1

    if created_count > 0:
        db.commit()

    return created_count


def generate_t1t2t3_consumption(
    t1_kwh: float,
    t2_kwh: float,
    t3_kwh: float,
    period: str,
) -> list[ParsedConsumptionRecord]:
    """T1/T2/T3 kWh değerlerinden saatlik tüketim profili üret.

    Saat sayısı ay bazlı dinamik hesaplanır:
    - 28 gün → 672 saat, 29 gün → 696 saat, 30 gün → 720 saat, 31 gün → 744 saat
    - calendar.monthrange(year, month) ile gün sayısı belirlenir

    Dağıtım mantığı (v1: uniform — hafta içi/sonu ayrımı yok):
    - Her gün için classify_hour(h) ile saat→dilim eşleştirmesi yapılır
    - T1 saatleri: 06:00–16:59 (günde 11 saat) → her saat = T1_kWh / (gün_sayısı × 11)
    - T2 saatleri: 17:00–21:59 (günde 5 saat)  → her saat = T2_kWh / (gün_sayısı × 5)
    - T3 saatleri: 22:00–05:59 (günde 8 saat)  → her saat = T3_kWh / (gün_sayısı × 8)

    Residual fix: Her zone'un son saatine artık eklenir, böylece
    sum(zone_hours) == zone_kwh TAM EŞİT olur (floating point hatası sıfırlanır).

    Args:
        t1_kwh: Gündüz tüketimi (kWh), >= 0
        t2_kwh: Puant tüketimi (kWh), >= 0
        t3_kwh: Gece tüketimi (kWh), >= 0
        period: Dönem (YYYY-MM formatı)

    Returns:
        ParsedConsumptionRecord listesi (gün_sayısı × 24 kayıt)

    Raises:
        ValueError: Geçersiz dönem formatı veya tüm değerler sıfır
    """
    import re as _re

    from .time_zones import classify_hour
    from .models import TimeZone

    # ── Dönem validasyonu ──
    if not _re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
        raise ValueError(
            f"Geçersiz dönem formatı: '{period}'. Beklenen: YYYY-MM"
        )

    # ── Toplam > 0 validasyonu ──
    if (t1_kwh + t2_kwh + t3_kwh) <= 0:
        raise ValueError(
            "Toplam tüketim sıfır olamaz. En az bir zaman diliminde tüketim giriniz."
        )

    year = int(period[:4])
    month = int(period[5:7])
    days_in_month = calendar.monthrange(year, month)[1]

    # ── Zone saat sayıları (dinamik, ay bazlı) ──
    t1_total_hours = days_in_month * 11  # 06:00–16:59
    t2_total_hours = days_in_month * 5   # 17:00–21:59
    t3_total_hours = days_in_month * 8   # 22:00–05:59

    # ── Zone bazlı saatlik kWh hesapla ──
    zone_kwh_map = {
        TimeZone.T1: t1_kwh,
        TimeZone.T2: t2_kwh,
        TimeZone.T3: t3_kwh,
    }
    zone_hours_map = {
        TimeZone.T1: t1_total_hours,
        TimeZone.T2: t2_total_hours,
        TimeZone.T3: t3_total_hours,
    }

    # Saatlik kWh: zone_kwh / zone_total_hours, round(4)
    # Sıfır zone'lar için 0.0
    zone_hourly_kwh: dict[TimeZone, float] = {}
    for tz in TimeZone:
        if zone_kwh_map[tz] == 0 or zone_hours_map[tz] == 0:
            zone_hourly_kwh[tz] = 0.0
        else:
            zone_hourly_kwh[tz] = round(zone_kwh_map[tz] / zone_hours_map[tz], 4)

    # ── Kayıtları üret + her zone'un son saatini takip et ──
    records: list[ParsedConsumptionRecord] = []
    # Son saat indekslerini zone bazında takip et (residual fix için)
    zone_last_index: dict[TimeZone, int] = {}

    idx = 0
    for day in range(1, days_in_month + 1):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        for hour in range(24):
            tz = classify_hour(hour)
            hourly_kwh = zone_hourly_kwh[tz]
            records.append(ParsedConsumptionRecord(
                date=date_str,
                hour=hour,
                consumption_kwh=hourly_kwh,
            ))
            zone_last_index[tz] = idx
            idx += 1

    # ── Residual fix: her zone'un son saatine artık ekle ──
    # Non-negative guard: residual negatifse ve son saati negatife düşürüyorsa,
    # artığı zone'un tüm saatlerine eşit dağıt (daha güvenli).
    for tz in TimeZone:
        if zone_kwh_map[tz] == 0:
            continue  # Sıfır zone — residual gerekmez
        distributed_total = zone_hourly_kwh[tz] * zone_hours_map[tz]
        residual = zone_kwh_map[tz] - distributed_total
        if residual != 0.0:
            last_idx = zone_last_index[tz]
            new_val = records[last_idx].consumption_kwh + residual
            if new_val < 0:
                # Son saat negatife düşer — residual'ı tüm zone saatlerine dağıt
                per_hour_adj = residual / zone_hours_map[tz]
                for i, r in enumerate(records):
                    if classify_hour(r.hour) == tz:
                        records[i] = ParsedConsumptionRecord(
                            date=r.date, hour=r.hour,
                            consumption_kwh=max(0.0, r.consumption_kwh + per_hour_adj),
                        )
            else:
                records[last_idx] = ParsedConsumptionRecord(
                    date=records[last_idx].date,
                    hour=records[last_idx].hour,
                    consumption_kwh=new_val,
                )

    return records


def generate_hourly_consumption(
    template_name: str,
    total_monthly_kwh: float,
    period: str,
    db: Session,
) -> list[ParsedConsumptionRecord]:
    """Şablondan saatlik tüketim serisi üret.

    Her gün için:
        daily_kwh = total_monthly_kwh / days_in_month
        hourly_kwh[h] = daily_kwh × hourly_weight[h]

    Args:
        template_name: Şablon adı (örn: "3_vardiya_sanayi").
        total_monthly_kwh: Aylık toplam tüketim (kWh).
        period: Dönem (YYYY-MM).
        db: SQLAlchemy oturumu.

    Returns:
        ParsedConsumptionRecord listesi (gün × 24 saat).

    Raises:
        ValueError: Şablon bulunamadı veya geçersiz dönem.
    """
    import re as _re

    if not _re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
        raise ValueError(
            f"Geçersiz dönem formatı: '{period}'. Beklenen: YYYY-MM"
        )

    template = get_template_by_name(db, template_name)
    if template is None:
        raise ValueError(f"Profil şablonu bulunamadı: '{template_name}'")

    weights: list[float] = json.loads(template.hourly_weights)

    year = int(period[:4])
    month = int(period[5:7])
    days_in_month = calendar.monthrange(year, month)[1]
    daily_kwh = total_monthly_kwh / days_in_month

    records: list[ParsedConsumptionRecord] = []
    for day in range(1, days_in_month + 1):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        for hour in range(24):
            hourly_kwh = round(daily_kwh * weights[hour], 4)
            records.append(ParsedConsumptionRecord(
                date=date_str,
                hour=hour,
                consumption_kwh=hourly_kwh,
            ))

    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Sorgu Yardımcıları
# ═══════════════════════════════════════════════════════════════════════════════


def get_template_by_name(
    db: Session,
    name: str,
) -> Optional[ProfileTemplate]:
    """İsme göre profil şablonu sorgula.

    Args:
        db: SQLAlchemy oturumu.
        name: Şablon adı.

    Returns:
        ProfileTemplate kaydı veya None.
    """
    return (
        db.query(ProfileTemplate)
        .filter(ProfileTemplate.name == name)
        .first()
    )


def list_templates(db: Session) -> list[ProfileTemplate]:
    """Tüm profil şablonlarını listele.

    Args:
        db: SQLAlchemy oturumu.

    Returns:
        ProfileTemplate kayıtları listesi.
    """
    return db.query(ProfileTemplate).all()
