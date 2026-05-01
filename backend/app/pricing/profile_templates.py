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
    """Yerleşik profil şablonu tanımı."""
    name: str
    display_name: str
    description: str
    hourly_weights: list[float]  # 24 eleman, toplam = 1.0


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


# ── 1. 3 Vardiya Sanayi — 7/24 düz profil ─────────────────────────────────
_3_vardiya_sanayi = _normalize([1.0] * 24)

# ── 2. Tek Vardiya Fabrika — 07:00-18:00 ağırlıklı ────────────────────────
_tek_vardiya_fabrika_raw = [
    0.5, 0.5, 0.5, 0.5, 0.5, 0.5,   # 00-05: gece minimal
    0.8,                                # 06: geçiş
    3.0, 4.0, 4.5, 4.5, 4.5,          # 07-11: tam üretim
    3.5,                                # 12: öğle molası
    4.5, 4.5, 4.5, 4.0, 3.0,          # 13-17: tam üretim
    1.0,                                # 18: kapanış
    0.5, 0.5, 0.5, 0.5, 0.5,          # 19-23: gece minimal
]
_tek_vardiya_fabrika = _normalize(_tek_vardiya_fabrika_raw)

# ── 3. Ofis — 08:00-18:00 ağırlıklı ───────────────────────────────────────
_ofis_raw = [
    0.3, 0.3, 0.3, 0.3, 0.3, 0.3,   # 00-05: gece minimal
    0.4, 0.6,                          # 06-07: erken gelenler
    3.5, 4.0, 4.5, 4.5,               # 08-11: tam mesai
    3.0,                                # 12: öğle
    4.5, 4.5, 4.0, 3.5, 3.0,          # 13-17: tam mesai
    1.0,                                # 18: kapanış
    0.4, 0.3, 0.3, 0.3, 0.3,          # 19-23: gece minimal
]
_ofis = _normalize(_ofis_raw)

# ── 4. Otel — 24 saat, akşam pik ──────────────────────────────────────────
_otel_raw = [
    2.0, 1.5, 1.5, 1.5, 1.5, 2.0,   # 00-05: gece (klima, aydınlatma)
    2.5, 3.0,                          # 06-07: kahvaltı hazırlık
    3.5, 3.0, 3.0, 3.0,               # 08-11: gündüz
    3.5,                                # 12: öğle
    3.0, 3.0, 3.0, 3.5, 4.0,          # 13-17: gündüz
    5.0, 5.5, 5.5, 5.0,               # 18-21: akşam pik
    4.0, 3.0,                          # 22-23: gece geçiş
]
_otel = _normalize(_otel_raw)

# ── 5. Restoran — öğle + akşam yemeği pikleri ─────────────────────────────
_restoran_raw = [
    0.5, 0.3, 0.3, 0.3, 0.3, 0.5,   # 00-05: kapalı/minimal
    1.0, 1.5,                          # 06-07: hazırlık
    2.0, 2.5, 3.0,                     # 08-10: açılış hazırlık
    5.0, 5.5, 5.0,                     # 11-13: öğle pik
    2.5, 2.0, 2.0, 2.5,               # 14-17: ara dönem
    5.0, 5.5, 6.0, 5.5,               # 18-21: akşam pik
    3.0, 1.5,                          # 22-23: kapanış
]
_restoran = _normalize(_restoran_raw)

# ── 6. Soğuk Hava Deposu — 24 saat, gece ağırlıklı ───────────────────────
_soguk_hava_deposu_raw = [
    5.5, 5.5, 5.5, 5.5, 5.5, 5.5,   # 00-05: gece (ucuz elektrik, tam soğutma)
    4.5, 4.0,                          # 06-07: geçiş
    3.0, 3.0, 3.0, 3.0,               # 08-11: gündüz (azaltılmış)
    3.0,                                # 12: gündüz
    3.0, 3.0, 3.0, 3.0, 3.5,          # 13-17: gündüz
    4.0, 4.0, 4.0, 4.5,               # 18-21: akşam
    5.0, 5.5,                          # 22-23: gece başlangıcı
]
_soguk_hava_deposu = _normalize(_soguk_hava_deposu_raw)

