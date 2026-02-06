"""
EPİAŞ Mock Client Test

Mock client ile PTF/YEKDEM verilerini test et.
"""

import asyncio
import sys
sys.path.insert(0, '.')

from app.epias_client import MockEpiasClient, fetch_market_prices_from_epias


async def test_mock_client():
    """Mock client test"""
    print("\n" + "="*60)
    print("EPİAŞ Mock Client Test")
    print("="*60)
    
    client = MockEpiasClient()
    
    # Test dönemleri
    periods = ["2024-12", "2025-01", "2025-02"]
    
    for period in periods:
        print(f"\n--- {period} ---")
        
        # PTF
        ptf = await client.get_monthly_ptf_average(period)
        print(f"   PTF: {ptf.average_tl_per_mwh:.2f} TL/MWh")
        print(f"   Min: {ptf.min_tl_per_mwh:.2f}, Max: {ptf.max_tl_per_mwh:.2f}")
        
        # YEKDEM
        yekdem = await client.get_yekdem_unit_price(period)
        print(f"   YEKDEM: {yekdem.unit_cost_tl_per_mwh:.2f} TL/MWh")
        
        # Combined
        prices = await client.get_market_prices(period)
        print(f"   Source: {prices.ptf_source}")


async def test_convenience_function():
    """Convenience function test"""
    print("\n" + "="*60)
    print("Convenience Function Test (use_mock=True)")
    print("="*60)
    
    period = "2025-01"
    result = await fetch_market_prices_from_epias(period, use_mock=True)
    
    print(f"\nDönem: {period}")
    print(f"PTF: {result.ptf_tl_per_mwh} TL/MWh ({result.ptf_source})")
    print(f"YEKDEM: {result.yekdem_tl_per_mwh} TL/MWh ({result.yekdem_source})")
    print(f"Veri Noktası: {result.ptf_data_points}")
    
    if result.warnings:
        print(f"Uyarılar: {result.warnings}")


async def main():
    await test_mock_client()
    await test_convenience_function()
    
    print("\n" + "="*60)
    print("✅ Mock Client Test Tamamlandı")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
