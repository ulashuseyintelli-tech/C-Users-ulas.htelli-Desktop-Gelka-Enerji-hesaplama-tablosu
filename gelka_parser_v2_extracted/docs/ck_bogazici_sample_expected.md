# CK Boğaziçi örnek (BBE2025000297356) — beklenen okuma davranışı

Bu örnekte sayfa image tabanlıdır (pdfplumber text=boş).

Bu yüzden:
- "Günlük(kWh)" alanları (4192,497 / 4019,744 gibi) **toplam tüketim değildir**.
- Toplam tüketim ve enerji bedeli **Fatura Detayı** satırlarından türetilmelidir.

Beklenen:
- `total_kwh` kalem qty'larının toplamı olmalı (pozitif/negatif satırlar dahil).
- `totals.total_tl` "Fatura Tutarı"ndan gelmeli.
- `totals.payable_tl` "Ödenecek Tutar"dan gelmeli.
- Validator: `TOTAL_CALC_MISMATCH` yoksa PASS.
