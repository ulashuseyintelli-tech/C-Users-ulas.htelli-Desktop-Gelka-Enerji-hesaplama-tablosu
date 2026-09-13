import React, { useState } from 'react';
import { downloadDraftOfferPdf, finalizeOfferPrice, FiyatKesinlestirmeHatasi } from '../api';
import { YEKDEM_SEGMENT_LABELS, type YekdemSegment } from './priceReadiness';

// =============================================================================
// TaslakTeklifPaneli — kaydedilmiş TASLAK teklif: taslak PDF + açık kesinleştirme
// =============================================================================
// OWNER-KARARI-03 (2026-09-13): doğrulanmamış fiyatla teklif yalnız AÇIKÇA işaretli
// taslak olarak kaydedilir; taslak PDF "TASLAK" damgalıdır ve saklanmaz. Taslak,
// onay gelince KENDİLİĞİNDEN kesinleşmez; kullanıcı bu paneldeki açık işlemle
// kesinleştirir ve sunucu bugünkü onaylı revizyonlarla YENİDEN doğrular.
//
// Çağrıldığı yerler:
// - App.tsx → taslak teklif kaydedildikten sonra (Detaylı Karşılaştırma altı)
// =============================================================================

export const ENGEL_NEDENLERI: Record<string, string> = {
  ptf_identity_missing: 'PTF için yetkili onaylı aylık aritmetik değer yok.',
  ptf_provisional: 'PTF dönem kaydı kesinleşmemiş.',
  ptf_unverified: 'PTF sunucu kaydıyla doğrulanmadı.',
  ptf_missing: 'PTF değeri eksik.',
  yekdem_missing: 'YEKDEM değeri eksik.',
  yekdem_segment_missing: 'YEKDEM segmenti seçilmedi.',
  yekdem_segment_invalid: 'YEKDEM segmenti geçersiz.',
  yekdem_approval_missing: 'Seçilen segmentte onaylı YEKDEM yok.',
  yekdem_unverified: 'YEKDEM seçilen segmentin onaylı değeriyle eşleşmiyor.',
  yekdem_mode_required: 'YEKDEM uygulaması (dahil/hariç) seçilmedi.',
  segment_celiskisi: 'Seçilen segment taslaktaki segmentle çelişiyor.',
  zaten_kesin: 'Teklif fiyatı zaten kesin.',
  taslak_degil: 'Bu kayıt sunucuda üretilmiş bir fiyat taslağı değil.',
};

export function engelMetni(kod: string): string {
  return ENGEL_NEDENLERI[kod] ?? kod;
}

export interface TaslakTeklifPaneliProps {
  offerId: number;
  blockingReasons: string[];
  /** Ekranda seçili segment (taslakta seçilmediyse kesinleştirmede gönderilir). */
  yekdemSegment: YekdemSegment | null;
  /** Kesinleşince çağrılır (App kesin teklif akışına geçer). */
  onKesinlesti: (offerId: number) => void;
}

export const TaslakTeklifPaneli: React.FC<TaslakTeklifPaneliProps> = ({
  offerId, blockingReasons, yekdemSegment, onKesinlesti,
}) => {
  const [kesinlestiren, setKesinlestiren] = useState('');
  const [islem, setIslem] = useState<'pdf' | 'kesinlestir' | null>(null);
  const [hata, setHata] = useState<{ mesaj: string; nedenler: string[] } | null>(null);
  const [bilgi, setBilgi] = useState<string | null>(null);

  const taslakPdf = async () => {
    setIslem('pdf');
    setHata(null);
    try {
      await downloadDraftOfferPdf(offerId);
      setBilgi('TASLAK damgalı PDF indirildi (sunucuda saklanmadı).');
    } catch (e: any) {
      setHata({ mesaj: e?.message || 'Taslak PDF üretilemedi.', nedenler: [] });
    } finally {
      setIslem(null);
    }
  };

  const kesinlestir = async () => {
    setIslem('kesinlestir');
    setHata(null);
    setBilgi(null);
    try {
      await finalizeOfferPrice(offerId, { yekdemSegment, kesinlestiren });
      onKesinlesti(offerId);
    } catch (e: any) {
      if (e instanceof FiyatKesinlestirmeHatasi) {
        setHata({ mesaj: engelMetni(e.kod) === e.kod ? e.message : engelMetni(e.kod), nedenler: e.blockingReasons });
      } else {
        setHata({ mesaj: e?.message || 'Kesinleştirme isteği tamamlanamadı; teklif değişmedi.', nedenler: [] });
      }
    } finally {
      setIslem(null);
    }
  };

  return (
    <div className="mb-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900" data-testid="taslak-teklif-paneli">
      <p className="font-semibold">TASLAK teklif #{offerId} — fiyat doğrulanmadı</p>
      {blockingReasons.length > 0 && (
        <ul className="ml-4 list-disc" data-testid="taslak-nedenleri">
          {blockingReasons.map((n) => (<li key={n}>{engelMetni(n)}</li>))}
        </ul>
      )}
      <p className="mt-1">
        Onay gelince teklif kendiliğinden kesinleşmez. Onaylı fiyat hazır olduğunda aşağıdaki işlemle
        sunucu yeniden doğrular; kayıtlı değerler yeniden hesaplanmaz, geçmezse teklif değişmez.
        {yekdemSegment ? ` Segment: ${YEKDEM_SEGMENT_LABELS[yekdemSegment]}.` : ''}
      </p>
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <button type="button" onClick={taslakPdf} disabled={islem !== null}
          className="rounded border border-amber-400 bg-white px-2 py-1 disabled:opacity-50">
          {islem === 'pdf' ? 'Hazırlanıyor…' : "Taslak PDF'i indir"}
        </button>
        <label className="flex flex-col">
          <span>Kesinleştiren</span>
          <input aria-label="Kesinleştiren" value={kesinlestiren} maxLength={100}
            onChange={(e) => setKesinlestiren(e.target.value)} className="rounded border border-amber-300 px-2 py-1" />
        </label>
        <button type="button" onClick={kesinlestir} disabled={islem !== null}
          className="rounded bg-amber-600 px-2 py-1 font-medium text-white disabled:opacity-50">
          {islem === 'kesinlestir' ? 'Doğrulanıyor…' : 'Fiyatı yeniden doğrula ve kesinleştir'}
        </button>
      </div>
      {bilgi && <p role="status" className="mt-1 text-amber-800">{bilgi}</p>}
      {hata && (
        <div role="alert" className="mt-1 rounded border border-red-300 bg-red-50 px-2 py-1 text-red-800" data-testid="kesinlestirme-hatasi">
          {hata.mesaj}
          {hata.nedenler.length > 0 && (
            <ul className="ml-4 list-disc">{hata.nedenler.map((n) => (<li key={n}>{engelMetni(n)}</li>))}</ul>
          )}
        </div>
      )}
    </div>
  );
};
