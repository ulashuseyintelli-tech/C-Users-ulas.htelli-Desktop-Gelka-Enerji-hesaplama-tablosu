// =============================================================================
// EPİAŞ Karşılaştırma — Türkçe etiketler
// =============================================================================

import type { AlanDurumu, KarsilastirmaTipi } from './types';

/** Satır sonucunun kullanıcıya görünen adı ve rengi. */
export const TIP_ETIKETLERI: Record<
  KarsilastirmaTipi,
  { etiket: string; sinif: string; karsilastirilabilir: boolean }
> = {
  AYNI: { etiket: 'Aynı', sinif: 'bg-green-100 text-green-800', karsilastirilabilir: true },
  DEGER_FARKI: { etiket: 'Değer farkı', sinif: 'bg-amber-100 text-amber-800', karsilastirilabilir: true },
  DONEM_UYUSMAZLIGI: { etiket: 'Dönem uyuşmuyor', sinif: 'bg-red-100 text-red-800', karsilastirilabilir: false },
  BIRIM_UYUSMAZLIGI: { etiket: 'Birim uyuşmuyor', sinif: 'bg-red-100 text-red-800', karsilastirilabilir: false },
  YONTEM_UYUSMAZLIGI: { etiket: 'Yöntem uyuşmuyor', sinif: 'bg-red-100 text-red-800', karsilastirilabilir: false },
  VERSIYON_UYUSMAZLIGI: { etiket: 'Versiyon uyuşmuyor', sinif: 'bg-red-100 text-red-800', karsilastirilabilir: false },
  SEGMENT_UYUSMAZLIGI: { etiket: 'Segment uyuşmuyor', sinif: 'bg-red-100 text-red-800', karsilastirilabilir: false },
  KARSILASTIRILAMAZ: { etiket: 'Karşılaştırılamaz', sinif: 'bg-gray-200 text-gray-700', karsilastirilabilir: false },
};

/** Backend `nedenler` kodlarının açıklaması. */
export const NEDEN_ETIKETLERI: Record<string, string> = {
  kayit_yok: 'Bu dönem için Gelka kaydı yok.',
  api_hatasi: 'EPİAŞ servisinden veri alınamadı.',
  deger_yok: 'Taraflardan birinde sayısal değer yok.',
  donem_bilinmiyor: 'Dönem kimliği okunamadı.',
  donem_uyusmuyor: 'Dönemler farklı.',
  birim_uyusmuyor: 'Birimler farklı.',
  yontem_bilinmiyor: 'Kayıt hangi PTF ortalamasıyla üretildiğini taşımıyor.',
  yontem_uyusmuyor: 'Kayıttaki PTF yöntemi EPİAŞ karşılığından farklı.',
  versiyon_bilinmiyor: 'Kayıt hangi YEKDEM versiyonuna ait olduğunu taşımıyor.',
  versiyon_uyusmuyor: 'Kayıttaki YEKDEM versiyonu EPİAŞ adayından farklı.',
  segment_bilinmiyor: 'Kayıt segment (Serbest Tüketici / GTŞ-K1) bilgisini taşımıyor.',
  segment_uyusmuyor: 'Kayıttaki segment EPİAŞ karşılığından farklı.',
  epias_versiyon_yok: 'EPİAŞ bu dönem için uygun versiyon döndürmedi.',
  epias_karsiligi_yok: 'Kayıttaki yöntemin (abone ağırlıklı) EPİAŞ karşılığı yok.',
  gecmis_tarih_kaniti_yok: 'Geçmiş değerlendirme tarihi: o tarihte hangi versiyonun bilindiği kanıtlanamaz.',
};

/** Kimlik alanı durumlarının kısa gösterimi. */
export const ALAN_DURUM_ETIKETLERI: Record<AlanDurumu, string> = {
  eslesti: 'eşleşti',
  bilinmiyor: 'bilinmiyor',
  uygulanamaz: 'uygulanmaz',
  uyusmuyor: 'uyuşmuyor',
};

/** Yöntem jetonlarının okunur karşılığı. */
export const YONTEM_ETIKETLERI: Record<string, string> = {
  mcp_avg: 'Aritmetik ortalama (EPİAŞ PTF)',
  mcp_wavg: 'Ağırlıklı ortalama (EPİAŞ PTF)',
  abone_agirlikli: 'Abone tüketimiyle ağırlıklı',
};

/** Segment jetonlarının okunur karşılığı. */
export const SEGMENT_ETIKETLERI: Record<string, string> = {
  st: 'Serbest Tüketici',
  gts: 'GTŞ-K1',
};

export function nedenMetni(kod: string): string {
  return NEDEN_ETIKETLERI[kod] ?? kod;
}
