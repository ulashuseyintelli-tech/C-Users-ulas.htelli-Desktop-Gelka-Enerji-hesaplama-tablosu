// =============================================================================
// EPİAŞ Karşılaştırma — yanıt sözleşmesi (SALT OKUNUR)
// =============================================================================
// Backend karşılığı: GET /admin/market-prices/epias-compare
// (backend/app/epias_compare.build_comparison — PR #57 / e0c47cf)
// Bu modül YALNIZ okuma tipleri içerir; yazma/onay tipi YOKTUR.
// =============================================================================

/** Bir kimlik alanının karşılaştırma durumu. */
export type AlanDurumu = 'eslesti' | 'bilinmiyor' | 'uygulanamaz' | 'uyusmuyor';

/** Satır sonucunun tipi (backend `tip` alanı). */
export type KarsilastirmaTipi =
  | 'AYNI'
  | 'DEGER_FARKI'
  | 'DONEM_UYUSMAZLIGI'
  | 'BIRIM_UYUSMAZLIGI'
  | 'YONTEM_UYUSMAZLIGI'
  | 'VERSIYON_UYUSMAZLIGI'
  | 'SEGMENT_UYUSMAZLIGI'
  | 'KARSILASTIRILAMAZ';

export type Kalem = 'PTF' | 'YEKDEM';

export type AlanAdi = 'donem' | 'birim' | 'yontem' | 'versiyon' | 'segment';

export interface GelkaTarafi {
  deger: number | null;
  birim: string | null;
  yontem: string | null;
  versiyon: string | null;
  segment: string | null;
  kaynak_jetonu: string | null;
  durum: string | null;
  kayit_id: number | null;
}

export interface EpiasTarafi {
  donem: string | null;
  birim: string | null;
  yontem: string | null;
  versiyon: string | null;
  segment: string | null;
  deger: number | null;
  /** EPİAŞ adayı hiçbir zaman "kesin" sayılmaz; backend bunu BELIRSIZ yazar. */
  kesinlik?: string;
  aday_secimi?: string;
}

export interface KarsilastirmaSatiri {
  donem: string;
  kalem: Kalem;
  tip: KarsilastirmaTipi;
  nedenler: string[];
  alanlar: Record<AlanAdi, AlanDurumu>;
  /** Yalnız kimlik doğrulanmış satırlarda dolu gelir; aksi hâlde null. */
  fark: number | null;
  gelka: GelkaTarafi;
  epias: EpiasTarafi | null;
}

export interface KarsilastirmaUyarisi {
  kod: string;
  mesaj: string;
  donem?: string;
}

export interface KarsilastirmaYaniti {
  kayit: string;
  olusturuldu_utc: string;
  evaluated_at: string;
  gecmis_tarih_kaniti: string;
  tolerans_tl_mwh: number;
  donemler: string[];
  uyarilar: KarsilastirmaUyarisi[];
  satirlar: KarsilastirmaSatiri[];
  ozet: Record<string, number>;
  not: string;
}

/** Ekranda ayrı ayrı anlatılabilmesi için sınıflandırılmış hata. */
export type HataTuru =
  | 'ozellik_kapali'
  | 'kimlik_bilgisi_eksik'
  | 'yetkisiz'
  | 'yasak'
  | 'gecersiz_istek'
  | 'sunucu'
  | 'ag';

export interface KarsilastirmaHatasi {
  tur: HataTuru;
  mesaj: string;
}
