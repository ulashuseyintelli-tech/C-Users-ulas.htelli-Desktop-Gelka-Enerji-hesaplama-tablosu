// =============================================================================
// EPİAŞ Karşılaştırma — API istemcisi (SALT OKUNUR)
// =============================================================================
// Mevcut adminApi örneğini kullanır (X-Admin-Key interceptor'ı dâhil).
// Yeni bir yetkilendirme düzeni KURMAZ. Parola/TGT tarayıcıya TAŞINMAZ:
// kimlik bilgileri yalnız sunucu tarafında, ortam değişkenlerinden okunur.
// =============================================================================

import { adminApi } from '../../api';
import type { KarsilastirmaHatasi, KarsilastirmaYaniti } from './types';

/**
 * Dönem aralığı için EPİAŞ karşılaştırma raporunu getirir.
 *
 * Çağrıldığı yerler:
 * - EpiasCompareSection → kullanıcı "Karşılaştır" düğmesine bastığında
 *   (sayfa açılışında OTOMATİK çağrılmaz).
 *
 * @param fromPeriod Başlangıç dönemi (YYYY-MM)
 * @param toPeriod   Bitiş dönemi (YYYY-MM)
 * @param signal     İsteği iptal etmek için AbortSignal
 */
export async function fetchEpiasComparison(
  fromPeriod: string,
  toPeriod: string,
  signal?: AbortSignal,
): Promise<KarsilastirmaYaniti> {
  const response = await adminApi.get<KarsilastirmaYaniti>(
    '/admin/market-prices/epias-compare',
    { params: { from_period: fromPeriod, to_period: toPeriod }, signal },
  );
  return response.data;
}

interface HataBenzeri {
  response?: { status?: number; data?: { detail?: { error?: string; message?: string } | string } };
  message?: string;
}

/**
 * Ham hatayı ekranda anlatılabilir bir türe çevirir.
 *
 * Sunucu mesajı maskelenmiş gelir (backend `_maskele`); yine de burada
 * yalnız sınıflandırma yapılır, ham gövde ekrana basılmaz.
 *
 * Çağrıldığı yerler:
 * - EpiasCompareSection → catch bloğu
 */
export function hatayiSiniflandir(hata: unknown): KarsilastirmaHatasi {
  const h = hata as HataBenzeri;
  const durum = h?.response?.status;
  const detay = h?.response?.data?.detail;
  const kod = typeof detay === 'object' && detay ? detay.error : undefined;
  const sunucuMesaji = typeof detay === 'object' && detay ? detay.message : undefined;

  if (durum === 503 || kod === 'feature_disabled') {
    return {
      tur: 'ozellik_kapali',
      mesaj:
        'EPİAŞ karşılaştırması kapalı. Sunucuda EPIAS_COMPARE_ENABLED açılmadan bu bölüm veri getirmez.',
    };
  }
  if (durum === 401) {
    return { tur: 'yetkisiz', mesaj: 'Yönetici anahtarı gönderilmedi. Oturumunuzu yenileyin.' };
  }
  if (durum === 403) {
    return { tur: 'yasak', mesaj: 'Yönetici anahtarı geçersiz. Bu bölümü görüntüleme yetkiniz yok.' };
  }
  if (durum === 422) {
    return {
      tur: 'gecersiz_istek',
      mesaj: sunucuMesaji || 'Dönem aralığı geçersiz (en fazla 12 dönem, biçim YYYY-MM).',
    };
  }
  if (typeof durum === 'number' && durum >= 500) {
    return {
      tur: 'sunucu',
      mesaj:
        'Sunucu isteği tamamlayamadı. EPİAŞ servisine erişilemiyor olabilir; kayıtlar değişmedi.',
    };
  }
  return {
    tur: 'ag',
    mesaj: 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edin; kayıtlar değişmedi.',
  };
}
