import React, { useCallback, useEffect, useState } from 'react';
import { fetchOnayAdayi, fetchOnayGecmisi, onayGonder, onayHatasiniSiniflandir } from './fiyatOnayApi';
import type {
  KaynakKaniti, OnayAdayi, OnayGecmisi, OnayHatasi, OnayKalemi, OnaySegmenti, OnaySonucu,
} from './types';

// =============================================================================
// FiyatOnayPaneli — resmî aday → inceleme → yetkili onay
// =============================================================================
// OWNER-KARARI-01/02/03 (2026-09-13):
// - Kesin teklifte PTF: resmî veriden doğrulanmış, yetkili onaylı AYLIK ARİTMETİK PTF.
// - YEKDEM onayı segment başınadır (Serbest Tüketici | GTŞ-K1); segment varsayılanı YOK.
// - YEKDEM yayım takibi (~ayın 20'si) YALNIZ bilgilendirmedir; hiçbir teklifi otomatik
//   kesinleştirmez. Onay ve teklif kesinleştirme ayrı, açık kullanıcı işlemleridir.
// - Sayfa açılışında OTOMATİK istek yok. EPİAŞ kimlik bilgisi ve TGT tarayıcıya gelmez.
// - Değer TAM hassasiyetle gösterilir (yuvarlanmaz); versiyon KISALTILMAZ.
//
// Çağrıldığı yerler:
// - MarketPricesTab → Piyasa Fiyatları sekmesi
// =============================================================================

export const SEGMENT_ADLARI: Record<OnaySegmenti, string> = { st: 'Serbest Tüketici', gts: 'GTŞ-K1' };

/** Sunucunun doğruladığı yetki türü → ekran metni (kişi kimliği DEĞİL). */
export const YETKI_ADLARI: Record<string, string> = {
  paylasilan_yonetici_anahtari: 'paylaşılan yönetici anahtarı doğrulandı; kişi doğrulanmadı',
};

/** Onaylayan satırı: beyan edilen ad ile doğrulanan yetki ayrı ve açık yazılır. */
export function onaylayanMetni(beyan: string, yetki?: string): string {
  return `beyan: ${beyan} · yetki: ${yetki ? (YETKI_ADLARI[yetki] ?? yetki) : 'bilinmiyor'}`;
}

export const ONAYLANAMAMA_NEDENLERI: Record<string, string> = {
  saatlik_veri_yok: 'Saatlik PTF verisi dönmedi.',
  gecersiz_saat_damgasi: 'Geçersiz ya da saat dilimsiz saat damgası var.',
  donem_disi_saat: 'Dönem dışı saat var.',
  tekrarlanan_saat: 'Aynı saat birden fazla kez döndü.',
  eksik_saat: 'Dönemin bazı saatleri eksik.',
  gecersiz_fiyat: 'Sayısal olmayan saatlik fiyat var.',
  sayfalama_kirpilmasi: 'Yanıt sayfalanmış/kırpılmış görünüyor (toplam kalem sayısı uyuşmuyor).',
  price_avg_yok: 'Yayımlanan aylık ortalama (priceAvg) yok.',
  ortalama_yuvarlama_tutarsiz: 'Saatlik ortalama ile yayımlanan priceAvg 0,005 toleransı içinde uyuşmuyor.',
};

const HATA_RENGI: Record<string, string> = {
  aday_degisti: 'border-amber-300 bg-amber-50 text-amber-900',
  yeniden_onay: 'border-amber-300 bg-amber-50 text-amber-900',
  // Zaman aşımı: sonuç BELİRSİZ (başarısız DEĞİL) → uyarı rengi, kırmızı "hata" değil.
  belirsiz: 'border-amber-300 bg-amber-50 text-amber-900',
};

/** Değeri yuvarlamadan, sunucudan geldiği hassasiyetle yazar. */
export function tamDeger(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : String(v);
}

