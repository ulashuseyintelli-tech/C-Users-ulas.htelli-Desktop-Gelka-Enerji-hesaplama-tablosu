// =============================================================================
// Fiyat Doğruluğu Faz 1 — TASLAK hesap işareti
// =============================================================================
// Owner teyidi: provisional (kesinleşmemiş) ya da doğrulanmamış fiyat YALNIZ
// açıkça işaretlenmiş taslak hesapta kullanılabilir. Kesin teklif ve müşteriye
// gidecek PDF sunucuda engellenir. Bu bileşen ekrandaki hesabı TASLAK olarak
// işaretler ve gerekçeleri listeler. Onay kutusu YOKTUR: kullanıcı beyanı
// kapıyı açmaz.
//
// Çağrıldığı yerler:
// - App.tsx → fiyat paneli (manuel + AI akışı)
// =============================================================================

import type { PriceReadiness } from './priceReadiness';

interface PriceDraftBannerProps {
  readiness: PriceReadiness;
  period?: string | null;
  /** Elle değiştirilen fiyatı kayıtlı dönem fiyatına döndürür (verilirse düğme görünür). */
  onRestore?: () => void;
}

export function PriceDraftBanner({ readiness, period, onRestore }: PriceDraftBannerProps) {
  if (!readiness.draft) {
    return (
      <div
        data-testid="price-final-badge"
        className="text-[11px] rounded border border-green-200 bg-green-50 px-2 py-1 text-green-800"
      >
        ✓ Fiyat sunucuda doğrulandı{period ? ` (${period})` : ''}. Kesin teklif ve PDF oluşturulabilir.
      </div>
    );
  }
  return (
    <div
      role="status"
      data-testid="price-draft-banner"
      className="text-[11px] rounded border border-amber-300 bg-amber-50 px-2 py-1 space-y-1"
    >
      <div className="font-semibold text-amber-900">
        TASLAK HESAP — {readiness.provisional ? 'fiyat kesinleşmedi (provisional)' : 'fiyat doğrulanmadı'}
      </div>
      <div className="text-amber-800">Kesin teklif kaydı ve müşteriye gidecek PDF oluşturulamaz.</div>
      <ul className="list-disc pl-4 text-amber-800">
        {readiness.reasons.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      {onRestore && (
        <button type="button" onClick={onRestore} className="underline text-blue-700">
          Kayıtlı dönem fiyatına dön
        </button>
      )}
    </div>
  );
}
