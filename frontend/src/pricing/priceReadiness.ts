// =============================================================================
// Fiyat Doğruluğu Faz 1 — teklif kesinleştirme için PTF/YEKDEM hazırlık durumu
// =============================================================================
// Backend kapısının (price_provenance.build_price_provenance → POST /offers 422
// price_unverified) istemci tarafı yansımasıdır: düğmeleri ve uyarıları yönetir.
// Esas koruma SUNUCUDADIR; bu modül kullanıcıya erken ve açık geri bildirim verir.
//
// Üç YEKDEM durumu birbirinden AYRILIR:
// - bilinmiyor (null)   → kesinleştirme engellenir
// - değer (0 dahil)     → 0, DB'de "girilmedi" ile karışabildiği için HER ZAMAN açık onay ister
// - "YEKDEM hariç"      → açık seçim; değer gerekmez
//
// Çağrıldığı yerler:
// - App.tsx → PDF İndir / teklif kaydı düğmeleri, handleDownloadPdf kapısı, fiyat paneli uyarıları
// =============================================================================

export interface PriceProvenanceComponent {
  value?: number | null;
  status?: string;
  source?: string | null;
  source_detail?: string | null;
  system_verified?: boolean;
  epias?: boolean;
  mode?: 'included' | 'excluded';
  exclusion_basis?: string;
  period_status?: string;
  period_value?: number | null;
  db_values?: number[];
}

export interface PriceProvenance {
  version?: number;
  period?: string | null;
  system_lookup?: string;
  ptf?: PriceProvenanceComponent;
  yekdem?: PriceProvenanceComponent;
  requires_confirmation?: boolean;
  user_confirmed?: boolean;
  verified?: boolean;
  verified_by?: 'system' | 'user' | null;
  epias_basis?: boolean;
  blocking_reasons?: string[];
  messages?: string[];
}

export interface PriceReadinessInput {
  ptf: number | null;
  yekdem: number | null;
  /** YEKDEM teklife dahil mi (manuel: "YEKDEM hariç" seçili değil; AI: faturaya göre) */
  yekdemIncluded: boolean;
  /** Sunucunun EKRANDAKİ değerler için döndürdüğü kaynak (değerler elle değiştiyse geçersiz) */
  provenance: PriceProvenance | null | undefined;
  valuesEditedByUser: boolean;
  userConfirmed: boolean;
}

export interface PriceReadiness {
  ready: boolean;
  ptfMissing: boolean;
  yekdemMissing: boolean;
  requiresConfirmation: boolean;
  reasons: string[];
}

const sonluSayi = (v: number | null | undefined): v is number =>
  typeof v === 'number' && Number.isFinite(v);

export function evaluatePriceReadiness(input: PriceReadinessInput): PriceReadiness {
  const ptfMissing = !(sonluSayi(input.ptf) && input.ptf > 0);
  const yekdemMissing = input.yekdemIncluded && !(sonluSayi(input.yekdem) && input.yekdem >= 0);
  const prov = input.valuesEditedByUser ? null : input.provenance ?? null;
  const ptfSistemce = prov?.ptf?.system_verified === true;
  const yekdemSistemce =
    !input.yekdemIncluded || (prov?.yekdem?.system_verified === true && input.yekdem !== 0);
  const requiresConfirmation = !ptfMissing && !yekdemMissing && (!ptfSistemce || !yekdemSistemce);

  const reasons: string[] = [];
  if (ptfMissing) reasons.push('Teklif PTF değeri eksik.');
  if (yekdemMissing) {
    reasons.push("YEKDEM birim bedeli bilinmiyor: değer girin ya da 'YEKDEM hariç' seçin.");
  }
  if (requiresConfirmation && !input.userConfirmed) {
    reasons.push('Fiyat kaynağı doğrulanmadı: PTF/YEKDEM değerlerini kontrol edip onaylayın.');
  }
  return { ready: reasons.length === 0, ptfMissing, yekdemMissing, requiresConfirmation, reasons };
}

/** Fiyat kaynağının kısa Türkçe etiketi (panel ve sonuç rozeti). */
export function priceSourceLabel(source: string | null | undefined): string {
  if (!source) return 'kaynak yok';
  if (source === 'manual_override') return 'manuel kayıt';
  if (source === 'reference_scalar') return 'aylık referans kaydı';
  if (source.startsWith('hourly_consumption')) return 'gerçek tüketim ağırlıklı';
  if (source.startsWith('hourly_weighted')) return 'saatlik ağırlıklı (profil)';
  if (source === 'override') return 'kullanıcı değeri';
  if (source === 'user_entered') return 'kullanıcı girişi';
  if (source === 'not_found') return 'PTF yok';
  return source;
}
