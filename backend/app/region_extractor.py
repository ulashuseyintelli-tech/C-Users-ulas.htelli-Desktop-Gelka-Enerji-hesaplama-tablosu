"""
Bölge Kırpma (ROI Extraction) Modülü

Fatura görselinden kritik bölgeleri kırparak OpenAI'a gönderir.
Bu yaklaşım:
1. Vision'ın dikkatini odaklar
2. Token maliyetini düşürür
3. Doğruluğu artırır

Strateji: Multi-crop hunting
- Sayfa 1'den 2-3 aday bölge kırp
- Her bölgeyi ayrı ayrı veya birlikte Vision'a gönder
- "Ödenecek Tutar" içeren bölgeyi bul
"""

import io
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class CropRegion:
    """Kırpılacak bölge tanımı."""
    name: str
    # Koordinatlar yüzde olarak (0-100)
    # Böylece farklı çözünürlüklerde çalışır
    x_percent: float  # Sol kenar
    y_percent: float  # Üst kenar
    width_percent: float
    height_percent: float


@dataclass
class CroppedImage:
    """Kırpılmış görsel ve meta bilgisi."""
    name: str
    image_bytes: bytes
    width: int
    height: int
    region: CropRegion


# ═══════════════════════════════════════════════════════════════════════════════
# CK Boğaziçi Fatura Bölgeleri (Sabit Layout)
# ═══════════════════════════════════════════════════════════════════════════════
# CK faturalarında "Fatura Özeti" kutusu genelde sağ üst bölgede
# Birden fazla aday bölge tanımlıyoruz (multi-crop hunting)

CK_BOGAZICI_REGIONS = [
    # Sağ üst - Fatura Özeti genelde burada
    CropRegion(
        name="sag_ust_fatura_ozeti",
        x_percent=45,
        y_percent=0,
        width_percent=55,
        height_percent=45
    ),
    # Sağ orta - Bazen aşağıda olabilir
    CropRegion(
        name="sag_orta",
        x_percent=45,
        y_percent=30,
        width_percent=55,
        height_percent=40
    ),
    # Tam sağ şerit - Tüm sağ taraf
    CropRegion(
        name="sag_serit",
        x_percent=50,
        y_percent=0,
        width_percent=50,
        height_percent=60
    ),
]

# Genel fatura bölgeleri (vendor-agnostic)
GENERIC_REGIONS = [
    # Sağ üst köşe - geniş
    CropRegion(
        name="sag_ust",
        x_percent=40,
        y_percent=0,
        width_percent=60,
        height_percent=50
    ),
    # Orta sağ
    CropRegion(
        name="orta_sag",
        x_percent=45,
        y_percent=25,
        width_percent=55,
        height_percent=45
    ),
    # Alt yarı (toplam genelde altta)
    CropRegion(
        name="alt_yari",
        x_percent=0,
        y_percent=50,
        width_percent=100,
        height_percent=50
    ),
]


def get_regions_for_vendor(vendor: str) -> List[CropRegion]:
    """
    Vendor'a göre kırpılacak bölgeleri döndür.
    
    Args:
        vendor: Tedarikçi adı (ck_bogazici, enerjisa, vb.)
        
    Returns:
        Kırpılacak bölge listesi
    """
    vendor_lower = vendor.lower() if vendor else ""
    
    if "ck" in vendor_lower or "bogazici" in vendor_lower or "bogazi" in vendor_lower:
        return CK_BOGAZICI_REGIONS
    
    # Diğer vendor'lar için generic bölgeler
    return GENERIC_REGIONS


