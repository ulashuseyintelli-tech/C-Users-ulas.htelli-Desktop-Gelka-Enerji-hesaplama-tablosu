import React, { useCallback, useMemo, useRef, useState } from 'react';
import { formatPrice } from '../utils';
import { fetchEpiasComparison, hatayiSiniflandir } from './epiasCompareApi';
import { adayBul, YAPISAL_KIMLIK } from './identityCandidates';
import {
  ALAN_DURUM_ETIKETLERI, SEGMENT_ETIKETLERI, TIP_ETIKETLERI, YONTEM_ETIKETLERI, nedenMetni,
} from './labels';
import type { KarsilastirmaHatasi, KarsilastirmaSatiri, KarsilastirmaYaniti } from './types';

// =============================================================================
// EpiasCompareSection — SALT OKUNUR karşılaştırma bölümü
// =============================================================================
// Piyasa Fiyatları ekranına eklenen küçük bölüm. Fiyat YAZMAZ, onaylamaz,
// kesinleştirmez; hiçbir yazma düğmesi içermez.
//
// - Sayfa açılışında OTOMATİK sorgu başlatmaz; kullanıcı düğmeye basar.
// - Yetkilendirme mevcut adminApi (X-Admin-Key) düzenidir.
// - Parola/TGT tarayıcıya taşınmaz; kimlik yalnız sunucuda kullanılır.
// - Fiyat farkı YALNIZ backend'in kimliği doğruladığı satırlarda gösterilir.
// - Kimlik adayları ÖNERİdir; eski kaydı doğrulanmış göstermez.
//
// Çağrıldığı yerler:
// - MarketPricesTab → Piyasa Fiyatları sekmesi
// =============================================================================

export interface EpiasCompareSectionProps {
  /** Listede görünen en küçük dönem (YYYY-MM). */
  fromPeriod: string | null;
  /** Listede görünen en büyük dönem (YYYY-MM). */
  toPeriod: string | null;
}

type Aralik = { from: string; to: string };

type Durum =
  | { ad: 'bosta' }
  | { ad: 'yukleniyor'; aralik: Aralik }
  | { ad: 'hata'; hata: KarsilastirmaHatasi; aralik: Aralik }
  | { ad: 'hazir'; yanit: KarsilastirmaYaniti; aralik: Aralik };

/**
 * Gösterilecek durum. Sonuç, SORGULANAN dönem aralığına bağlıdır: aralık
 * değiştiyse eski yanıt yeni dönem başlığı altında GÖSTERİLMEZ, durum boşa döner.
 */
function gorunenDurum(durum: Durum, from: string | null, to: string | null): Durum {
  if (durum.ad === 'bosta') return durum;
  if (durum.aralik.from !== from || durum.aralik.to !== to) return { ad: 'bosta' };
  return durum;
}

/** Uygulanabilir kimlik alanlarından bilinmeyen var mı? */
function kimlikEksik(satir: KarsilastirmaSatiri): boolean {
  const alanlar = satir.kalem === 'PTF' ? (['yontem'] as const) : (['versiyon', 'segment'] as const);
  return alanlar.some((a) => satir.alanlar[a] === 'bilinmiyor');
}

function deger(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : formatPrice(v);
}

