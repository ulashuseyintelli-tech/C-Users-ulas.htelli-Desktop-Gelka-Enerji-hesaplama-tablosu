// =============================================================================
// Fiyat Doğruluğu Faz 1 — teklif kesinleştirme için PTF/YEKDEM hazırlık durumu
// =============================================================================
// Backend kapısının (price_provenance.build_price_provenance → POST /offers 422
// price_unverified) istemci tarafı yansımasıdır. Düğmeleri, TASLAK işaretini ve
// uyarıları yönetir. Esas koruma SUNUCUDADIR; bu modül yalnız erken ve açık geri
// bildirim verir. Kullanıcı onayı kapıyı AÇMAZ (owner teyidi: doğrulama sunucuda).
//
// Kesin teklif için:
// - PTF, sunucunun doğruladığı (güvenilir + KESİN/final) dönem değeriyle aynı olmalı;
// - YEKDEM uygulaması AÇIKÇA seçilmeli: dahil / hariç (doğrulanmış bir muafiyet
//   kuralı olmadığı için "muaf" seçeneği yok);
// - dahilse YEKDEM, dönemin güvenilir + kesin kaydıyla aynı olmalı.
// provisional (kesinleşmemiş) kayıttan gelen fiyat yalnız TASLAK hesapta kullanılır.
//
// YEKDEM durumları birbirine EŞİTLENMEZ:
// - bilinmiyor (null)        → engel
// - gerçek 0                 → yetkili ekrandan açıkça ve kesin girilmişse sunucu
//                              doğrular (denetim kaydı); değer 0 olarak gösterilir
// - anlamı bilinmeyen 0      → açık giriş kaydı olmayan eski sıfır; kesin teklifte
//                              kullanılamaz, otomatik gerçek ya da eksik sayılmaz
// - hariç                    → açık seçim; değer gerekmez
//
// Sürüm 3 (OWNER-KARARI-01/02): provenance.version >= 3 ise kesin teklif için PTF'in
// yetkili onaylı aylık aritmetik PTF olması (ptf.approval) ve YEKDEM dahilse segmentin
// açıkça seçilip o segmentin onaylı değeriyle eşleşmesi gerekir. Sürüm 2 provenance'ı
// (eski yanıtlar) eski kurallarla değerlendirilir.
//
// Çağrıldığı yerler:
// - App.tsx → PDF İndir / teklif kaydı düğmeleri, handleDownloadPdf kapısı, fiyat paneli
// - pricing/PriceDraftBanner.tsx → TASLAK işareti ve gerekçe listesi
// - pricing/YekdemModeSelector.tsx → seçenek etiketleri
// =============================================================================

export type YekdemMode = 'included' | 'excluded';
export type YekdemSegment = 'st' | 'gts';

export const YEKDEM_SEGMENT_LABELS: Record<YekdemSegment, string> = {
  st: 'Serbest Tüketici',
  gts: 'GTŞ-K1',
};

export const YEKDEM_MODE_LABELS: Record<YekdemMode, string> = {
  included: 'Dahil',
  excluded: 'Hariç',
};

export interface PriceProvenanceComponent {
  value?: number | null;
  status?: string;
  source?: string | null;
  source_detail?: string | null;
  trusted?: boolean;
  final?: boolean;
  system_verified?: boolean;
  epias?: boolean;
  db_values?: number[];
  // YEKDEM'e özgü: seçim ve dönemin kendi durumu (seçimden bağımsız)
  mode?: YekdemMode | null;
  mode_basis?: string | null;
  period_status?: string;
  period_value?: number | null;
  period_record_status?: string | null;
  period_trusted?: boolean;
  period_verified?: boolean;
  /** Kayıtlı 0'ın açık giriş kanıtı (denetim satırı); yoksa null */
  period_zero_audit_id?: number | null;
  // Sürüm 3: yetkili onay kimliği ve YEKDEM segmenti
  approval?: Record<string, unknown> | null;
  yontem_etiketi?: string | null;
  segment?: YekdemSegment | null;
  segment_basis?: string | null;
}

export interface PriceProvenance {
  version?: number;
  period?: string | null;
  system_lookup?: string;
  ptf?: PriceProvenanceComponent;
  yekdem?: PriceProvenanceComponent;
  verified?: boolean;
  verified_by?: 'system' | null;
  draft_only?: boolean;
  provisional?: boolean;
  epias_basis?: boolean;
  blocking_reasons?: string[];
  messages?: string[];
}

export interface PriceReadinessInput {
  ptf: number | null;
  yekdem: number | null;
  /** YEKDEM uygulaması (açık seçim); null = seçilmedi */
  yekdemMode: YekdemMode | null;
  /** Sunucunun EKRANDAKİ değerler için döndürdüğü kaynak (değerler elle değiştiyse geçersiz) */
  provenance: PriceProvenance | null | undefined;
  valuesEditedByUser: boolean;
  /** Sürüm 3: YEKDEM segmentinin açık seçimi; null/undefined = seçilmedi */
  yekdemSegment?: YekdemSegment | null;
}

export interface PriceReadiness {
  /** Kesin teklif ve PDF için hazır (sunucu kapısının yansıması) */
  ready: boolean;
  /** Ekrandaki hesap TASLAK: fiyat eksik, doğrulanmamış ya da kesinleşmemiş */
  draft: boolean;
  /** En az bir fiyat kesinleşmemiş (provisional) kayıttan geliyor */
  provisional: boolean;
  ptfMissing: boolean;
  yekdemMissing: boolean;
  modeRequired: boolean;
  reasons: string[];
}