const KanitAyrintisi: React.FC<{ kanit: KaynakKaniti; sha: string; baslik?: string }> = ({ kanit, sha, baslik }) => {
  const d = kanit.saatlik_dogrulama;
  return (
    <details className="mt-2 rounded border border-gray-200 bg-gray-50 p-2 text-xs" data-testid="kaynak-kaniti">
      <summary className="cursor-pointer font-medium text-gray-700">{baslik ?? 'Kaynak kanıtı'}</summary>
      <dl className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1">
        <dt className="text-gray-500">Kaynak</dt><dd>{kanit.kaynak}</dd>
        <dt className="text-gray-500">Servis</dt><dd className="font-mono break-all">{kanit.servis}</dd>
        <dt className="text-gray-500">İstek aralığı</dt>
        <dd className="font-mono">{kanit.istek?.startDate} → {kanit.istek?.endDate}</dd>
        {kanit.alan && (<><dt className="text-gray-500">Alan</dt><dd className="font-mono">{kanit.alan}</dd></>)}
        {kanit.priceAvg && (<><dt className="text-gray-500">priceAvg (ham)</dt><dd className="font-mono">{kanit.priceAvg}</dd></>)}
        {kanit.ptfWeightedAvg && (
          <><dt className="text-gray-500">ptfWeightedAvg (bilgi)</dt><dd className="font-mono">{kanit.ptfWeightedAvg}</dd></>
        )}
        {kanit.version && (<><dt className="text-gray-500">Tam versiyon</dt><dd className="font-mono">{kanit.version}</dd></>)}
        {kanit.ayni_donem_versiyonlari && (
          <><dt className="text-gray-500">Dönemdeki versiyonlar</dt>
            <dd className="font-mono">{kanit.ayni_donem_versiyonlari.join(' · ')}</dd></>
        )}
        {kanit.segment_alani && (
          <><dt className="text-gray-500">Segment alanı</dt>
            <dd className="font-mono">{kanit.segment_alani} = {String(kanit[kanit.segment_alani] ?? '—')}</dd></>
        )}
        {d && (
          <>
            <dt className="text-gray-500">Saatlik kapsam</dt>
            <dd>{d.tekil_gecerli_saat}/{d.beklenen_saat} saat ({d.ilk_saat} → {d.son_saat})</dd>
            <dt className="text-gray-500">Eksik / tekrar / dönem dışı</dt>
            <dd>{d.eksik_saat} / {d.tekrarlanan_saat} / {d.donem_disi_saat}</dd>
            <dt className="text-gray-500">Saatlik ortalama</dt>
            <dd className="font-mono">{d.saatlik_ortalama ?? '—'} (fark {d.ortalama_farki ?? '—'}, tolerans {d.yuvarlama_toleransi})</dd>
          </>
        )}
        <dt className="text-gray-500">Kanıt SHA-256</dt><dd className="font-mono break-all">{sha}</dd>
      </dl>
      <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded bg-white p-2 font-mono text-[11px]">
        {JSON.stringify(kanit, null, 2)}
      </pre>
    </details>
  );
};

/** YEKDEM yayım takibi — YALNIZ bilgi; tarih hiçbir şeyi tetiklemez. */
export function yekdemTakipNotu(period: string): string {
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(period)) return '';
  return `${period} YEKDEM yayımı bilgi amaçlı olarak ayın 20'si civarında izlenir. `
    + 'Bu yalnız bilgilendirmedir: tarih hiçbir teklifi kesinleştirmez; onay ve kesinleştirme açık işlemdir.';
}

export interface FiyatOnayPaneliProps {
  /** Başlangıç dönemi önerisi (YYYY-MM); kullanıcı değiştirebilir. */
  varsayilanDonem: string | null;
}