def crop_region(image_bytes: bytes, region: CropRegion) -> CroppedImage:
    """
    Görselden belirtilen bölgeyi kırp.
    
    Args:
        image_bytes: Orijinal görsel
        region: Kırpılacak bölge
        
    Returns:
        Kırpılmış görsel
    """
    img = Image.open(io.BytesIO(image_bytes))
    width, height = img.size
    
    # Yüzdelik koordinatları piksel koordinatlarına çevir
    x1 = int(width * region.x_percent / 100)
    y1 = int(height * region.y_percent / 100)
    x2 = int(x1 + width * region.width_percent / 100)
    y2 = int(y1 + height * region.height_percent / 100)
    
    # Sınırları kontrol et
    x2 = min(x2, width)
    y2 = min(y2, height)
    
    # Kırp
    cropped = img.crop((x1, y1, x2, y2))
    
    # Bytes'a çevir
    buffer = io.BytesIO()
    if cropped.mode == "RGBA":
        cropped = cropped.convert("RGB")
    cropped.save(buffer, format="PNG", optimize=True)
    
    crop_bytes = buffer.getvalue()
    
    logger.info(
        f"Region cropped: {region.name} | "
        f"coords=({x1},{y1})-({x2},{y2}) | "
        f"size={cropped.width}x{cropped.height} | "
        f"bytes={len(crop_bytes)}"
    )
    
    return CroppedImage(
        name=region.name,
        image_bytes=crop_bytes,
        width=cropped.width,
        height=cropped.height,
        region=region
    )


def crop_multiple_regions(
    image_bytes: bytes, 
    regions: List[CropRegion]
) -> List[CroppedImage]:
    """
    Görselden birden fazla bölge kırp.
    
    Args:
        image_bytes: Orijinal görsel
        regions: Kırpılacak bölgeler
        
    Returns:
        Kırpılmış görseller listesi
    """
    cropped_images = []
    
    for region in regions:
        try:
            cropped = crop_region(image_bytes, region)
            cropped_images.append(cropped)
        except Exception as e:
            logger.warning(f"Region crop failed: {region.name} - {e}")
    
    return cropped_images


def extract_payable_total_from_crops(
    cropped_images: List[CroppedImage],
    extract_func
) -> Tuple[Optional[float], Optional[str], Optional[CroppedImage]]:
    """
    Kırpılmış görsellerden "Ödenecek Tutar" değerini bul.
    
    Multi-crop hunting stratejisi:
    1. Her crop'u sırayla dene
    2. İlk başarılı sonucu döndür
    3. Hiçbirinde bulunamazsa None döndür
    
    Args:
        cropped_images: Kırpılmış görseller
        extract_func: Extraction fonksiyonu (OpenAI çağrısı)
        
    Returns:
        (payable_total, evidence, winning_crop)
    """
    for crop in cropped_images:
        try:
            logger.info(f"Trying crop: {crop.name}")
            
            # Extraction fonksiyonunu çağır
            result = extract_func(crop.image_bytes)
            
            # Sonucu kontrol et
            if result and result.get("payable_total"):
                value = result["payable_total"]
                evidence = result.get("evidence", crop.name)
                
                logger.info(f"Found payable_total in {crop.name}: {value}")
                return value, evidence, crop
                
        except Exception as e:
            logger.warning(f"Extraction failed for {crop.name}: {e}")
    
    logger.warning("No payable_total found in any crop")
    return None, None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Minimal Extraction Prompt (Sadece Ödenecek Tutar için)
# ═══════════════════════════════════════════════════════════════════════════════

