/**
 * Bayi Komisyon Raporu PDF Üretici
 * 
 * HTML template oluşturup tarayıcının print API'si ile PDF'e çevirir.
 * Türkçe karakter sorunu yok.
 */

export interface BayiRaporData {
  customerName: string;
  contactPerson?: string;
  invoicePeriod: string;
  raporTarihi: string;
  consumptionKwh: number;
  ptfTlPerMwh: number;
  yekdemTlPerMwh: number;
  multiplier: number;
  bayiPoints: number;        // Bayi puan payı (ör: 2)
  gelkaPoints: number;       // Gelka puan payı (ör: 8)
  totalMarginPoints: number; // Toplam marj puanı (ör: 10)
  segmentName: string;       // Segment adı (ör: "Yüksek")
  offerEnergyBase: number;
  supplierProfitTl: number;
  bayiCommissionTl: number;
  gelkaNetProfitTl: number;
  offerTotalWithVatTl: number;
  currentTotalWithVatTl: number;
  savingsRatio: number;
}

const fmt = (n: number, d = 2): string =>
  n.toLocaleString('tr-TR', { minimumFractionDigits: d, maximumFractionDigits: d });

const fmtC = (n: number): string => `₺${fmt(n)}`;

export function generateBayiRaporPdf(data: BayiRaporData): void {
  const marjPuan = data.totalMarginPoints;
  const birimMaliyet = (data.ptfTlPerMwh + data.yekdemTlPerMwh) / 1000;
  const teklifBirimFiyat = birimMaliyet * data.multiplier;
  const tasarruf = data.currentTotalWithVatTl - data.offerTotalWithVatTl;

  const html = `<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>Bayi Komisyon Raporu - ${data.customerName}</title>
<style>
  @page { size: A4; margin: 15mm 20mm; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', Tahoma, Arial, sans-serif; font-size: 11px; color: #333; line-height: 1.5; }
  .header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #16a34a; padding-bottom: 8px; margin-bottom: 16px; }
  .header-left h1 { font-size: 18px; color: #16a34a; font-weight: 700; }
  .header-left p { font-size: 12px; color: #666; }
  .header-right { text-align: right; font-size: 9px; color: #999; }
  .header-right .gizli { color: #e11d48; font-weight: 600; font-size: 10px; }
  
  .section { margin-bottom: 14px; }
  .section-title { background: #f3f4f6; padding: 5px 10px; font-weight: 600; font-size: 11px; color: #374151; border-left: 3px solid #16a34a; margin-bottom: 6px; }
  
  table { width: 100%; border-collapse: collapse; font-size: 10px; }
  .info-table td { padding: 3px 8px; }
  .info-table td:first-child { font-weight: 600; width: 140px; color: #555; }
  
  .komisyon-table { border: 1px solid #e5e7eb; }
  .komisyon-table th { background: #f97316; color: white; padding: 8px 12px; text-align: left; font-size: 11px; }
  .komisyon-table td { padding: 7px 12px; border-bottom: 1px solid #f3f4f6; }
  .komisyon-table tr:last-child td { border-bottom: none; }
  .komisyon-table .bayi-row td { color: #c2410c; }
  .komisyon-table .gelka-row td { color: #16a34a; font-weight: 700; }
  .komisyon-table .toplam-row td { font-weight: 600; }
  .text-right { text-align: right; }
  .text-center { text-align: center; }
  
  .ozet-table td { padding: 3px 8px; }
  .ozet-table td:first-child { font-weight: 600; color: #555; }
  .ozet-table td:last-child { text-align: right; font-weight: 600; }
  
  .hesap-detay { margin-top: 16px; padding: 10px; background: #fafafa; border: 1px solid #e5e7eb; border-radius: 4px; font-size: 9px; color: #888; font-family: 'Consolas', monospace; line-height: 1.8; }
  
  .footer { position: fixed; bottom: 0; left: 0; right: 0; border-top: 1px solid #e5e7eb; padding: 6px 20mm; display: flex; justify-content: space-between; font-size: 8px; color: #ccc; }

  @media print {
    body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
    .no-print { display: none; }
  }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>⚡ GELKA ENERJİ</h1>
    <p>Bayi Komisyon Raporu</p>
  </div>
  <div class="header-right">
    <div class="gizli">GİZLİ — DAHİLİ KULLANIM</div>
    <div>Rapor Tarihi: ${data.raporTarihi}</div>
  </div>
</div>

<div class="section">
  <div class="section-title">Müşteri Bilgileri</div>
  <table class="info-table">
    <tr><td>Firma</td><td>${data.customerName}</td></tr>
    <tr><td>Yetkili</td><td>${data.contactPerson || '-'}</td></tr>
    <tr><td>Fatura Dönemi</td><td>${data.invoicePeriod}</td></tr>
    <tr><td>Tüketim</td><td>${fmt(data.consumptionKwh, 0)} kWh</td></tr>
  </table>
</div>

<div class="section">
  <div class="section-title">Teklif Parametreleri</div>
  <table class="info-table">
    <tr><td>PTF (Ağırlıklı Ortalama)</td><td>${fmt(data.ptfTlPerMwh)} TL/MWh</td></tr>
    <tr><td>YEKDEM</td><td>${fmt(data.yekdemTlPerMwh)} TL/MWh</td></tr>
    <tr><td>Toplam Birim Maliyet</td><td>${fmt(birimMaliyet, 4)} TL/kWh</td></tr>
    <tr><td>Çarpan (Marj)</td><td>${fmt(data.multiplier)} (%${fmt(marjPuan, 1)} marj)</td></tr>
    <tr><td>Teklif Birim Fiyat</td><td>${fmt(teklifBirimFiyat, 4)} TL/kWh</td></tr>
  </table>
</div>

<div class="section">
  <div class="section-title" style="border-left-color: #f97316;">Komisyon Dağılımı</div>
  <table class="komisyon-table">
    <thead>
      <tr>
        <th>Kalem</th>
        <th class="text-center">Puan</th>
        <th class="text-right">Tutar (TL)</th>
        <th class="text-center">Pay (%)</th>
      </tr>
    </thead>
    <tbody>
      <tr class="toplam-row">
        <td>Toplam Marj Karı</td>
        <td class="text-center">${fmt(marjPuan, 1)}p</td>
        <td class="text-right">${fmtC(data.supplierProfitTl)}</td>
        <td class="text-center">100%</td>
      </tr>
      <tr class="bayi-row">
        <td>Bayi Komisyonu (${data.segmentName} — ${fmt(data.bayiPoints, 1)}p)</td>
        <td class="text-center">${fmt(data.bayiPoints, 1)}p</td>
        <td class="text-right">${fmtC(data.bayiCommissionTl)}</td>
        <td class="text-center">${fmt(marjPuan > 0 ? (data.bayiPoints / marjPuan) * 100 : 0, 0)}%</td>
      </tr>
      <tr class="gelka-row">
        <td>Gelka Net Kar</td>
        <td class="text-center">${fmt(data.gelkaPoints, 1)}p</td>
        <td class="text-right">${fmtC(data.gelkaNetProfitTl)}</td>
        <td class="text-center">${fmt(marjPuan > 0 ? (data.gelkaPoints / marjPuan) * 100 : 0, 0)}%</td>
      </tr>
    </tbody>
  </table>
</div>

<div class="section">
  <div class="section-title">Teklif Özeti</div>
  <table class="ozet-table">
    <tr><td>Mevcut Fatura (KDV Dahil)</td><td>${fmtC(data.currentTotalWithVatTl)}</td></tr>
    <tr><td>Teklif Tutarı (KDV Dahil)</td><td>${fmtC(data.offerTotalWithVatTl)}</td></tr>
    <tr><td>Müşteri Tasarrufu</td><td>${fmtC(tasarruf)} (%${fmt(data.savingsRatio * 100, 1)})</td></tr>
  </table>
</div>

<div class="hesap-detay">
  <strong>Hesaplama Detayı:</strong><br>
  Baz Enerji = (${fmt(data.ptfTlPerMwh)} + ${fmt(data.yekdemTlPerMwh)}) / 1000 × ${fmt(data.consumptionKwh, 0)} kWh = ${fmtC(data.offerEnergyBase)}<br>
  Toplam Marj = ${fmtC(data.offerEnergyBase)} × %${fmt(marjPuan, 1)} = ${fmtC(data.supplierProfitTl)}<br>
  Bayi Komisyon = ${fmtC(data.offerEnergyBase)} × %${fmt(data.bayiPoints, 1)} (${data.segmentName}) = ${fmtC(data.bayiCommissionTl)}<br>
  Gelka Net = ${fmtC(data.offerEnergyBase)} × %${fmt(data.gelkaPoints, 1)} = ${fmtC(data.gelkaNetProfitTl)}
</div>

<div class="footer">
  <span>Bu rapor Gelka Enerji dahili kullanımı içindir. Müşteriye iletilmez.</span>
  <span>© ${new Date().getFullYear()} Gelka Enerji</span>
</div>

</body>
</html>`;

  // Yeni pencerede aç ve yazdır
  const printWindow = window.open('', '_blank', 'width=800,height=1100');
  if (!printWindow) {
    alert('Popup engelleyici aktif. Lütfen izin verin.');
    return;
  }
  printWindow.document.write(html);
  printWindow.document.close();
  
  // Sayfa yüklendikten sonra yazdır
  printWindow.onload = () => {
    printWindow.print();
  };
}
