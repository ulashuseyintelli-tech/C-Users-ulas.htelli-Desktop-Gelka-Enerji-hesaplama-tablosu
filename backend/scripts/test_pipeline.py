#!/usr/bin/env python3
"""
Test Pipeline Runner
====================
PDF/görsel faturalarını tam pipeline'dan geçirir ve sonuçları raporlar.

Kullanım:
    python scripts/test_pipeline.py path/to/invoice.pdf
    python scripts/test_pipeline.py path/to/invoices/  # klasör
    python scripts/test_pipeline.py --all  # workspace'deki tüm PDF'ler

Çıktı:
    - Extraction sonuçları
    - Validation sonuçları
    - Sanity check
    - Önerilen düzeltmeler
"""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.image_prep import preprocess_image_bytes
from app.pdf_render import render_pdf_first_page
from app.extractor import extract_invoice_data, ExtractionError
from app.validator import validate_extraction


def process_file(file_path: str, verbose: bool = True) -> dict:
    """
    Tek dosyayı pipeline'dan geçir.
    
    Returns:
        {
            "file": str,
            "success": bool,
            "extraction": dict | None,
            "validation": dict | None,
            "error": str | None,
            "duration_ms": int
        }
    """
    start_time = datetime.now()
    result = {
        "file": file_path,
        "success": False,
        "extraction": None,
        "validation": None,
        "error": None,
        "duration_ms": 0
    }
    
    try:
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Dosya bulunamadı: {file_path}")
        
        suffix = file_path.suffix.lower()
        
        # 1) Dosyayı oku ve preprocess et
        if verbose:
            print(f"\n{'='*60}")
            print(f"📄 Dosya: {file_path.name}")
            print(f"{'='*60}")
        
        if suffix == ".pdf":
            # PDF → Page1 render → Preprocess
            if verbose:
                print("📑 PDF render ediliyor...")
            
            temp_png = str(file_path.with_suffix("_temp_p1.png"))
            render_pdf_first_page(str(file_path), temp_png, scale=2.5)
            
            with open(temp_png, "rb") as f:
                image_bytes = f.read()
            
            # Cleanup temp file
            try:
                os.remove(temp_png)
            except:
                pass
            
            # Preprocess
            processed_bytes, content_type = preprocess_image_bytes(
                image_bytes, max_width=2200, jpeg_quality=88, output_format="JPEG"
            )
            
        elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
            # Görsel → Preprocess
            with open(file_path, "rb") as f:
                image_bytes = f.read()
            
            processed_bytes, content_type = preprocess_image_bytes(
                image_bytes, max_width=2000, jpeg_quality=85, output_format="JPEG"
            )
        else:
            raise ValueError(f"Desteklenmeyen dosya formatı: {suffix}")
        
        if verbose:
            print(f"✅ Preprocess tamamlandı ({len(processed_bytes):,} bytes)")
        
        # 2) Extraction
        if verbose:
            print("🔍 Extraction başlıyor (OpenAI Vision)...")
        
        extraction = extract_invoice_data(processed_bytes, content_type)
        result["extraction"] = extraction.model_dump()
        
        if verbose:
            print(f"✅ Extraction tamamlandı")
            print(f"   Vendor: {extraction.vendor}")
            print(f"   Dönem: {extraction.invoice_period}")
            print(f"   Tüketim: {extraction.consumption_kwh.value} kWh")
            print(f"   Birim Fiyat: {extraction.current_active_unit_price_tl_per_kwh.value} TL/kWh")
            if extraction.meta:
                print(f"   Fatura Tipi: {extraction.meta.invoice_type_guess}")
        
        # 3) Validation
        if verbose:
            print("🔎 Validation başlıyor...")
        
        validation = validate_extraction(extraction)
        result["validation"] = validation.model_dump()
        
        if verbose:
            print(f"✅ Validation tamamlandı")
            print(f"   Ready for Pricing: {validation.is_ready_for_pricing}")
            print(f"   Missing Fields: {validation.missing_fields}")
            print(f"   Errors: {len(validation.errors)}")
            print(f"   Warnings: {len(validation.warnings)}")
            
            if validation.sanity_check:
                sc = validation.sanity_check
                print(f"\n   📊 Sanity Check:")
                print(f"      Energy Est: {sc.energy_est_tl:,.2f} TL" if sc.energy_est_tl else "      Energy Est: N/A")
                print(f"      Total Est: {sc.total_est_tl:,.2f} TL" if sc.total_est_tl else "      Total Est: N/A")
                print(f"      Invoice Total: {sc.invoice_total_with_vat_tl:,.2f} TL" if sc.invoice_total_with_vat_tl else "      Invoice Total: N/A")
                print(f"      Delta: {sc.delta_ratio:.1f}%" if sc.delta_ratio is not None else "      Delta: N/A")
            
            if validation.suggested_fixes:
                print(f"\n   💡 Önerilen Düzeltmeler:")
                for fix in validation.suggested_fixes:
                    print(f"      {fix.field_name}: {fix.suggested_value} ({fix.basis})")
            
            if validation.questions:
                print(f"\n   ❓ Sorulacak Alanlar:")
                for q in validation.questions:
                    print(f"      {q.field_name}: {q.why_needed}")
        
        result["success"] = True
        
    except ExtractionError as e:
        result["error"] = f"Extraction Error: {str(e)}"
        if verbose:
            print(f"❌ Extraction hatası: {e}")
    except Exception as e:
        result["error"] = str(e)
        if verbose:
            print(f"❌ Hata: {e}")
    
    result["duration_ms"] = int((datetime.now() - start_time).total_seconds() * 1000)
    
    if verbose:
        print(f"\n⏱️ Süre: {result['duration_ms']} ms")
    
    return result