# ── 7. Gece Ağırlıklı Üretim — 22:00-06:00 ağırlıklı ─────────────────────
_gece_agirlikli_uretim_raw = [
    6.0, 6.0, 6.0, 6.0, 6.0, 6.0,   # 00-05: tam gece üretimi
    3.0, 1.0,                          # 06-07: vardiya geçişi
    0.5, 0.5, 0.5, 0.5,               # 08-11: gündüz minimal
    0.5,                                # 12: gündüz minimal
    0.5, 0.5, 0.5, 0.5, 0.5,          # 13-17: gündüz minimal
    1.0, 1.5, 2.0, 3.0,               # 18-21: akşam geçiş
    5.5, 6.0,                          # 22-23: gece üretimi başlangıcı
]
_gece_agirlikli_uretim = _normalize(_gece_agirlikli_uretim_raw)

# ── 8. AVM — 10:00-22:00 ağırlıklı ────────────────────────────────────────
_avm_raw = [
    0.5, 0.5, 0.5, 0.5, 0.5, 0.5,   # 00-05: kapalı
    0.5, 0.8,                          # 06-07: güvenlik/temizlik
    1.5, 2.5,                          # 08-09: açılış hazırlık
    5.0, 5.0, 5.5,                     # 10-12: açık
    5.5, 5.5, 5.5, 5.5, 5.5,          # 13-17: yoğun saatler
    6.0, 6.0, 5.5, 5.0,               # 18-21: akşam pik
    2.0, 0.8,                          # 22-23: kapanış
]
_avm = _normalize(_avm_raw)

# ── 9. Akaryakıt İstasyonu — 24 saat, sabah/akşam pikleri ─────────────────
_akaryakit_istasyonu_raw = [
    2.0, 1.5, 1.5, 1.5, 2.0, 3.0,   # 00-05: gece minimal + erken sabah
    4.5, 5.5,                          # 06-07: sabah pik (işe gidiş)
    5.0, 4.5, 4.0, 3.5,               # 08-11: gündüz
    3.5,                                # 12: öğle
    3.5, 3.5, 3.5, 4.0, 4.5,          # 13-17: gündüz
    5.5, 5.0, 4.5, 4.0,               # 18-21: akşam pik (işten dönüş)
    3.0, 2.5,                          # 22-23: gece geçiş
]
_akaryakit_istasyonu = _normalize(_akaryakit_istasyonu_raw)

# ── 10. Market/Süpermarket — 08:00-22:00, öğleden sonra pik ───────────────
_market_supermarket_raw = [
    1.0, 1.0, 1.0, 1.0, 1.0, 1.0,   # 00-05: kapalı (soğutma devam)
    1.5, 2.0,                          # 06-07: açılış hazırlık
    3.5, 4.0, 4.5, 4.5,               # 08-11: açık
    4.0,                                # 12: öğle
    4.5, 5.0, 5.5, 5.5, 5.5,          # 13-17: öğleden sonra pik
    5.0, 5.0, 4.5, 4.0,               # 18-21: akşam
    2.5, 1.5,                          # 22-23: kapanış
]
_market_supermarket = _normalize(_market_supermarket_raw)

# ── 11. Hastane — 24 saat, gündüz ağırlıklı ───────────────────────────────
_hastane_raw = [
    3.0, 2.5, 2.5, 2.5, 2.5, 3.0,   # 00-05: gece (acil, yoğun bakım)
    3.5, 4.0,                          # 06-07: sabah hazırlık
    5.5, 6.0, 6.0, 5.5,               # 08-11: ameliyat/poliklinik pik
    5.0,                                # 12: öğle
    5.5, 5.5, 5.5, 5.0, 4.5,          # 13-17: öğleden sonra
    4.0, 3.5, 3.5, 3.0,               # 18-21: akşam
    3.0, 3.0,                          # 22-23: gece geçiş
]
_hastane = _normalize(_hastane_raw)

# ── 12. Tarımsal Sulama — gece ağırlıklı (22:00-06:00, ucuz saatler) ──────
_tarimsal_sulama_raw = [
    6.5, 6.5, 6.5, 6.5, 6.5, 6.5,   # 00-05: gece sulama (ucuz elektrik)
    4.0, 2.0,                          # 06-07: sabah geçiş
    0.5, 0.5, 0.5, 0.5,               # 08-11: gündüz minimal
    0.5,                                # 12: gündüz minimal
    0.5, 0.5, 0.5, 0.5, 0.5,          # 13-17: gündüz minimal
    1.0, 1.5, 2.0, 3.0,               # 18-21: akşam geçiş
    5.5, 6.5,                          # 22-23: gece sulama başlangıcı
]
_tarimsal_sulama = _normalize(_tarimsal_sulama_raw)

