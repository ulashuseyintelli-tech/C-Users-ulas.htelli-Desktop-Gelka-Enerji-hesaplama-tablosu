"""
PTF Seed Data Loader

EPİAŞ'tan alınan gerçek PTF değerleri (2024-01 → 2026-02).
Kaynak: EPİAŞ Şeffaflık Platformu - Gün Öncesi Piyasası Aylık PTF

Kullanım:
    python -m backend.app.seed_market_prices
    
    veya Python'dan:
    from backend.app.seed_market_prices import seed_market_prices
    seed_market_prices(db_session)

Status Kuralı (Europe/Istanbul timezone):
- period == current_period(TR) → status="provisional"
- period < current_period(TR) → status="final"
"""

import logging
from datetime import datetime
from typing import List, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Europe/Istanbul timezone
TR_TZ = ZoneInfo("Europe/Istanbul")


# ═══════════════════════════════════════════════════════════════════════════════
# EPİAŞ PTF SEED DATA (TL/MWh)
# Kaynak: EPİAŞ Şeffaflık Platformu - 06.02.2026 tarihli ekran görüntüsü
# ═══════════════════════════════════════════════════════════════════════════════

SEED_DATA: List[Tuple[str, float]] = [
    # 2024
    ("2024-01", 1942.90),
    ("2024-02", 1957.68),
    ("2024-03", 2190.11),
    ("2024-04", 1764.04),
    ("2024-05", 2047.32),
    ("2024-06", 2095.23),
    ("2024-07", 2588.83),
    ("2024-08", 2574.15),
    ("2024-09", 2395.78),
    ("2024-10", 2335.71),
    ("2024-11", 2463.14),
    ("2024-12", 2446.22),
    # 2025
    ("2025-01", 2508.80),
    ("2025-02", 2478.28),
    ("2025-03", 2183.83),
    ("2025-04", 2452.67),
    ("2025-05", 2458.15),
    ("2025-06", 2202.23),
    ("2025-07", 2965.16),
    ("2025-08", 2939.24),
    ("2025-09", 2729.02),
    ("2025-10", 2739.50),
    ("2025-11", 2784.10),
    ("2025-12", 2973.04),
    # 2026
    ("2026-01", 2894.92),
    ("2026-02", 2536.21),  # Ay devam ediyor (06.02.2026 itibarıyla)
]


def get_current_period_tr() -> str:
    """Get current period in YYYY-MM format (Europe/Istanbul timezone)."""
    now_tr = datetime.now(TR_TZ)
    return now_tr.strftime("%Y-%m")


def determine_status(period: str) -> str:
    """
    Determine status based on period vs current month.
    
    Rules:
    - period == current_period(TR) → "provisional"
    - period < current_period(TR) → "final"
    - period > current_period(TR) → "provisional" (future, shouldn't happen in seed)
    """
    current = get_current_period_tr()
    
    if period >= current:
        return "provisional"
    return "final"


def seed_market_prices(db, force_update: bool = False, dry_run: bool = False) -> dict:
    """
    Load seed data into market_reference_prices table.
    
    Args:
        db: SQLAlchemy session
        force_update: If True, update existing records. If False, skip existing.
        dry_run: If True, don't commit changes, just return what would happen.
    
    Returns:
        {
            "inserted": int,
            "updated": int,
            "skipped": int,
            "errors": List[str],
            "details": List[dict]
        }
    """
    from .database import MarketReferencePrice
    
    result = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "details": []
    }
    
    captured_at = datetime.utcnow()
    current_period = get_current_period_tr()
    
    logger.info(f"Seeding market prices. Current period (TR): {current_period}")
    logger.info(f"Force update: {force_update}, Dry run: {dry_run}")
    
    for period, ptf_value in SEED_DATA:
        status = determine_status(period)
        
        detail = {
            "period": period,
            "ptf_tl_per_mwh": ptf_value,
            "status": status,
            "action": None
        }
        
        try:
            # Check if record exists
            existing = db.query(MarketReferencePrice).filter(
                MarketReferencePrice.price_type == "PTF",
                MarketReferencePrice.period == period
            ).first()
            
            if existing:
                if force_update:
                    # Update existing record
                    existing.ptf_tl_per_mwh = ptf_value
                    existing.status = status
                    existing.source = "seed"
                    existing.captured_at = captured_at
                    existing.updated_by = "seed_loader"
                    existing.change_reason = "Seed data refresh"
                    
                    detail["action"] = "updated"
                    result["updated"] += 1
                    logger.debug(f"Updated: {period} = {ptf_value} TL/MWh ({status})")
                else:
                    detail["action"] = "skipped"
                    result["skipped"] += 1
                    logger.debug(f"Skipped (exists): {period}")
            else:
                # Insert new record
                new_record = MarketReferencePrice(
                    price_type="PTF",
                    period=period,
                    ptf_tl_per_mwh=ptf_value,
                    yekdem_tl_per_mwh=0,  # YEKDEM not included in seed
                    status=status,
                    source="seed",
                    captured_at=captured_at,
                    source_note="EPİAŞ Şeffaflık Platformu - Seed Data",
                    updated_by="seed_loader",
                    is_locked=0
                )
                db.add(new_record)
                
                detail["action"] = "inserted"
                result["inserted"] += 1
                logger.debug(f"Inserted: {period} = {ptf_value} TL/MWh ({status})")
                
        except Exception as e:
            detail["action"] = "error"
            detail["error"] = str(e)
            result["errors"].append(f"{period}: {str(e)}")
            logger.error(f"Error seeding {period}: {e}")
        
        result["details"].append(detail)
    
    if not dry_run:
        try:
            db.commit()
            logger.info(f"Seed complete: {result['inserted']} inserted, {result['updated']} updated, {result['skipped']} skipped")
        except Exception as e:
            db.rollback()
            result["errors"].append(f"Commit failed: {str(e)}")
            logger.error(f"Commit failed: {e}")
    else:
        db.rollback()
        logger.info(f"Dry run complete: {result['inserted']} would insert, {result['updated']} would update, {result['skipped']} would skip")
    
    return result


def main():
    """CLI entry point for seeding market prices."""
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="Seed market prices from EPİAŞ data")
    parser.add_argument("--force", action="store_true", help="Update existing records")
    parser.add_argument("--dry-run", action="store_true", help="Don't commit changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    # Get database session
    from .database import SessionLocal
    
    db = SessionLocal()
    try:
        result = seed_market_prices(db, force_update=args.force, dry_run=args.dry_run)
        
        print(f"\n{'='*50}")
        print(f"Seed Results:")
        print(f"  Inserted: {result['inserted']}")
        print(f"  Updated:  {result['updated']}")
        print(f"  Skipped:  {result['skipped']}")
        print(f"  Errors:   {len(result['errors'])}")
        
        if result['errors']:
            print(f"\nErrors:")
            for err in result['errors']:
                print(f"  - {err}")
            sys.exit(1)
        
        sys.exit(0)
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
