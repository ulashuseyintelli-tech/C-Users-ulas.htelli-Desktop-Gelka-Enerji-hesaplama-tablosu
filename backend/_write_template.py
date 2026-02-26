"""Write the offer template to disk - bypasses editor file locks."""
import os

template = r'''<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>Teklif {{ offer_id }}</title>
<style>
@page { size: A4; margin: 0; }
html, body { width: 210mm; height: 297mm; margin: 0; padding: 0; background: transparent; }
body { font-family: Arial, sans-serif; font-size: 8pt; line-height: 1.35; color: #333; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
* { margin: 0; padding: 0; box-sizing: border-box; }
.letterhead { position: fixed; top: 0; left: 0; width: 210mm; height: 297mm; z-index: 0; pointer-events: none; }
.letterhead img { width: 100%; height: 100%; object-fit: fill; display: block; }
.content { position: relative; z-index: 1; background: transparent; padding: 33mm 18mm 25mm 18mm; }
.header-meta { text-align: right; font-size: 8pt; color: #374151; line-height: 1.3; margin-bottom: 3px; }
.title { text-align: center; font-size: 15pt; font-weight: bold; text-transform: uppercase; letter-spacing: 1.5px; border-bottom: 2px solid #10B981; padding: 6px 0; margin-bottom: 8px; color: #1F2937; }
.intro { margin-bottom: 5px; line-height: 1.3; font-size: 8pt; }
.explanation { margin-bottom: 4px; line-height: 1.3; font-size: 7.5pt; }
.explanation b { font-weight: 600; }
.section-label { font-size: 8pt; font-weight: 600; margin-bottom: 1px; margin-top: 4px; color: #1F2937; }
table { width: 100%; border-collapse: collapse; margin-bottom: 5px; font-size: 7.5pt; }
th, td { padding: 3.5px 5px; border: 1px solid #ddd; }
th { background: #f5f5f5; text-align: left; font-weight: 600; }
.cmp th { background: #10B981; color: white; text-align: center; font-size: 8.5pt; }
.cmp td { text-align: right; }
.cmp td:first-child { text-align: left; }
.total { background: #e8f5e9 !important; font-weight: bold; }
.save { background: #10B981; color: white; padding: 6px; border-radius: 5px; text-align: center; margin: 5px 0; }
.save .label { font-size: 6.5pt; }
.save .amt { font-size: 12pt; font-weight: bold; }
.terms { background: #f9f9f9; border-left: 3px solid #10B981; padding: 5px 8px; margin: 5px 0; font-size: 7pt; }
.terms-title { font-size: 8pt; font-weight: bold; color: #1F2937; margin-bottom: 2px; }
.terms-grid { display: flex; gap: 12px; flex-wrap: wrap; }
.terms-item { display: flex; align-items: center; gap: 4px; }
.terms-check { width: 10px; height: 10px; border: 1.5px solid #10B981; border-radius: 2px; display: inline-flex; align-items: center; justify-content: center; font-size: 7pt; color: #10B981; font-weight: bold; }
.ek-bilgi { font-size: 6.5pt; color: #6B7280; margin-top: 5px; line-height: 1.3; }
.foot { margin-top: 5px; font-size: 6.5pt; color: #888; text-align: center; }
table, img { max-width: 100%; }
</style>
</head>
<body>
{% if letterhead_base64 %}
<div class="letterhead">
  <img src="data:image/png;base64,{{ letterhead_base64 }}" alt="" />
</div>
{% endif %}
<div class="content">
<div class="header-meta"><strong>Teklif No:</strong> {{ offer_id }}<br><strong>Tarih:</strong> {{ date }}</div>
<div class="title">ENERJ&#304; TASARRUF TEKL&#304;F&#304;</div>
<p class="intro"><strong>{{ greeting }}</strong><br>Mevcut elektrik t&#252;ketim verileriniz ve taraf&#305;m&#305;za iletilen fatura bilgileriniz esas al&#305;narak yap&#305;lan analiz sonucunda, {{ tariff_group }} abone grubunuz i&#231;in haz&#305;rlanan elektrik enerjisi tedarik teklifimizi bilgilerinize sunar&#305;z.</p>
{% if customer_name or contact_person %}
<table><tr><th style="width:15%">Firma Ad&#305;</th><td style="width:35%">{{ customer_name or '-' }}</td><th style="width:15%">Yetkili Ki&#351;i</th><td style="width:35%">{{ contact_person or '-' }}</td></tr></table>
{% endif %}
<p class="explanation">&#199;al&#305;&#351;ma, ayn&#305; t&#252;ketim miktar&#305; (<b>{{ consumption_kwh | number }} kWh</b>), ayn&#305; da&#287;&#305;t&#305;m bedelleri ve ayn&#305; vergi kalemleri esas al&#305;narak yap&#305;lm&#305;&#351;; fark yaln&#305;zca enerji tedarik bedelinden kaynaklanmaktad&#305;r.</p>
<p class="section-label">Enerji Bedelinin Hesaplama Yap&#305;s&#305;</p>
<p class="explanation">Enerji bedeli, EP&#304;A&#350; verileri esas al&#305;narak olu&#351;turulmaktad&#305;r. &#304;lgili fatura d&#246;nemi i&#231;in EP&#304;A&#350; saatlik PTF ile abonenin t&#252;ketim de&#287;erleri kullan&#305;larak <b>A&#287;&#305;rl&#305;kl&#305; PTF</b> hesaplan&#305;r. &#220;zerine YEKDEM birim bedeli eklenerek toplam enerji birim maliyeti olu&#351;turulur. Bu maliyet, anla&#351;ma fiyat katsay&#305;s&#305; (<b>{{ agreement_multiplier | number(2) }}</b>) ile &#231;arp&#305;larak nihai enerji bedeline ula&#351;&#305;l&#305;r.</p>
<p class="section-label">YEKDEM Uygulamas&#305;</p>
<p class="explanation">YEKDEM bedeli, EP&#304;A&#350; taraf&#305;ndan kesinle&#351;tirilmedi&#287;i durumlarda tahmini olarak faturaland&#305;r&#305;labilir. Ger&#231;ekle&#351;en de&#287;er a&#231;&#305;kland&#305;&#287;&#305;nda fark izleyen d&#246;nemlerde mahsup edilir.</p>
<p class="section-label">Di&#287;er Bedeller</p>
<p class="explanation">Da&#287;&#305;t&#305;m bedeli, BTV ve KDV gibi reg&#252;le edilen kalemlerde mevcut uygulama aynen korunmaktad&#305;r.</p>
<p style="font-size:8.5pt;font-weight:bold;margin:5px 0 3px 0;color:#1F2937;text-transform:uppercase;letter-spacing:0.5px;">Maliyet Kar&#351;&#305;la&#351;t&#305;rmas&#305;</p>
<table class="cmp">
<tr><th style="width:28%">Kalem</th><th style="width:24%">Mevcut Fatura</th><th style="width:24%">Teklifimiz</th><th style="width:24%">Tasarruf</th></tr>
<tr><td>Enerji Bedeli</td><td>{{ calc.current_energy_tl | currency }}</td><td>{{ calc.offer_energy_tl | currency }}</td><td>{{ (calc.current_energy_tl - calc.offer_energy_tl) | currency }}</td></tr>
<tr><td>Da&#287;&#305;t&#305;m Bedeli</td><td>{{ calc.current_distribution_tl | currency }}</td><td>{{ calc.offer_distribution_tl | currency }}</td><td>-</td></tr>
<tr><td>BTV</td><td>{{ calc.current_btv_tl | currency }}</td><td>{{ calc.offer_btv_tl | currency }}</td><td>-</td></tr>
<tr><td>KDV Matrah&#305;</td><td>{{ calc.current_vat_matrah_tl | currency }}</td><td>{{ calc.offer_vat_matrah_tl | currency }}</td><td>-</td></tr>
<tr><td>KDV (%{{ (calc.meta_vat_rate * 100) | int if calc.meta_vat_rate else 20 }})</td><td>{{ calc.current_vat_tl | currency }}</td><td>{{ calc.offer_vat_tl | currency }}</td><td>-</td></tr>
<tr class="total"><td>TOPLAM</td><td>{{ calc.current_total_with_vat_tl | currency }}</td><td>{{ calc.offer_total_with_vat_tl | currency }}</td><td>{{ calc.difference_incl_vat_tl | currency }}</td></tr>
</table>
<div class="save"><div class="label">Ayl&#305;k Tasarruf</div><div class="amt">{{ calc.difference_incl_vat_tl | currency }} ({{ calc.savings_ratio | percent }})</div></div>
<table>
<tr><th>A&#287;&#305;rl&#305;kl&#305; PTF</th><td>{{ weighted_ptf | number(2) }} TL/MWh</td><th>YEKDEM</th><td>{{ yekdem | number(2) }} TL/MWh</td></tr>
<tr><th>Anla&#351;ma &#199;arpan&#305;</th><td>{{ agreement_multiplier | number(2) }}</td><th>Teklif Birim Fiyat</th><td>{{ offer_unit_price | number(4) }} TL/kWh</td></tr>
<tr><th>Mevcut Birim Fiyat</th><td>{{ current_unit_price | number(4) }} TL/kWh</td><th>Birim Fiyat Fark&#305;</th><td>{{ (current_unit_price - offer_unit_price) | number(4) }} TL/kWh</td></tr>
</table>
<table>
<tr><th>Tedarik&#231;i</th><td>{{ vendor or 'Manuel Giri&#351;' }}</td><th>D&#246;nem</th><td>{{ invoice_period or '-' }}</td></tr>
<tr><th>T&#252;ketim</th><td>{{ consumption_kwh | number }} kWh</td><th>Tarife Grubu</th><td>{{ tariff_group }}</td></tr>
</table>
<p class="explanation" style="margin-top:6px;">Yap&#305;lan hesaplamalar sonucunda; mevcut durumda KDV hari&#231; toplam bedel <b>{{ calc.current_vat_matrah_tl | currency }}</b>, teklifimiz kapsam&#305;nda KDV hari&#231; toplam bedel <b>{{ calc.offer_vat_matrah_tl | currency }}</b> olmak &#252;zere, KDV hari&#231; <b>%{{ (calc.savings_ratio * 100) | abs | number(2) }}</b> oran&#305;nda tasarruf sa&#287;lanmaktad&#305;r.</p>
{% if contact_person %}<p class="explanation">&#304;lgili: {{ contact_person }}</p>{% endif %}
<p class="explanation">Bilgilerinize sunar&#305;z. Sayg&#305;lar&#305;m&#305;zla, <strong>Gelka Enerji</strong></p>
<div class="terms"><div class="terms-title">Ticari &#350;artlar</div><div class="terms-grid"><div class="terms-item"><span class="terms-check"></span> Fatura vadesi +10 g&#252;n</div><div class="terms-item"><span class="terms-check"></span> Teminat</div><div class="terms-item"><span class="terms-check"></span> G&#252;vence</div><div class="terms-item"><span class="terms-check"></span> &#214;n &#246;deme</div></div></div>
<p class="ek-bilgi"><strong>Ek Bilgiler:</strong> Bu teklif, mevcut fatura verileriniz esas al&#305;narak haz&#305;rlanm&#305;&#351;t&#305;r. Ger&#231;ek tasarruf tutarlar&#305;, t&#252;ketim miktar&#305; ve piyasa ko&#351;ullar&#305;na g&#246;re de&#287;i&#351;iklik g&#246;sterebilir. Teklif {{ offer_validity_days }} g&#252;n s&#252;reyle ge&#231;erlidir.</p>
<div class="foot">Teklif No: {{ offer_id }} | {{ date }} | Ge&#231;erlilik: {{ offer_validity_days }} G&#252;n | www.gelkaenerji.com.tr</div>
</div>
</body>
</html>'''

path = os.path.join(os.path.dirname(__file__), 'app', 'templates', 'offer_template.html')
tmp = path + '.tmp'
with open(tmp, 'w', encoding='utf-8') as f:
    f.write(template)
os.replace(tmp, path)
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()
print(f"OK: {len(c)} bytes, fixed={'position: fixed' in c}, upper={'text-transform: uppercase' in c}")