def find_invoice_files(path: str) -> list[str]:
    """Klasördeki tüm fatura dosyalarını bul."""
    path = Path(path)
    
    if path.is_file():
        return [str(path)]
    
    if path.is_dir():
        files = []
        for ext in ["*.pdf", "*.png", "*.jpg", "*.jpeg"]:
            files.extend(path.glob(ext))
            files.extend(path.glob(f"**/{ext}"))  # recursive
        return [str(f) for f in files]
    
    return []


def main():
    parser = argparse.ArgumentParser(description="Fatura Pipeline Test Runner")
    parser.add_argument("path", nargs="?", help="Dosya veya klasör yolu")
    parser.add_argument("--all", action="store_true", help="Workspace'deki tüm PDF'leri test et")
    parser.add_argument("--json", action="store_true", help="JSON çıktı")
    parser.add_argument("--quiet", "-q", action="store_true", help="Sessiz mod")
    
    args = parser.parse_args()
    
    # Dosyaları bul
    if args.all:
        # Workspace root'taki PDF'ler
        workspace_root = Path(__file__).parent.parent.parent
        files = find_invoice_files(str(workspace_root))
    elif args.path:
        files = find_invoice_files(args.path)
    else:
        parser.print_help()
        return
    
    if not files:
        print("❌ Hiç dosya bulunamadı")
        return
    
    # Her dosyayı işle
    results = []
    for file_path in files:
        result = process_file(file_path, verbose=not args.quiet and not args.json)
        results.append(result)
    
    # Özet
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(f"\n{'='*60}")
        print("📊 ÖZET")
        print(f"{'='*60}")
        
        success_count = sum(1 for r in results if r["success"])
        total_count = len(results)
        total_time = sum(r["duration_ms"] for r in results)
        
        print(f"Toplam: {total_count} dosya")
        print(f"Başarılı: {success_count}")
        print(f"Başarısız: {total_count - success_count}")
        print(f"Toplam süre: {total_time:,} ms")
        
        if total_count - success_count > 0:
            print(f"\n❌ Başarısız dosyalar:")
            for r in results:
                if not r["success"]:
                    print(f"   {r['file']}: {r['error']}")


if __name__ == "__main__":
    main()