const KimlikHucresi: React.FC<{ satir: KarsilastirmaSatiri }> = ({ satir }) => {
  const { gelka, epias, alanlar, kalem } = satir;
  const satirlar: Array<{ ad: string; gelka: string; epias: string; durum: string }> = [];
  if (kalem === 'PTF') {
    satirlar.push({
      ad: 'Yöntem',
      gelka: gelka.yontem ? (YONTEM_ETIKETLERI[gelka.yontem] ?? gelka.yontem) : 'bilinmiyor',
      epias: epias?.yontem ? (YONTEM_ETIKETLERI[epias.yontem] ?? epias.yontem) : '—',
      durum: ALAN_DURUM_ETIKETLERI[alanlar.yontem],
    });
  } else {
    satirlar.push({
      ad: 'Versiyon',
      gelka: gelka.versiyon ?? 'bilinmiyor',
      epias: epias?.versiyon ?? '—',
      durum: ALAN_DURUM_ETIKETLERI[alanlar.versiyon],
    });
    satirlar.push({
      ad: 'Segment',
      gelka: gelka.segment ? (SEGMENT_ETIKETLERI[gelka.segment] ?? gelka.segment) : 'bilinmiyor',
      epias: epias?.segment ? (SEGMENT_ETIKETLERI[epias.segment] ?? epias.segment) : '—',
      durum: ALAN_DURUM_ETIKETLERI[alanlar.segment],
    });
  }
  return (
    <div className="space-y-1 text-xs">
      {satirlar.map((s) => (
        <div key={s.ad}>
          <span className="font-medium text-gray-700">{s.ad}:</span>{' '}
          <span className="text-gray-900">{s.gelka}</span>
          <span className="text-gray-400"> / EPİAŞ: </span>
          <span className="text-gray-900">{s.epias}</span>{' '}
          <span className="text-gray-500">({s.durum})</span>
        </div>
      ))}
      <div className="text-gray-500">
        Birim: {gelka.birim ?? '—'} ({ALAN_DURUM_ETIKETLERI[alanlar.birim]})
      </div>
    </div>
  );
};

const OneriKutusu: React.FC<{ satir: KarsilastirmaSatiri }> = ({ satir }) => {
  // Aday YALNIZ türetildiği dönem/değer/kaynak koşulları hâlâ eşleşiyorsa gelir.
  const aday = adayBul(satir.donem, satir.kalem, {
    deger: satir.gelka.deger,
    kaynak_jetonu: satir.gelka.kaynak_jetonu,
  });
  if (!aday) {
    return (
      <p className="mt-2 text-xs text-gray-500" data-testid={`kars-oneri-yok-${satir.donem}-${satir.kalem}`}>
        Bu kayıt için kimlik önerisi gösterilmiyor: kayıt, önerinin türetildiği
        dönem/değer/kaynak koşullarıyla eşleşmiyor. Eski öneri otomatik taşınmaz.
      </p>
    );
  }
  const onerilen = satir.kalem === 'PTF'
    ? (aday.onerilenYontem ? (YONTEM_ETIKETLERI[aday.onerilenYontem] ?? aday.onerilenYontem) : '—')
    : `${aday.onerilenVersiyon ?? '—'} / ${
        aday.onerilenSegment ? (SEGMENT_ETIKETLERI[aday.onerilenSegment] ?? aday.onerilenSegment) : '—'
      }`;
  const gucEtiketi = {
    desteklenen_hipotez: 'desteklenen hipotez',
    zayif: 'zayıf — yalnız sayısal eşitlik',
    kanit_yok: 'kanıtsız — iş bağlamı',
  }[aday.guc];
  return (
    <details className="mt-2 rounded border border-dashed border-indigo-300 bg-indigo-50 p-2 text-xs">
      <summary className="cursor-pointer text-indigo-900">
        <span className="font-semibold">ÖNERİ — hipotez, kanıt değil:</span> {onerilen}{' '}
        <span className="text-indigo-700">[{gucEtiketi}]</span>
      </summary>
      <div className="mt-2 space-y-2 text-indigo-900">
        <p className="italic">
          Bu bir eşleştirme hipotezidir. Kayda yazılmamıştır, fiyat farkı hesabında
          kullanılmaz ve geçmiş girişin kaynağını kanıtlamaz. Kayıt doğrulanmış SAYILMAZ.
          Owner onayı gerekir.
        </p>
        <div>
          <div className="font-semibold">Dayanak</div>
          <ul className="list-disc pl-4">
            {aday.dayanak.map((d) => <li key={d}>{d}</li>)}
          </ul>
        </div>
        <div>
          <div className="font-semibold">Belirsizlik</div>
          <ul className="list-disc pl-4">
            {aday.belirsizlik.map((b) => <li key={b}>{b}</li>)}
          </ul>
        </div>
        <div className="text-indigo-700">
          Yapısal kimlik: {YAPISAL_KIMLIK.donem} {YAPISAL_KIMLIK.birim}
        </div>
      </div>
    </details>
  );
};