export const FiyatOnayPaneli: React.FC<FiyatOnayPaneliProps> = ({ varsayilanDonem }) => {
  const [donem, setDonem] = useState(varsayilanDonem ?? '');
  const [kalem, setKalem] = useState<OnayKalemi>('PTF');
  const [segment, setSegment] = useState<OnaySegmenti | ''>('');
  const [aday, setAday] = useState<OnayAdayi | null>(null);
  const [onaylayan, setOnaylayan] = useState('');
  const [gerekce, setGerekce] = useState('');
  const [yukleniyor, setYukleniyor] = useState<'aday' | 'onay' | 'gecmis' | null>(null);
  const [hata, setHata] = useState<OnayHatasi | null>(null);
  const [sonuc, setSonuc] = useState<OnaySonucu | null>(null);
  const [gecmis, setGecmis] = useState<OnayGecmisi | null>(null);

  useEffect(() => {
    if (!donem && varsayilanDonem) setDonem(varsayilanDonem);
  }, [varsayilanDonem, donem]);

  // Seçim değişince eski aday yeni seçim başlığı altında GÖSTERİLMEZ.
  const secimiDegistir = useCallback((f: () => void) => {
    f();
    setAday(null);
    setSonuc(null);
    setHata(null);
  }, []);

  const donemGecerli = /^\d{4}-(0[1-9]|1[0-2])$/.test(donem);
  const segmentGerekli = kalem === 'YEKDEM';
  const adayGetirilebilir = donemGecerli && (!segmentGerekli || segment !== '') && yukleniyor === null;

  const adayGetir = useCallback(async () => {
    setYukleniyor('aday');
    setHata(null);
    setSonuc(null);
    try {
      setAday(await fetchOnayAdayi(donem, kalem, segment || null));
    } catch (e) {
      setAday(null);
      setHata(onayHatasiniSiniflandir(e));
    } finally {
      setYukleniyor(null);
    }
  }, [donem, kalem, segment]);

  const gecmisGetir = useCallback(async () => {
    setYukleniyor('gecmis');
    try {
      setGecmis(await fetchOnayGecmisi(donem));
    } catch (e) {
      setHata(onayHatasiniSiniflandir(e));
    } finally {
      setYukleniyor(null);
    }
  }, [donem]);

  const onayla = useCallback(async () => {
    if (!aday) return;
    setYukleniyor('onay');
    setHata(null);
    try {
      const s = await onayGonder({
        period: aday.period,
        kalem: aday.kalem,
        segment: aday.segment,
        aday_parmak_izi: aday.aday_parmak_izi,
        beklenen_revision: aday.beklenen_revision,
        beklenen_kayit_parmak_izi: aday.beklenen_kayit_parmak_izi ?? null,
        onaylayan_beyan: onaylayan.trim(),
        change_reason: gerekce.trim(),
      });
      setSonuc(s);
      setAday(null);
      try {
        setGecmis(await fetchOnayGecmisi(s.period));
      } catch {
        // Geçmiş okunamaması onay sonucunu değiştirmez.
      }
    } catch (e) {
      const h = onayHatasiniSiniflandir(e);
      setHata(h);
      if (h.tur === 'aday_degisti' && h.yeniAday) {
        setAday(h.yeniAday);  // yeni adayı göster; kullanıcı yeniden inceleyip onaylar
      } else if (h.tur === 'yeniden_onay') {
        setAday(null);
      } else if (h.tur === 'belirsiz') {
        // Sonuç belirsiz: kayıt yazılmış OLABİLİR. Otomatik yeniden gönderim YOK; adayı
        // ekranda tut ve onay geçmişini tazele ki kullanıcı revizyondan uzlaştırabilsin.
        try {
          setGecmis(await fetchOnayGecmisi(aday.period));
        } catch {
          // Geçmiş okunamaması belirsizliği değiştirmez; kullanıcı elle "Onay geçmişi" ile bakabilir.
        }
      }
    } finally {
      setYukleniyor(null);
    }
  }, [aday, onaylayan, gerekce]);

  const onayGonderilebilir = !!aday && aday.onaylanabilir && onaylayan.trim() !== ''
    && gerekce.trim() !== '' && yukleniyor === null;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4" data-testid="fiyat-onay-paneli">
      <h3 className="text-base font-semibold text-gray-900">Fiyat Onayı (yetkili)</h3>
      <p className="mt-1 text-xs text-gray-600">
        Resmî EPİAŞ adayı sunucuda çekilir; değer, yöntem, tam versiyon ve kaynak kanıtı incelendikten sonra
        yetkili onaya gönderilir. EPİAŞ kullanıcı bilgileri bu ekrana gelmez.
      </p>

      <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col">
          <span className="text-xs text-gray-600">Dönem</span>
          <input aria-label="Onay dönemi" value={donem} placeholder="YYYY-MM"
            onChange={(e) => secimiDegistir(() => setDonem(e.target.value.trim()))}
            className="rounded border border-gray-300 px-2 py-1 font-mono" />
        </label>
        <label className="flex flex-col">
          <span className="text-xs text-gray-600">Kalem</span>
          <select aria-label="Onay kalemi" value={kalem}
            onChange={(e) => secimiDegistir(() => setKalem(e.target.value as OnayKalemi))}
            className="rounded border border-gray-300 px-2 py-1">
            <option value="PTF">PTF (aylık aritmetik)</option>
            <option value="YEKDEM">YEKDEM</option>
          </select>
        </label>
        {segmentGerekli && (
          <label className="flex flex-col">
            <span className="text-xs text-gray-600">Segment (zorunlu)</span>
            <select aria-label="Onay segmenti" value={segment}
              onChange={(e) => secimiDegistir(() => setSegment(e.target.value as OnaySegmenti | ''))}
              className="rounded border border-gray-300 px-2 py-1">
              <option value="">Seçiniz</option>
              <option value="st">{SEGMENT_ADLARI.st}</option>
              <option value="gts">{SEGMENT_ADLARI.gts}</option>
            </select>
          </label>
        )}
        <button type="button" onClick={adayGetir} disabled={!adayGetirilebilir}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
          {yukleniyor === 'aday' ? 'Getiriliyor…' : 'Resmî adayı getir'}
        </button>
        <button type="button" onClick={gecmisGetir} disabled={!donemGecerli || yukleniyor !== null}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm text-gray-700 disabled:opacity-50">
          {yukleniyor === 'gecmis' ? 'Okunuyor…' : 'Onay geçmişi'}
        </button>
      </div>

      {segmentGerekli && donemGecerli && (
        <p className="mt-2 rounded bg-sky-50 px-2 py-1 text-xs text-sky-900" data-testid="yekdem-takip-notu">
          {yekdemTakipNotu(donem)}
        </p>
      )}

      {hata && (
        <div role="alert" data-testid="onay-hatasi"
          className={`mt-3 rounded border px-3 py-2 text-sm ${HATA_RENGI[hata.tur] ?? 'border-red-300 bg-red-50 text-red-800'}`}>
          {hata.mesaj}
        </div>
      )}

      {sonuc && (
        <div role="status" data-testid="onay-sonucu" className="mt-3 rounded border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-900">
          Onaylandı: {sonuc.kalem} {sonuc.period}{sonuc.segment ? ` · ${SEGMENT_ADLARI[sonuc.segment]}` : ''} ·
          revizyon {sonuc.revision} · değer {tamDeger(sonuc.value)}
          {sonuc.version ? ` · versiyon ${sonuc.version}` : ''} ·{' '}
          {onaylayanMetni(sonuc.onaylayan_beyan, sonuc.dogrulanan_yetki)}. Mevcut taslak teklifler kendiliğinden
          kesinleşmez; teklif ekranında açık işlemle kesinleştirilir.
        </div>
      )}

      {aday && (
        <div className="mt-3 rounded border border-gray-200 p-3 text-sm" data-testid="onay-adayi">
          <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
            <dt className="text-gray-500">Dönem</dt><dd className="font-mono">{aday.period}</dd>
            <dt className="text-gray-500">Kalem</dt><dd>{aday.kalem}</dd>
            <dt className="text-gray-500">Değer</dt>
            <dd className="font-mono" data-testid="aday-degeri">{tamDeger(aday.value)} {aday.birim ?? 'TL/MWh'}</dd>
            <dt className="text-gray-500">Yöntem</dt>
            <dd>{aday.kalem === 'PTF' ? (aday.yontem_etiketi ?? aday.basis) : 'EPİAŞ YEKDEM birim maliyeti'}</dd>
            {aday.kalem === 'YEKDEM' && (
              <>
                <dt className="text-gray-500">Tam versiyon</dt>
                <dd className="font-mono" data-testid="aday-versiyonu">{aday.version}</dd>
                <dt className="text-gray-500">Segment</dt>
                <dd>{aday.segment ? SEGMENT_ADLARI[aday.segment] : '—'}</dd>
              </>
            )}
            <dt className="text-gray-500">Mevcut geçerli onay</dt>
            <dd>{aday.gecerli_onay
              ? `revizyon ${aday.gecerli_onay.revision} · ${tamDeger(aday.gecerli_onay.value)}`
                + `${aday.gecerli_onay.version ? ` · ${aday.gecerli_onay.version}` : ''} · ${onaylayanMetni(aday.gecerli_onay.onaylayan_beyan, aday.gecerli_onay.dogrulanan_yetki)}`
              : 'yok'}</dd>
            {aday.mevcut_kayit && (
              <>
                <dt className="text-gray-500">Dönem kaydı</dt>
                <dd>{tamDeger(aday.mevcut_kayit.ptf_tl_per_mwh)} · {aday.mevcut_kayit.status} · {aday.mevcut_kayit.source}</dd>
              </>
            )}
          </dl>

          {!aday.onaylanabilir && (
            <div role="alert" data-testid="onaylanamaz" className="mt-2 rounded border border-red-300 bg-red-50 px-2 py-1 text-xs text-red-800">
              Bu aday onaylanamaz:
              <ul className="ml-4 list-disc">
                {aday.onaylanamama_nedenleri.map((n) => (<li key={n}>{ONAYLANAMAMA_NEDENLERI[n] ?? n}</li>))}
              </ul>
            </div>
          )}

          <KanitAyrintisi kanit={aday.kaynak_kanit} sha={aday.kaynak_kanit_sha256} />

          <p className="mt-3 text-xs text-gray-600" data-testid="beyan-notu">
            Ad beyan olarak kaydedilir. Sunucu yalnız yönetici anahtarının sunulduğunu doğrular; kişiyi doğrulamaz.
          </p>
          <div className="mt-1 flex flex-wrap items-end gap-3">
            <label className="flex flex-col">
              <span className="text-xs text-gray-600">Onaylayan adı (beyan)</span>
              <input aria-label="Onaylayan adı (beyan)" value={onaylayan} maxLength={100}
                onChange={(e) => setOnaylayan(e.target.value)} className="rounded border border-gray-300 px-2 py-1" />
            </label>
            <label className="flex flex-1 flex-col">
              <span className="text-xs text-gray-600">Gerekçe</span>
              <input aria-label="Onay gerekçesi" value={gerekce}
                onChange={(e) => setGerekce(e.target.value)} className="rounded border border-gray-300 px-2 py-1" />
            </label>
            <button type="button" onClick={onayla} disabled={!onayGonderilebilir}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50">
              {yukleniyor === 'onay' ? 'Gönderiliyor…' : 'Yetkili onaya gönder'}
            </button>
          </div>
        </div>
      )}

      {gecmis && (
        <div className="mt-3 text-sm" data-testid="onay-gecmisi">
          <h4 className="font-medium text-gray-800">Onay geçmişi — {gecmis.period}</h4>
          {!gecmis.altyapi ? (
            <p className="text-xs text-red-700">Onay tabloları bu veritabanında yok; onay kaydı tutulamıyor.</p>
          ) : gecmis.ptf.length + gecmis.yekdem.length === 0 ? (
            <p className="text-xs text-gray-600">Bu dönem için onay yok.</p>
          ) : (
            <ul className="mt-1 space-y-2">
              {[...gecmis.ptf, ...gecmis.yekdem].map((s) => (
                <li key={`${s.segment ?? 'ptf'}-${s.id}`} className="rounded border border-gray-200 p-2 text-xs">
                  <span className="font-medium">{s.segment ? `YEKDEM · ${SEGMENT_ADLARI[s.segment]}` : 'PTF'}</span>
                  {' '}· revizyon {s.revision} · {tamDeger(s.value)}
                  {s.version ? ` · ${s.version}` : ''} · {onaylayanMetni(s.onaylayan_beyan, s.dogrulanan_yetki)} · {s.approved_at}
                  {s.gecerli === true && <span className="ml-1 rounded bg-green-100 px-1 text-green-800">geçerli</span>}
                  {s.gecerli === false && <span className="ml-1 rounded bg-gray-100 px-1 text-gray-600">geçersiz/eski</span>}
                  {s.guncel === true && <span className="ml-1 rounded bg-green-100 px-1 text-green-800">güncel</span>}
                  <KanitAyrintisi kanit={s.kaynak_kanit} sha={s.kaynak_kanit_sha256} baslik="Kaynak kanıtını incele" />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
};