PAYABLE_TOTAL_PROMPT = """
Bu görsel bir Türk elektrik faturasının bir bölümü.

GÖREV: "Ödenecek Tutar" veya "Genel Toplam" değerini bul.

KRİTİK KURALLAR:
1. "Ödenecek Tutar" = KDV DAHİL son tutar (müşterinin ödeyeceği)
2. "KDV Hariç Toplam" veya "Ara Toplam" DEĞİL - bunlar yanlış!
3. En BÜYÜK tutarı ara (KDV dahil tutar her zaman en büyüktür)
4. Türkçe sayı formatı: 593.740,00 (nokta=binlik, virgül=ondalık)
5. Bu formatı AYNEN koru, dönüştürme

ARAMA ÖNCELİĞİ:
1. "Ödenecek Tutar" etiketi
2. "Genel Toplam" etiketi
3. "Toplam Tutar" etiketi (KDV dahil olanı seç)
4. "TOPLAM" etiketi (en büyük değer)

JSON FORMATI:
{
    "payable_total": "593.740,00",
    "currency": "TL",
    "confidence": 0.95,
    "evidence": "Ödenecek Tutar: 593.740,00 TL",
    "found_label": "Ödenecek Tutar"
}

Bulamazsan:
{
    "payable_total": null,
    "currency": null,
    "confidence": 0,
    "evidence": null,
    "found_label": null,
    "reason": "Ödenecek Tutar bulunamadı"
}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-Field Extraction Prompt (Tüm kritik alanlar için)
# ═══════════════════════════════════════════════════════════════════════════════

MULTI_FIELD_PROMPT = """
Bu görsel bir Türk elektrik faturasının "Fatura Özeti" veya "Toplam" bölümü.

GÖREV: Aşağıdaki değerleri bul ve çıkar.

ARANAN DEĞERLER:
1. payable_total: "Ödenecek Tutar" veya "Genel Toplam" (KDV DAHİL)
2. vat_amount: "KDV" veya "KDV Tutarı" (%20 vergi)
3. vat_base: "KDV Matrahı" veya "Matrah" (KDV hariç toplam)
4. energy_total: "Enerji Bedeli" veya "Aktif Enerji Bedeli"
5. distribution_total: "Dağıtım Bedeli" veya "Elk. Dağıtım Bedeli"
6. consumption_kwh: "Toplam Tüketim" veya "Tüketim (kWh)"

KRİTİK KURALLAR:
1. Türkçe sayı formatı: 593.740,00 (nokta=binlik, virgül=ondalık)
2. Bu formatı AYNEN koru, dönüştürme yapma
3. Bulamadığın alanlar için null döndür
4. Her alan için confidence (0-1) ver
5. evidence olarak gördüğün metni yaz

JSON FORMATI:
{
    "payable_total": {"value": "593.740,00", "confidence": 0.95, "evidence": "Ödenecek Tutar: 593.740,00 TL"},
    "vat_amount": {"value": "98.956,24", "confidence": 0.90, "evidence": "KDV (%20): 98.956,24 TL"},
    "vat_base": {"value": "494.783,76", "confidence": 0.85, "evidence": "KDV Matrahı: 494.783,76 TL"},
    "energy_total": {"value": "506.738,26", "confidence": 0.80, "evidence": "Enerji Bedeli: 506.738,26 TL"},
    "distribution_total": {"value": "86.952,60", "confidence": 0.80, "evidence": "Dağıtım Bedeli: 86.952,60 TL"},
    "consumption_kwh": {"value": "116.145,63", "confidence": 0.85, "evidence": "Toplam Tüketim: 116.145,63 kWh"}
}

