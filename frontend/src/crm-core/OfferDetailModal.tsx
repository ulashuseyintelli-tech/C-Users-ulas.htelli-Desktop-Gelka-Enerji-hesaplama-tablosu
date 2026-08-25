// S5-R01 — Kayıtlı teklif detayı (dar kapsamlı).
//
// Tüm değerler `GET /offers/{id}` ile dönen KAYITLI SNAPSHOT'tan gelir; bu
// bileşen hiçbir hesap YAPMAZ ve hiçbir hesap değeri sunucuya GÖNDERMEZ.
// Listedeki ticari değerlerle birebir aynı snapshot okunur.
//
// PDF: yalnız offer-bound zincir kullanılır
//   POST /offers/{id}/generate-pdf  →  GET /offers/{id}/download
// `/generate-pdf-simple` bu akışta KULLANILMAZ (teklif kimliği taşımaz,
// istemciden gelen hesap değerlerine güvenir, `pdf_ref` yazmaz).
//
// `pdf_ref` fiziksel yolu EKRANDA GÖSTERİLMEZ ve LOGLANMAZ; yalnız
// "PDF var mı" sorusunun cevabı (Boolean) için kullanılır.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Loader2, AlertCircle, X, FileText, Download } from 'lucide-react';
import { getOffer, generateOfferPdf, downloadOfferPdf, OfferDetail } from '../api';

interface Props {
  offerId: number;
  onClose: () => void;
}

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('tr-TR', {
    style: 'currency',
    currency: 'TRY',
    maximumFractionDigits: 2,
  }).format(value);
}

function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('tr-TR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

// Backend `savings_ratio`yu KESİR olarak saklar (calculator.py: 0..1,
// 4 haneye yuvarlı). Yüzdeye çevirmek görüntüleme işidir.
function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `%${(value * 100).toFixed(1)}`;
}

function Satir({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-gray-100 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className="font-medium text-gray-900 text-right">{value}</span>
    </div>
  );
}

