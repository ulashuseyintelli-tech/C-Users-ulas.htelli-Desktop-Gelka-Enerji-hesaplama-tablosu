"""
Invoice Reconciliation Engine — Phase 1.

Saatlik tüketim Excel dosyalarını parse eder, aylara böler, T1/T2/T3 hesaplar,
fatura değerleriyle karşılaştırır ve saatlik PTF ile maliyet hesaplaması yapar.

Modüller:
- schemas: Pydantic request/response modelleri
- parser: Excel parse + format detection (pluggable provider)
- splitter: Aylık bölme (monthly split, DST-aware)
- classifier: T1/T2/T3 sınıflandırma (classify_hour wrapper)
- reconciler: Fatura mutabakat doğrulaması
- cost_engine: PTF/YEKDEM maliyet hesaplama
- comparator: Fatura vs Gelka teklifi karşılaştırma
- report_builder: Rapor birleştirme ve formatlama
- router: FastAPI endpoint'leri

Implementation Constraints:
- IC-1: Tüm iç hesaplamalar Decimal ile yapılır
- IC-2: Timestamp'lar Europe/Istanbul'a normalize edilir
- IC-3: Month split DST-aware (23h/25h günler)
- IC-4: Reconciliation output: excel_total_kwh, invoice_total_kwh, delta_kwh, delta_pct, severity
- IC-5: Parser pluggable provider mimarisi (BaseFormatProvider + registry)
"""