# Site Yönetimi — Ortak alan aydınlatma, asansör, hidrofor, otopark havalandırma
# Sabah ve akşam pikleri (ev giriş-çıkış), gece minimal, gündüz orta
_site_yonetimi_raw = [
    2.0, 1.5, 1.0, 1.0, 1.0, 1.5,    # 00-05: gece minimal (aydınlatma + hidrofor)
    3.0, 5.0, 6.0, 4.0, 3.5, 3.5,    # 06-11: sabah piki (asansör + hidrofor)
    3.0, 3.0, 3.0, 3.5, 4.0, 5.0,    # 12-17: gündüz orta
    7.0, 8.0, 7.0, 6.0, 4.0, 3.0,    # 18-23: akşam piki (aydınlatma + asansör + klima)
]
_site_yonetimi = _normalize(_site_yonetimi_raw)


# ═══════════════════════════════════════════════════════════════════════════════
# Yerleşik Şablon Listesi
# ═══════════════════════════════════════════════════════════════════════════════

BUILTIN_TEMPLATES: list[TemplateDefinition] = [
    TemplateDefinition(
        name="3_vardiya_sanayi",
        display_name="3 Vardiya Sanayi",
        description="7/24 kesintisiz üretim yapan sanayi tesisi. Saatlik tüketim hemen hemen düz dağılımlıdır.",
        hourly_weights=_3_vardiya_sanayi,
    ),
    TemplateDefinition(
        name="tek_vardiya_fabrika",
        display_name="Tek Vardiya Fabrika",
        description="07:00-18:00 arası üretim yapan fabrika. Gece saatlerinde minimal tüketim.",
        hourly_weights=_tek_vardiya_fabrika,
    ),
    TemplateDefinition(
        name="ofis",
        display_name="Ofis",
        description="08:00-18:00 mesai saatlerinde yoğun tüketim. Gece ve hafta sonu minimal.",
        hourly_weights=_ofis,
    ),
    TemplateDefinition(
        name="otel",
        display_name="Otel",
        description="24 saat açık, akşam saatlerinde (18:00-22:00) pik tüketim. Klima ve aydınlatma ağırlıklı.",
        hourly_weights=_otel,
    ),
    TemplateDefinition(
        name="restoran",
        display_name="Restoran",
        description="Öğle (11:00-14:00) ve akşam (18:00-22:00) yemek saatlerinde çift pik tüketim.",
        hourly_weights=_restoran,
    ),
    TemplateDefinition(
        name="soguk_hava_deposu",
        display_name="Soğuk Hava Deposu",
        description="24 saat soğutma, gece saatlerinde (ucuz elektrik) ağırlıklı tam kapasite çalışma.",
        hourly_weights=_soguk_hava_deposu,
    ),
    TemplateDefinition(
        name="gece_agirlikli_uretim",
        display_name="Gece Ağırlıklı Üretim",
        description="22:00-06:00 arası ağırlıklı üretim. Ucuz gece tarifesinden faydalanan tesisler.",
        hourly_weights=_gece_agirlikli_uretim,
    ),
    TemplateDefinition(
        name="avm",
        display_name="AVM",
        description="10:00-22:00 arası açık alışveriş merkezi. Akşam saatlerinde pik tüketim.",
        hourly_weights=_avm,
    ),
    TemplateDefinition(
        name="akaryakit_istasyonu",
        display_name="Akaryakıt İstasyonu",
        description="24 saat açık, sabah (06:00-08:00) ve akşam (17:00-20:00) trafik piklerinde yoğun.",
        hourly_weights=_akaryakit_istasyonu,
    ),
    TemplateDefinition(
        name="market_supermarket",
        display_name="Market / Süpermarket",
        description="08:00-22:00 arası açık, öğleden sonra (13:00-18:00) pik tüketim. Soğutma sürekli.",
        hourly_weights=_market_supermarket,
    ),
    TemplateDefinition(
        name="hastane",
        display_name="Hastane",
        description="24 saat açık, gündüz saatlerinde (08:00-17:00) ameliyat ve poliklinik piki.",
        hourly_weights=_hastane,
    ),
    TemplateDefinition(
        name="tarimsal_sulama",
        display_name="Tarımsal Sulama",
        description="Gece saatlerinde (22:00-06:00) ağırlıklı sulama. Ucuz gece tarifesinden faydalanır.",
        hourly_weights=_tarimsal_sulama,
    ),
    TemplateDefinition(
        name="site_yonetimi",
        display_name="Site Yönetimi",
        description="Ortak alan aydınlatma, asansör, hidrofor, otopark. Sabah ve akşam ev giriş-çıkış pikleri.",
        hourly_weights=_site_yonetimi,
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
