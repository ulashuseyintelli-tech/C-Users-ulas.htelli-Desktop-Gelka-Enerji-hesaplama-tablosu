// =============================================================================
// KAYNAK KİMLİĞİ EŞLEŞTİRME ADAYLARI (2026-01 … 2026-07)
// =============================================================================
// Bunlar DESTEKLENEN HİPOTEZdir, kanıt değildir. Hiçbiri kayda yazılmamıştır,
// hiçbiri geçmiş girişin kaynağını kesin olarak KANITLAMAZ ve hiçbiri fiyat
// farkı hesabında KULLANILMAZ. Fark yalnız backend'in kimliği doğruladığı
// satırlarda (tip = AYNI | DEGER_FARKI) gösterilir.
//
// KURAL: sayısal eşitlik tek başına kaynak kanıtı sayılmaz. Her aday için
// "dayanak" (hangi gözlem hipotezi destekliyor) ile "belirsizlik" (ne
// kanıtlanmadı) ayrı yazılır.
//
// KOŞUL BAĞI: her aday, türetildiği dönem/değer/kaynak koşullarını taşır.
// Kayıt bu koşullardan ayrıldıysa öneri GÖSTERİLMEZ (eski öneri yeni kayda
// otomatik taşınmaz).
//
// Kanıt kökeni (yerel, repoya girmez): 2026-09-12 salt okunur karşılaştırma ve
// Temmuz 2026 canlı API kabulü. Ekrana yerel dosya yolu TAŞINMAZ.
// =============================================================================

import type { Kalem } from './types';

/** Adayın desteklenme düzeyi. Hiçbiri "kanıtlandı" anlamına gelmez. */
export type AdayGucu =
  /** Sayısal eşitliğin ötesinde destekleyici gözlem var; yine de hipotezdir. */
  | 'desteklenen_hipotez'
  /** Yalnız sayısal eşitlik var; destekleyici ek gözlem yok. */
  | 'zayif'
  /** Veriden ayırt edilemiyor; öneri yalnız iş bağlamına dayanıyor. */
  | 'kanit_yok';

/** Adayın türetildiği kayıt koşulları. Kayıt bunlardan ayrılırsa öneri düşer. */
export interface AdayKosulu {
  /** Gözlem anında kayıtta duran değer (TL/MWh). */
  deger: number;
  /** Gözlem anında kaydın kaynak jetonu. */
  kaynak: string;
}

export interface KimlikAdayi {
  donem: string;
  kalem: Kalem;
  /** PTF için yöntem jetonu (mcp_avg | mcp_wavg | abone_agirlikli). */
  onerilenYontem?: string;
  /** YEKDEM için versiyon (YYYY-MM). */
  onerilenVersiyon?: string;
  /** YEKDEM için segment (st | gts). */
  onerilenSegment?: string;
  guc: AdayGucu;
  kosul: AdayKosulu;
  dayanak: string[];
  belirsizlik: string[];
}

/** Kaydın kendisinden gelen (yapısal) kimlik — bu kısım hipotez değildir. */
export const YAPISAL_KIMLIK = {
  donem: 'market_reference_prices.period alanından gelir (yapısal).',
  birim: 'Şema alan adları ptf_tl_per_mwh / yekdem_tl_per_mwh → TL/MWh (yapısal).',
} as const;

/** Aday koşulu karşılaştırmasında kullanılan tolerans (TL/MWh). */
export const KOSUL_TOLERANSI = 0.005;

const KAYNAK_JETONU = 'manual_override';

const PTF_ORTAK_BELIRSIZLIK = [
  'Kayıtta source=manual_override; yöntem jetonu YOK.',
  'Değer elle girilmiş (created_at 2026-08-18); hangi sütunun kopyalandığı KAYITLI DEĞİL.',
  'Gözlemler bu hipotezi destekler; geçmiş girişin kaynağını KANITLAMAZ.',
];

const PTF_ORTAK_DAYANAK = [
  'Yedi dönemin 7/7\'sinde EPİAŞ aritmetik ortalamasıyla birebir (tek başına kanıt DEĞİL).',
  'EPİAŞ ağırlıklı ortalamasından belirgin sapma (−34,48 … −204,80 TL/MWh): bu dönemlerde ' +
  'gözlem "ağırlıklı" okumasıyla bağdaşmıyor.',
  'docs/sot-x-offer-ptf-parity.md: aylık skaler = ağırlıksız.',
];

/** Dönem → (Gelka kaydındaki PTF değeri, varsa saatlik seri gözlemi). */
const PTF_KAYIT: Record<string, { deger: number; saat?: number; ortalama?: number; fark?: number }> = {
  '2026-01': { deger: 2894.92 },
  '2026-02': { deger: 2078.2, saat: 672, ortalama: 2078.195, fark: -0.005 },
  '2026-03': { deger: 1620.32, saat: 744, ortalama: 1620.3243, fark: 0.0043 },
  '2026-04': { deger: 921.06, saat: 720, ortalama: 921.0566, fark: -0.0034 },
  '2026-05': { deger: 590.9, saat: 744, ortalama: 590.9, fark: -0.0 },
  '2026-06': { deger: 1240.16, saat: 720, ortalama: 1240.1616, fark: 0.0016 },
  '2026-07': { deger: 2699.61, saat: 744, ortalama: 2699.6135, fark: 0.0035 },
};