Bulamadığın alanlar için:
{
    "field_name": {"value": null, "confidence": 0, "evidence": null, "reason": "Bulunamadı"}
}
"""


@dataclass
class MultiFieldResult:
    """Multi-field extraction sonucu."""
    payable_total: Optional[float] = None
    vat_amount: Optional[float] = None
    vat_base: Optional[float] = None
    energy_total: Optional[float] = None
    distribution_total: Optional[float] = None
    consumption_kwh: Optional[float] = None
    
    # Confidence değerleri
    payable_total_confidence: float = 0.0
    vat_amount_confidence: float = 0.0
    vat_base_confidence: float = 0.0
    energy_total_confidence: float = 0.0
    distribution_total_confidence: float = 0.0
    consumption_kwh_confidence: float = 0.0
    
    # Kaynak bilgisi
    source_region: str = ""
    
    def to_dict(self) -> dict:
        return {
            "payable_total": self.payable_total,
            "vat_amount": self.vat_amount,
            "vat_base": self.vat_base,
            "energy_total": self.energy_total,
            "distribution_total": self.distribution_total,
            "consumption_kwh": self.consumption_kwh,
            "confidences": {
                "payable_total": self.payable_total_confidence,
                "vat_amount": self.vat_amount_confidence,
                "vat_base": self.vat_base_confidence,
                "energy_total": self.energy_total_confidence,
                "distribution_total": self.distribution_total_confidence,
                "consumption_kwh": self.consumption_kwh_confidence,
            },
            "source_region": self.source_region,
        }


def create_multi_field_extraction_func(openai_client, model: str = "gpt-4o"):
    """
    Multi-field extraction fonksiyonu oluştur.
    
    Args:
        openai_client: OpenAI client
        model: Kullanılacak model
        
    Returns:
        Extraction fonksiyonu
    """
    import base64
    import json
    from .parse_tr import parse_tr_float
    
    def extract(image_bytes: bytes) -> MultiFieldResult:
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MULTI_FIELD_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.1
        )
        
        raw = response.choices[0].message.content
        data = json.loads(raw)
        
        result = MultiFieldResult()
        
        # Her alanı parse et
        for field in ["payable_total", "vat_amount", "vat_base", "energy_total", "distribution_total", "consumption_kwh"]:
            field_data = data.get(field, {})
            if field_data and field_data.get("value"):
                value = parse_tr_float(field_data["value"])
                confidence = field_data.get("confidence", 0.5)
                
                setattr(result, field, value)
                setattr(result, f"{field}_confidence", confidence)
        
        return result
    
    return extract


def extract_multi_fields_from_crops(
    cropped_images: List[CroppedImage],
    extract_func
) -> MultiFieldResult:
    """
    Kırpılmış görsellerden tüm kritik alanları çıkar.
    
    Strateji:
    1. Her crop'u dene
    2. En çok alan bulan crop'u seç
    3. Veya: Her alan için en yüksek confidence'lı değeri seç
    
    Args:
        cropped_images: Kırpılmış görseller
        extract_func: Multi-field extraction fonksiyonu
        
    Returns:
        MultiFieldResult with best values from all crops
    """
    best_result = MultiFieldResult()
    best_field_count = 0
    
    for crop in cropped_images:
        try:
            logger.info(f"Multi-field extraction from: {crop.name}")
            
            result = extract_func(crop.image_bytes)
            result.source_region = crop.name
            
            # Bulunan alan sayısını hesapla
            field_count = sum([
                1 if result.payable_total else 0,
                1 if result.vat_amount else 0,
                1 if result.vat_base else 0,
                1 if result.energy_total else 0,
                1 if result.distribution_total else 0,
                1 if result.consumption_kwh else 0,
            ])
            
            logger.info(f"Found {field_count} fields in {crop.name}")
            
            # En çok alan bulan crop'u seç
            if field_count > best_field_count:
                best_result = result
                best_field_count = field_count
                
            # Eğer tüm alanlar bulunduysa dur
            if field_count >= 5:
                logger.info(f"All fields found in {crop.name}, stopping")
                break
                
        except Exception as e:
            logger.warning(f"Multi-field extraction failed for {crop.name}: {e}")
    
    logger.info(f"Best result from {best_result.source_region}: {best_field_count} fields")
    return best_result


def create_payable_total_extraction_func(openai_client, model: str = "gpt-4o"):
    """
    Ödenecek Tutar extraction fonksiyonu oluştur.
    
    Args:
        openai_client: OpenAI client
        model: Kullanılacak model
        
    Returns:
        Extraction fonksiyonu
    """
    import base64
    import json
    
    def extract(image_bytes: bytes) -> dict:
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PAYABLE_TOTAL_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "high"  # Yüksek detay - küçük crop için önemli
                            }
                        }
                    ]
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0.1
        )
        
        raw = response.choices[0].message.content
        return json.loads(raw)
    
    return extract