export function OfferDetailModal({ offerId, onClose }: Props) {
  const [offer, setOffer] = useState<OfferDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Tek bir PDF aksiyonu aynı anda çalışır; buton bu bayrakla disabled olur.
  // NOT: bu yalnız UI rahatlığıdır — eşzamanlılık garantisi SUNUCUDADIR
  // (offer PDF üretimi cross-process kilitle korunur).
  const [pdfBusy, setPdfBusy] = useState(false);
  // `pdfBusy` state'i asenkron uygulanır: AYNI tick içinde art arda gelen
  // tıklamalar butonu hâlâ enabled görür. Ref SENKRON güncellenir ve bu
  // pencereyi de kapatır (UAT'te üç programatik tıklama üç istek üretmişti;
  // sunucu yine tek fiziksel PDF üretti, fakat gereksiz istek de olmamalı).
  const pdfCalisiyorRef = useRef(false);
  const [pdfNotice, setPdfNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setOffer(await getOffer(offerId));
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Teklif yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, [offerId]);

  useEffect(() => {
    load();
  }, [load]);

  const hataMesaji = (err: any, varsayilan: string): string => {
    const detail = err?.response?.data?.detail;
    if (detail?.error === 'generation_in_progress') {
      return 'Bu teklif için PDF üretimi şu anda sürüyor. Lütfen birkaç saniye sonra tekrar deneyin.';
    }
    if (detail?.error === 'pdf_artifact_missing') {
      return 'Teklife bağlı PDF kaydı var fakat dosya bulunamadı. Bu bir tutarsızlıktır; lütfen yöneticinize bildirin.';
    }
    return detail?.message || err?.message || varsayilan;
  };

  const handleGenerate = async () => {
    if (pdfCalisiyorRef.current) return;  // senkron tekrar-tiklama engeli
    pdfCalisiyorRef.current = true;
    setPdfBusy(true);
    setError(null);
    setPdfNotice(null);
    try {
      const sonuc = await generateOfferPdf(offerId);
      setPdfNotice(sonuc.regenerated ? 'PDF oluşturuldu.' : 'PDF zaten mevcuttu.');
      await load(); // pdf_ref durumu tazelenir
    } catch (err: any) {
      setError(hataMesaji(err, 'PDF oluşturulamadı.'));
    } finally {
      pdfCalisiyorRef.current = false;
      setPdfBusy(false);
    }
  };

  const handleDownload = async () => {
    if (pdfCalisiyorRef.current) return;  // senkron tekrar-tiklama engeli
    pdfCalisiyorRef.current = true;
    setPdfBusy(true);
    setError(null);
    setPdfNotice(null);
    try {
      await downloadOfferPdf(offerId);
    } catch (err: any) {
      setError(hataMesaji(err, 'PDF indirilemedi.'));
    } finally {
      pdfCalisiyorRef.current = false;
      setPdfBusy(false);
    }
  };

  // Yalnız VARLIK bilgisi kullanılır; `pdf_ref` değerinin kendisi hiçbir
  // yerde render edilmez veya URL'ye birleştirilmez.
  const pdfMevcut = Boolean(offer?.pdf_ref);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h2 className="text-lg font-semibold text-gray-900">
            Teklif Detayı <span className="text-gray-400 font-normal">#{offerId}</span>
          </h2>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label="Kapat">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
              <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {pdfNotice && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
              {pdfNotice}
            </div>
          )}

          {loading ? (
            <div className="p-8 text-center text-gray-500">
              <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
              Yükleniyor...
            </div>
          ) : !offer ? (
            <div className="p-8 text-center text-gray-500">Teklif bulunamadı.</div>
          ) : (
            <>
              <section>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Müşteri ve Fatura</h3>
                <div className="text-sm">
                  <Satir label="Firma" value={offer.customer?.company || offer.customer?.name || 'Müşterisiz'} />
                  <Satir label="Yetkili" value={offer.customer?.name || '—'} />
                  <Satir label="Tedarikçi" value={offer.vendor || '—'} />
                  <Satir label="Fatura Dönemi" value={offer.invoice_period || '—'} />
                  <Satir label="Tüketim" value={`${formatNumber(offer.consumption_kwh)} kWh`} />
                </div>
              </section>

              <section>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Ticari Değerler</h3>
                <div className="text-sm">
                  <Satir label="Mevcut Tutar" value={formatCurrency(offer.current_total)} />
                  <Satir label="Teklif Tutarı" value={formatCurrency(offer.offer_total)} />
                  <Satir
                    label="Tasarruf"
                    value={<span className="text-green-700">{formatCurrency(offer.savings_amount)}</span>}
                  />
                  <Satir
                    label="Tasarruf Oranı"
                    value={<span className="text-green-700">{formatRatio(offer.savings_ratio)}</span>}
                  />
                  <Satir label="Anlaşma Katsayısı" value={offer.agreement_multiplier?.toFixed(2) ?? '—'} />
                </div>
              </section>

              <section>
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Fiyat Parametreleri</h3>
                <div className="text-sm">
                  <Satir label="Ağırlıklı PTF" value={`${formatNumber(offer.weighted_ptf, 2)} TL/MWh`} />
                  <Satir label="YEKDEM" value={`${formatNumber(offer.yekdem, 2)} TL/MWh`} />
                  <Satir label="Mevcut Birim Fiyat" value={`${formatNumber(offer.current_unit_price, 4)} TL/kWh`} />
                  <Satir
                    label="Dağıtım Birim Fiyatı"
                    value={
                      offer.distribution_unit_price === null
                        ? '—'
                        : `${formatNumber(offer.distribution_unit_price, 4)} TL/kWh`
                    }
                  />
                </div>
              </section>

              <section className="pt-1">
                <h3 className="text-sm font-semibold text-gray-700 mb-2">Teklif Belgesi</h3>
                <div className="flex items-center gap-2">
                  {pdfMevcut ? (
                    <button
                      type="button"
                      onClick={handleDownload}
                      disabled={pdfBusy}
                      className="btn-primary flex items-center gap-2 disabled:opacity-50"
                    >
                      {pdfBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                      PDF İndir
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={handleGenerate}
                      disabled={pdfBusy}
                      className="btn-primary flex items-center gap-2 disabled:opacity-50"
                    >
                      {pdfBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" />}
                      PDF Oluştur
                    </button>
                  )}
                  <span className="text-xs text-gray-500">
                    {pdfMevcut
                      ? 'Belge kayıtlı teklif verisinden üretilmiştir.'
                      : 'Belge henüz oluşturulmadı.'}
                  </span>
                </div>
              </section>
            </>
          )}
        </div>

        <div className="border-t border-gray-200 px-5 py-3 flex justify-end">
          <button type="button" onClick={onClose} className="btn-secondary">
            Kapat
          </button>
        </div>
      </div>
    </div>
  );
}
