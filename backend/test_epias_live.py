"""
EPİAŞ API Live Test

Bu script gerçek EPİAŞ API'sini test eder.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from app.epias_client import (
    EpiasClient,
    fetch_market_prices_from_epias,
    EpiasApiError,
    EpiasDataNotFoundError,
)


async def test_ptf():
    """PTF verisi çek"""
    print("\n" + "="*60)
    print("EPİAŞ PTF Test")
    print("="*60)
    
    period = "2025-01"
    print(f"\nDönem: {period}")
    
    try:
        async with EpiasClient() as client:
            ptf_data = await client.get_monthly_ptf_average(period)
            
            print(f"\n✅ PTF Verisi Alındı:")
            print(f"   Ortalama: {ptf_data.average_tl_per_mwh:.2f} TL/MWh")
            print(f"   Min: {ptf_data.min_tl_per_mwh:.2f} TL/MWh")
            print(f"   Max: {ptf_data.max_tl_per_mwh:.2f} TL/MWh")
            print(f"   Veri Noktası: {ptf_data.data_points}")
            print(f"   Kaynak: {ptf_data.source}")
            
    except EpiasDataNotFoundError as e:
        print(f"\n⚠️ Veri bulunamadı: {e}")
    except EpiasApiError as e:
        print(f"\n❌ API Hatası: {e}")
    except Exception as e:
        print(f"\n❌ Beklenmeyen Hata: {e}")


async def test_yekdem():
    """YEKDEM verisi çek"""
    print("\n" + "="*60)
    print("EPİAŞ YEKDEM Test")
    print("="*60)
    
    period = "2025-01"
    print(f"\nDönem: {period}")
    
    try:
        async with EpiasClient() as client:
            yekdem_data = await client.get_yekdem_unit_price(period)
            
            print(f"\n✅ YEKDEM Verisi Alındı:")
            print(f"   Birim Bedel: {yekdem_data.unit_cost_tl_per_mwh:.2f} TL/MWh")
            print(f"   Kaynak: {yekdem_data.source}")
            
    except EpiasDataNotFoundError as e:
        print(f"\n⚠️ Veri bulunamadı: {e}")
    except EpiasApiError as e:
        print(f"\n❌ API Hatası: {e}")
    except Exception as e:
        print(f"\n❌ Beklenmeyen Hata: {e}")


async def test_combined():
    """PTF + YEKDEM birlikte çek"""
    print("\n" + "="*60)
    print("EPİAŞ Combined Test (PTF + YEKDEM)")
    print("="*60)
    
    period = "2025-01"
    print(f"\nDönem: {period}")
    
    try:
        result = await fetch_market_prices_from_epias(period)
        
        print(f"\n📊 Sonuç:")
        print(f"   PTF: {result.ptf_tl_per_mwh} TL/MWh ({result.ptf_source})")
        print(f"   YEKDEM: {result.yekdem_tl_per_mwh} TL/MWh ({result.yekdem_source})")
        print(f"   PTF Veri Noktası: {result.ptf_data_points}")
        
        if result.warnings:
            print(f"\n⚠️ Uyarılar:")
            for w in result.warnings:
                print(f"   - {w}")
                
    except Exception as e:
        print(f"\n❌ Hata: {e}")


async def test_multiple_periods():
    """Birden fazla dönem test et"""
    print("\n" + "="*60)
    print("EPİAŞ Multiple Periods Test")
    print("="*60)
    
    periods = ["2024-12", "2025-01", "2024-06"]
    
    for period in periods:
        print(f"\n--- {period} ---")
        try:
            result = await fetch_market_prices_from_epias(period)
            print(f"   PTF: {result.ptf_tl_per_mwh} TL/MWh")
            print(f"   YEKDEM: {result.yekdem_tl_per_mwh} TL/MWh")
        except Exception as e:
            print(f"   ❌ Hata: {e}")


async def main():
    """Ana test fonksiyonu"""
    print("\n" + "="*60)
    print("EPİAŞ Şeffaflık Platformu API Test")
    print("="*60)
    
    await test_ptf()
    await test_yekdem()
    await test_combined()
    # await test_multiple_periods()  # Çok fazla istek atmamak için kapalı
    
    print("\n" + "="*60)
    print("Test Tamamlandı")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
