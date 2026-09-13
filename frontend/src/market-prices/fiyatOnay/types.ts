// =============================================================================
// Fiyat Onayı — tipler (backend app/price_approval.py sözleşmesinin yansıması)
// =============================================================================

export type OnayKalemi = 'PTF' | 'YEKDEM';
export type OnaySegmenti = 'st' | 'gts';

/** PTF adayının saatlik dayanak denetimi (saatlik_ptf_dogrula raporu). */
export interface SaatlikDogrulama {
  beklenen_saat: number;
  donen_kalem: number;
  tekil_gecerli_saat: number;
  eksik_saat: number;
  eksik_ornek: string[];
  tekrarlanan_saat: number;
  donem_disi_saat: number;
  gecersiz_saat_damgasi: number;
  gecersiz_fiyat: number;
  ilk_saat: string;
  son_saat: string;
  sayfa_toplam: number | null;
  saatlik_ortalama: string | null;
  yayimlanan_price_avg: string | null;
  ortalama_farki: string | null;
  yuvarlama_toleransi: string;
  saatlik_veri_sha256: string;
  onaylanabilir: boolean;
  nedenler: string[];
}

/** Resmî kaynak kanıtı (onay kaydında AYNEN saklanan JSON). */
export interface KaynakKaniti {
  kalem: OnayKalemi;
  kaynak: string;
  servis: string;
  istek: { startDate: string; endDate: string };
  period: string;
  alan?: string;
  priceAvg?: string;
  ptfWeightedAvg?: string;
  yontem?: string;
  saatlik_dogrulama?: SaatlikDogrulama;
  version?: string;
  ayni_donem_versiyonlari?: string[];
  supplierUnitCost?: string;
  unitCost?: string;
  segment?: OnaySegmenti;
  segment_alani?: string;
  resmi_kesinlesme?: string;
  birim?: string | null;
  [alan: string]: unknown;
}

export interface OnayAdayi {
  kalem: OnayKalemi;
  period: string;
  value: number;
  basis: string | null;
  segment: OnaySegmenti | null;
  version: string | null;
  birim?: string | null;
  kaynak_kanit_sha256: string;
  kaynak_kanit: KaynakKaniti;
  yontem_etiketi?: string;
  segment_etiketi?: string;
  onaylanabilir: boolean;
  onaylanamama_nedenleri: string[];
  aday_parmak_izi: string;
  beklenen_revision: number;
  beklenen_kayit_parmak_izi?: string;
  mevcut_kayit?: { id: number; ptf_tl_per_mwh: number; status: string; source: string } | null;
  gecerli_onay?: {
    revision: number; value: number; version?: string;
    onaylayan_beyan: string; onaylayan_dogrulandi?: false; dogrulanan_yetki?: string;
  } | null;
}

export interface OnayIstegi {
  period: string;
  kalem: OnayKalemi;
  segment?: OnaySegmenti | null;
  aday_parmak_izi: string;
  beklenen_revision: number;
  beklenen_kayit_parmak_izi?: string | null;
  /** Formdaki ad — BEYAN; sunucu kişiyi doğrulamaz. Doğrulanan yetki istemciden gönderilmez. */
  onaylayan_beyan: string;
  change_reason: string;
}

export interface OnaySonucu {
  kalem: OnayKalemi;
  period: string;
  revision: number;
  value: number;
  segment?: OnaySegmenti;
  version?: string;
  basis?: string;
  yontem_etiketi?: string;
  kaynak_kanit_sha256: string;
  onaylayan_beyan: string;
  onaylayan_dogrulandi: false;
  dogrulanan_yetki: string;
}

export interface OnayGecmisSatiri {
  id: number;
  period: string;
  revision: number;
  value: number;
  segment?: OnaySegmenti;
  version?: string;
  basis?: string;
  kaynak_kanit_sha256: string;
  kaynak_kanit: KaynakKaniti;
  onaylayan_beyan: string;
  dogrulanan_yetki: string;
  approved_at: string;
  change_reason: string;
  gecerli?: boolean;
  guncel?: boolean;
}

export interface OnayGecmisi {
  period: string;
  altyapi: boolean;
  ptf: OnayGecmisSatiri[];
  yekdem: OnayGecmisSatiri[];
  resmi_kesinlesme?: string;
}

export type OnayHataTuru =
  | 'onay_yapilandirilmamis'
  | 'yetkisiz'
  | 'yasak'
  | 'ozellik_kapali'
  | 'kimlik_bilgisi_eksik'
  | 'altyapi_yok'
  | 'aday_degisti'
  | 'yeniden_onay'
  | 'dogrulanamadi'
  | 'gecersiz_istek'
  | 'sunucu'
  | 'ag';

export interface OnayHatasi {
  tur: OnayHataTuru;
  kod?: string;
  mesaj: string;
  /** 409 aday_degisti: sunucunun yeniden çektiği güncel aday. */
  yeniAday?: OnayAdayi;
}