const SatirKarti: React.FC<{ satir: KarsilastirmaSatiri }> = ({ satir }) => {
  const tip = TIP_ETIKETLERI[satir.tip] ?? TIP_ETIKETLERI.KARSILASTIRILAMAZ;
  const farkGoster = tip.karsilastirilabilir && satir.fark !== null;
  return (
    <tr className="border-b border-gray-100 align-top" data-testid={`kars-satir-${satir.donem}-${satir.kalem}`}>
      <td className="px-3 py-2 text-sm font-medium text-gray-900">{satir.donem}</td>
      <td className="px-3 py-2 text-sm text-gray-700">{satir.kalem}</td>
      <td className="px-3 py-2 text-right text-sm text-gray-900">{deger(satir.gelka.deger)}</td>
      <td className="px-3 py-2 text-right text-sm text-gray-900">{deger(satir.epias?.deger)}</td>
      <td className="px-3 py-2 text-right text-sm">
        {farkGoster ? (
          <span className={satir.fark === 0 ? 'text-gray-600' : 'font-medium text-amber-700'}>
            {formatPrice(satir.fark as number)}
          </span>
        ) : (
          <span className="text-gray-400" title="Kimlik doğrulanmadığı için fark hesaplanmaz">—</span>
        )}
      </td>
      <td className="px-3 py-2">
        <KimlikHucresi satir={satir} />
      </td>
      <td className="px-3 py-2">
        <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${tip.sinif}`}>
          {tip.etiket}
        </span>
        {satir.nedenler.length > 0 && (
          <ul className="mt-1 list-disc pl-4 text-xs text-gray-600">
            {satir.nedenler.map((n) => <li key={n}>{nedenMetni(n)}</li>)}
          </ul>
        )}
        {kimlikEksik(satir) && <OneriKutusu satir={satir} />}
      </td>
    </tr>
  );
};

const HataKutusu: React.FC<{ hata: KarsilastirmaHatasi }> = ({ hata }) => {
  const kapaliMi = hata.tur === 'ozellik_kapali';
  return (
    <div
      role="alert"
      data-testid="kars-hata"
      className={`rounded-md border p-3 text-sm ${
        kapaliMi ? 'border-gray-300 bg-gray-50 text-gray-700' : 'border-red-300 bg-red-50 text-red-800'
      }`}
    >
      <div className="font-medium">
        {kapaliMi ? 'Karşılaştırma kapalı' : 'Karşılaştırma yapılamadı'}
      </div>
      <div className="mt-1">{hata.mesaj}</div>
      <div className="mt-1 text-xs text-gray-600">Mevcut fiyat kayıtları değişmedi.</div>
    </div>
  );
};

export const EpiasCompareSection: React.FC<EpiasCompareSectionProps> = ({ fromPeriod, toPeriod }) => {
  const [hamDurum, setDurum] = useState<Durum>({ ad: 'bosta' });
  const durum = gorunenDurum(hamDurum, fromPeriod, toPeriod);
  const iptalRef = useRef<AbortController | null>(null);

  const aralikVar = Boolean(fromPeriod && toPeriod);

  const karsilastir = useCallback(async () => {
    if (!fromPeriod || !toPeriod) return;
    iptalRef.current?.abort();
    const kontrol = new AbortController();
    iptalRef.current = kontrol;
    const aralik: Aralik = { from: fromPeriod, to: toPeriod };
    setDurum({ ad: 'yukleniyor', aralik });
    try {
      const yanit = await fetchEpiasComparison(fromPeriod, toPeriod, kontrol.signal);
      setDurum({ ad: 'hazir', yanit, aralik });
    } catch (e) {
      setDurum({ ad: 'hata', hata: hatayiSiniflandir(e), aralik });
    }
  }, [fromPeriod, toPeriod]);

  const temizle = useCallback(() => {
    iptalRef.current?.abort();
    setDurum({ ad: 'bosta' });
  }, []);

  const ozetMetni = useMemo(() => {
    if (durum.ad !== 'hazir') return null;
    const girdiler = Object.entries(durum.yanit.ozet);
    if (girdiler.length === 0) return null;
    return girdiler
      .map(([tip, adet]) => `${TIP_ETIKETLERI[tip as keyof typeof TIP_ETIKETLERI]?.etiket ?? tip}: ${adet}`)
      .join(' · ');
  }, [durum]);

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4" data-testid="epias-compare">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900">
            EPİAŞ Karşılaştırması{' '}
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs font-normal text-gray-600">
              salt okunur
            </span>
          </h3>
          <p className="mt-1 text-xs text-gray-600">
            Kayıtlar EPİAŞ ile karşılaştırılır. Hiçbir fiyat yazılmaz, onaylanmaz veya
            kesinleştirilmez. Sorgu sayfa açılışında otomatik başlamaz.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-600" data-testid="kars-aralik">
            {aralikVar ? `${fromPeriod} … ${toPeriod}` : 'Dönem aralığı yok'}
          </span>
          <button
            type="button"
            onClick={karsilastir}
            disabled={!aralikVar || durum.ad === 'yukleniyor'}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {durum.ad === 'yukleniyor' ? 'Karşılaştırılıyor…' : 'Karşılaştır'}
          </button>
          {durum.ad !== 'bosta' && (
            <button
              type="button"
              onClick={temizle}
              className="rounded-md px-2 py-1.5 text-sm text-gray-500 hover:text-gray-700"
            >
              Temizle
            </button>
          )}
        </div>
      </div>

      <div className="mt-3">
        {durum.ad === 'bosta' && (
          <p className="text-sm text-gray-500" data-testid="kars-bosta">
            {aralikVar
              ? 'Karşılaştırmak için düğmeye basın.'
              : 'Listede dönem bulunmadığı için karşılaştırma yapılamaz. Filtreleri temizleyin veya kayıt ekleyin.'}
          </p>
        )}

        {durum.ad === 'yukleniyor' && (
          <p className="text-sm text-gray-500" data-testid="kars-yukleniyor">
            EPİAŞ sorgulanıyor…
          </p>
        )}

        {durum.ad === 'hata' && <HataKutusu hata={durum.hata} />}

        {durum.ad === 'hazir' && (
          <div className="space-y-3" data-testid="kars-sonuc">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
              <span>Değerlendirme tarihi: {durum.yanit.evaluated_at}</span>
              <span>Tolerans: {durum.yanit.tolerans_tl_mwh} TL/MWh</span>
              {ozetMetni && <span>Özet: {ozetMetni}</span>}
            </div>

            {durum.yanit.uyarilar.length > 0 && (
              <ul
                className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900"
                data-testid="kars-uyarilar"
              >
                {durum.yanit.uyarilar.map((u, i) => (
                  <li key={`${u.kod}-${u.donem ?? i}`}>
                    <span className="font-medium">{nedenMetni(u.kod)}</span>
                    {u.donem ? ` (${u.donem})` : ''} — {u.mesaj}
                  </li>
                ))}
              </ul>
            )}

            {durum.yanit.satirlar.length === 0 ? (
              <p className="text-sm text-gray-500" data-testid="kars-bos-sonuc">
                Karşılaştırılacak satır bulunamadı.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full border-collapse">
                  <thead>
                    <tr className="border-b border-gray-200 bg-gray-50 text-left text-xs uppercase text-gray-500">
                      <th className="px-3 py-2">Dönem</th>
                      <th className="px-3 py-2">Kalem</th>
                      <th className="px-3 py-2 text-right">Gelka</th>
                      <th className="px-3 py-2 text-right">EPİAŞ</th>
                      <th className="px-3 py-2 text-right">Fark</th>
                      <th className="px-3 py-2">Kimlik</th>
                      <th className="px-3 py-2">Karşılaştırılabilirlik</th>
                    </tr>
                  </thead>
                  <tbody>
                    {durum.yanit.satirlar.map((s) => (
                      <SatirKarti key={`${s.donem}-${s.kalem}`} satir={s} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="text-xs italic text-gray-500" data-testid="kars-not">
              {durum.yanit.not}
            </p>
          </div>
        )}
      </div>
    </section>
  );
};