/** Dönem → (Gelka kaydındaki YEKDEM değeri, ilk/son versiyon farkları). */
const YEKDEM_KAYIT: Record<string, { deger: number; ilk: number; son: number | null }> = {
  '2026-01': { deger: 162.73, ilk: 0.003, son: 0.037 },
  '2026-02': { deger: 479.35, ilk: 0.003, son: -0.365 },
  '2026-03': { deger: 747.8, ilk: 0.003, son: -0.53 },
  '2026-04': { deger: 1038.34, ilk: -0.003, son: 0.369 },
  '2026-05': { deger: 1306.1, ilk: -0.003, son: -11.136 },
  '2026-06': { deger: 1083.63, ilk: 0.001, son: -3.243 },
  '2026-07': { deger: 486.31, ilk: -0.004, son: null },
};

export const DONEMLER = [
  '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07',
] as const;

function ptfAdayi(donem: string): KimlikAdayi {
  const k = PTF_KAYIT[donem];
  const dayanak = [...PTF_ORTAK_DAYANAK];
  const belirsizlik = [...PTF_ORTAK_BELIRSIZLIK];
  if (k.saat !== undefined) {
    dayanak.push(
      `Gelka'nın kendi saatlik serisi (${k.saat} saat, kaynak epias_excel) ortalaması ` +
      `${k.ortalama} → aylık kayıtla fark ${k.fark} TL/MWh. Bu, aylık değerin aritmetik ` +
      'ortalama olduğu hipotezini DESTEKLER; kaydın nasıl üretildiğini göstermez.',
    );
  } else {
    belirsizlik.push(
      'Bu dönem için Gelka\'da saatlik seri YOK → destekleyici gözlem yok, yalnız sayısal eşitlik var.',
    );
  }
  if (donem === '2026-07') {
    dayanak.push(
      'Canlı API kabulü (2026-09-12): dönen 744 saatlik fiyatın ortalaması 2699,6135080645163 — ' +
      'Gelka saatlik ortalamasıyla birebir.',
    );
  }
  return {
    donem,
    kalem: 'PTF',
    onerilenYontem: 'mcp_avg',
    guc: k.saat !== undefined ? 'desteklenen_hipotez' : 'zayif',
    kosul: { deger: k.deger, kaynak: KAYNAK_JETONU },
    dayanak,
    belirsizlik,
  };
}

function yekdemAdayi(donem: string): KimlikAdayi {
  const f = YEKDEM_KAYIT[donem];
  const ayirtEdici = f.son !== null;
  const dayanak = [
    `Kayıt, dönemin İLK yayımlanan versiyonuyla ${f.ilk} TL/MWh farkla örtüşüyor ` +
    '(yuvarlama düzeyinde).',
  ];
  const belirsizlik = [
    'Kayıtta versiyon jetonu YOK; "ilk versiyon" bir gözlemdir, kayıtlı bir niyet değildir.',
    'Gözlem bu hipotezi destekler; geçmiş girişin kaynağını KANITLAMAZ.',
  ];
  if (ayirtEdici) {
    dayanak.push(
      `SON yayımlanan versiyonla fark ${f.son} TL/MWh → bu dönemde gözlem "son versiyon" ` +
      'okumasıyla bağdaşmıyor; "ilk versiyon" hipotezini DESTEKLER.',
    );
  } else {
    belirsizlik.push(
      'Bu dönemde tek versiyon yayımlanmış (ilk = son) → hipotezler ayırt EDİLEMİYOR.',
    );
  }
  return {
    donem,
    kalem: 'YEKDEM',
    onerilenVersiyon: donem,
    onerilenSegment: 'st',
    guc: ayirtEdici ? 'desteklenen_hipotez' : 'zayif',
    kosul: { deger: f.deger, kaynak: KAYNAK_JETONU },
    dayanak,
    belirsizlik: [
      ...belirsizlik,
      'SEGMENT KANITSIZ: Serbest Tüketici ve GTŞ-K1 birim fiyatları incelenen 28/28 satırda EŞİT. ' +
      'Segment önerisi yalnız iş bağlamına (ikili anlaşmalı serbest tüketici) dayanır; owner onayı gerekir.',
    ],
  };
}

/** Ocak–Temmuz 2026 için desteklenen kimlik hipotezleri. */
export const KIMLIK_ADAYLARI: KimlikAdayi[] = DONEMLER.flatMap((d) => [
  ptfAdayi(d),
  yekdemAdayi(d),
]);

/** Adayın türetildiği kayıt koşulu hâlâ geçerli mi? */
export function kosulUyuyor(
  aday: KimlikAdayi,
  guncel: { deger: number | null; kaynak_jetonu: string | null },
): boolean {
  if (guncel.kaynak_jetonu !== aday.kosul.kaynak) return false;
  if (guncel.deger === null || guncel.deger === undefined) return false;
  return Math.abs(guncel.deger - aday.kosul.deger) <= KOSUL_TOLERANSI;
}

/**
 * Dönem + kalem için adayı döndürür; YALNIZ kaydın dönem/değer/kaynak
 * koşulları adayın türetildiği koşullarla hâlâ eşleşiyorsa.
 *
 * Kayıt değişmişse (değer güncellenmiş, kaynak jetonu farklı) eski öneri
 * OTOMATİK TAŞINMAZ ve undefined döner.
 */
export function adayBul(
  donem: string,
  kalem: Kalem,
  guncel?: { deger: number | null; kaynak_jetonu: string | null },
): KimlikAdayi | undefined {
  const aday = KIMLIK_ADAYLARI.find((a) => a.donem === donem && a.kalem === kalem);
  if (!aday) return undefined;
  if (!guncel) return undefined;
  return kosulUyuyor(aday, guncel) ? aday : undefined;
}
