// =============================================================================
// Fiyat Onayı — API istemcisi
// =============================================================================
// Mevcut adminApi örneğini kullanır (X-Admin-Key yalnız bellekte; interceptor).
// EPİAŞ kullanıcı adı/parolası ve TGT tarayıcıya TAŞINMAZ: resmî veri yalnız
// sunucuda, sunucu ortam değişkenleriyle çekilir. Onay isteğinde istemcinin
// gönderdiği DEĞER kullanılmaz; sunucu adayı yeniden çeker ve parmak izini karşılaştırır.
// =============================================================================

import { adminApi } from '../../api';
import type {
  OnayAdayi, OnayGecmisi, OnayHatasi, OnayIstegi, OnayKalemi, OnaySegmenti, OnaySonucu,
} from './types';

/**
 * Onaya sunulacak resmî adayı getirir (SALT OKUNUR).
 *
 * Çağrıldığı yerler:
 * - FiyatOnayPaneli → "Resmî adayı getir" düğmesi (sayfa açılışında OTOMATİK çağrılmaz)
 */
export async function fetchOnayAdayi(
  period: string,
  kalem: OnayKalemi,
  segment?: OnaySegmenti | null,
): Promise<OnayAdayi> {
  const response = await adminApi.get<OnayAdayi>('/admin/market-prices/approval-candidate', {
    params: { period, kalem, ...(kalem === 'YEKDEM' && segment ? { segment } : {}) },
  });
  return response.data;
}

/**
 * Yetkili onay (YAZMA). Sunucu adayı yeniden çeker; farklıysa 409 aday_degisti döner.
 *
 * Çağrıldığı yerler:
 * - FiyatOnayPaneli → "Yetkili onaya gönder" düğmesi
 */
export async function onayGonder(istek: OnayIstegi): Promise<OnaySonucu> {
  const response = await adminApi.post<OnaySonucu>('/admin/market-prices/approve', istek);
  return response.data;
}

/**
 * Dönemin onay revizyonları ve kaynak kanıtları (SALT OKUNUR; EPİAŞ'a gitmez).
 *
 * Çağrıldığı yerler:
 * - FiyatOnayPaneli → "Onay geçmişi" düğmesi ve başarılı onaydan sonra
 */
export async function fetchOnayGecmisi(period: string): Promise<OnayGecmisi> {
  const response = await adminApi.get<OnayGecmisi>('/admin/market-prices/approvals', { params: { period } });
  return response.data;
}

interface HataBenzeri {
  response?: {
    status?: number;
    data?: { detail?: ({ error?: string; message?: string; yeni_aday?: OnayAdayi }) | string };
  };
}

/**
 * Ham hatayı ekranda anlatılabilir türe çevirir. Gizli bilgi içeren ham gövde basılmaz;
 * yalnız sunucunun kullanıcıya yönelik mesajı kullanılır.
 *
 * Çağrıldığı yerler:
 * - FiyatOnayPaneli → aday getirme, onay ve geçmiş isteklerinin catch blokları
 */
export function onayHatasiniSiniflandir(hata: unknown): OnayHatasi {
  const h = hata as HataBenzeri;
  const durum = h?.response?.status;
  const detay = h?.response?.data?.detail;
  const nesne = typeof detay === 'object' && detay ? detay : undefined;
  const kod = nesne?.error;
  const sunucuMesaji = nesne?.message;

  if (kod === 'approval_not_configured') {
    return { tur: 'onay_yapilandirilmamis', kod,
      mesaj: 'Fiyat onayı bu sunucuda yapılandırılmamış (onay anahtarı tanımlı değil). Onay yazılamaz.' };
  }
  if (durum === 401) {
    return { tur: 'yetkisiz', kod,
      mesaj: 'Yönetici/onay anahtarı gönderilmedi. Yönetici oturumunu açıp yeniden deneyin.' };
  }
  if (durum === 403) {
    return { tur: 'yasak', kod, mesaj: 'Anahtar geçersiz. Fiyat onaylama yetkiniz yok.' };
  }
  if (kod === 'kimlik_bilgisi_eksik') {
    return { tur: 'kimlik_bilgisi_eksik', kod,
      mesaj: 'Sunucuda EPİAŞ kimlik bilgisi tanımlı değil. Bilgiler yalnız sunucu ortamında tanımlanır; '
        + 'bu ekrandan girilmez.' };
  }
  if (kod === 'feature_disabled') {
    return { tur: 'ozellik_kapali', kod,
      mesaj: 'EPİAŞ resmî veri erişimi sunucuda kapalı (EPIAS_COMPARE_ENABLED). Aday üretilemez.' };
  }
  if (kod === 'onay_altyapisi_yok') {
    return { tur: 'altyapi_yok', kod,
      mesaj: 'Onay tabloları bu veritabanında yok (migration uygulanmamış). Onay yazılamaz; teklifler kesinleşemez.' };
  }
  if (kod === 'aday_degisti') {
    return { tur: 'aday_degisti', kod, yeniAday: nesne?.yeni_aday,
      mesaj: 'Resmî aday ekranda gösterilenden farklı. Hiçbir şey yazılmadı; yeni adayı inceleyip yeniden onaylayın.' };
  }
  if (kod === 'kayit_degisti' || kod === 'onay_yarisi') {
    return { tur: 'yeniden_onay', kod,
      mesaj: (sunucuMesaji || 'Kayıt başka bir işlemle değişti.') + ' Adayı yeniden getirin.' };
  }
  if (kod === 'aday_dogrulanamadi') {
    return { tur: 'dogrulanamadi', kod,
      mesaj: sunucuMesaji || 'Resmî aday doğrulanamadı; eksik ya da tutarsız aday onaylanamaz.' };
  }
  if (durum === 422) {
    return { tur: 'gecersiz_istek', kod, mesaj: sunucuMesaji || 'İstek geçersiz.' };
  }
  if (typeof durum === 'number' && durum >= 500) {
    return { tur: 'sunucu', kod,
      mesaj: sunucuMesaji && durum !== 500
        ? sunucuMesaji
        : 'Sunucu isteği tamamlayamadı. EPİAŞ servisine erişilemiyor olabilir; kayıtlar değişmedi.' };
  }
  return { tur: 'ag', mesaj: 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edin; kayıtlar değişmedi.' };
}