const sonluSayi = (v: number | null | undefined): v is number =>
  typeof v === 'number' && Number.isFinite(v);

/** Sunucu toleransıyla aynı (0,01 TL/MWh). */
const ayni = (a: number | null | undefined, b: number | null | undefined): boolean =>
  sonluSayi(a) && sonluSayi(b) && Math.abs(a - b) <= 0.01;

export function evaluatePriceReadiness(input: PriceReadinessInput): PriceReadiness {
  const reasons: string[] = [];
  const ptfMissing = !(sonluSayi(input.ptf) && input.ptf > 0);
  const modeRequired = input.yekdemMode === null;
  const included = input.yekdemMode === 'included';
  const yekdemMissing = included && !sonluSayi(input.yekdem);
  // Elle değiştirilen değerler sunucuda doğrulanmadı: eldeki kaynak bilgisi geçersizdir.
  const prov = input.valuesEditedByUser ? null : input.provenance ?? null;
  let provisional = false;

  if (ptfMissing) {
    reasons.push('Teklif PTF değeri eksik.');
  } else if (input.valuesEditedByUser) {
    reasons.push('Fiyat elle değiştirildi ve sunucuda doğrulanmadı. Kesin teklif için kayıtlı dönem fiyatına dönün.');
  } else if (!prov?.ptf) {
    reasons.push('Fiyat kaynağı bilinmiyor: sunucu doğrulaması yok.');
  } else if (prov.ptf.system_verified !== true) {
    if (prov.ptf.status === 'matched' && prov.ptf.trusted === true && prov.ptf.final === false) {
      provisional = true;
      reasons.push('PTF dönem kaydı kesinleşmemiş (provisional). Yalnız taslak hesapta kullanılabilir.');
    } else if ((prov.version ?? 0) >= 3 && prov.ptf.status === 'matched' && prov.ptf.trusted === true
        && prov.ptf.final === true && !prov.ptf.approval) {
      reasons.push('PTF için yetkili onay yok: kesin teklifte yalnız kaynağı doğrulanmış ve onaylanmış aylık aritmetik PTF kullanılır.');
    } else {
      reasons.push('PTF kaynağı doğrulanmadı: dönemin kesin kaydıyla eşleşmiyor.');
    }
  }

  if (modeRequired) {
    reasons.push("YEKDEM uygulaması seçilmedi: 'Dahil' ya da 'Hariç' seçin.");
  } else if (included) {
    const y = prov?.yekdem;
    if (yekdemMissing) {
      reasons.push("YEKDEM birim bedeli bilinmiyor. Piyasa Fiyatları'ndan girin ya da 'Hariç' seçin.");
    } else if (input.valuesEditedByUser || !prov) {
      // Gerekçe PTF satırında zaten bildirildi (elle değişiklik / kaynak yok).
    } else if (!y) {
      reasons.push('YEKDEM kaynağı bilinmiyor.');
    } else if ((prov.version ?? 0) >= 3) {
      if (!input.yekdemSegment) {
        reasons.push('YEKDEM segmenti seçilmedi: Serbest Tüketici ya da GTŞ-K1\'i açıkça seçin (değerler eşit olsa bile).');
      } else if (y.status === 'segment_missing') {
        // Doğrulama segment seçiminden önce üretildi (ör. AI hesabı); kayıt anında sunucu
        // seçilen segmentin onaylı değerini denetler. Burada ayrıca engel üretilmez.
      } else if (y.status === 'approval_missing') {
        reasons.push('Seçilen segmentte onaylı YEKDEM yok (henüz yayımlanıp onaylanmadı). Teklif taslak kalır.');
      } else if (!(y.system_verified === true && ayni(input.yekdem, y.value))) {
        reasons.push('YEKDEM, seçilen segmentin onaylı değeriyle eşleşmiyor.');
      }
    } else if (!(y.period_verified === true && ayni(input.yekdem, y.period_value))) {
      if (input.yekdem === 0 && y.period_status === 'zero_unverified') {
        reasons.push("Kayıtlı YEKDEM 0'ın açık ve kesin giriş kaydı yok (anlamı bilinmeyen sıfır); kesin teklifte kullanılamaz.");
      } else if (y.period_status === 'known' && y.period_trusted === true
          && y.period_record_status !== 'final' && ayni(input.yekdem, y.period_value)) {
        provisional = true;
        reasons.push('YEKDEM dönem kaydı kesinleşmemiş (provisional). Yalnız taslak hesapta kullanılabilir.');
      } else {
        reasons.push('YEKDEM kaynağı doğrulanmadı: dönemin kesin kaydıyla eşleşmiyor.');
      }
    }
  }

  const ready = reasons.length === 0;
  return { ready, draft: !ready, provisional, ptfMissing, yekdemMissing, modeRequired, reasons };
}

/** Fiyat kaynağının kısa Türkçe etiketi (panel ve sonuç rozeti). */
export function priceSourceLabel(source: string | null | undefined): string {
  if (!source) return 'kaynak yok';
  if (source === 'monthly_arithmetic:mcp_avg') return 'Aylık aritmetik PTF (EPİAŞ, yetkili onaylı)';
  if (source === 'manual_override') return 'manuel kayıt';
  if (source === 'reference_scalar') return 'aylık referans kaydı';
  if (source.startsWith('hourly_consumption')) return 'gerçek tüketim ağırlıklı';
  if (source.startsWith('hourly_weighted')) return 'saatlik ağırlıklı (profil)';
  if (source === 'override') return 'kullanıcı değeri';
  if (source === 'user_entered') return 'kullanıcı girişi';
  if (source === 'not_found') return 'PTF yok';
  return source;
}
