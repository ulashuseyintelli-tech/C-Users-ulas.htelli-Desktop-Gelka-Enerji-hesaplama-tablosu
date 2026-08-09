// S2 — backend'den gelen naive-UTC ISO string'lerini (owner kararı: DB
// istisnasız naive UTC, "Türkiye local-time stringlerini DB'ye rastgele
// yazma") Türkiye yerel saatinde GÖSTERMEK için ortak yardımcılar.
//
// KRİTİK: tarayıcının KENDİ sistem saat dilimine GÜVENİLMEZ — explicit
// `timeZone: 'Europe/Istanbul'` verilir. Aksi halde (örn. sunucu/CI/farklı
// bölge ayarlı bir makine) gösterilen saat backend'in ZoneInfo("Europe/
// Istanbul") ile hesapladığı "bugün" sınırından SAPAR. Bu, "Bugün sınırı
// kullanıcının yerel günüyle hesaplanmalı" ilkesinin gösterim tarafındaki
// simetriğidir (bkz. backend/app/crm/service.py _today_utc_bounds_tr).
//
// Çağrıldığı yerler:
// - ActivityTimeline.tsx, TaskList.tsx, TodayScreen.tsx (occurred_at/
//   due_at/completed_at gösterimi) [S2 WB-6/WB-7]

function toExplicitUtcIso(isoNaiveUtc: string): string {
  return isoNaiveUtc.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(isoNaiveUtc) ? isoNaiveUtc : `${isoNaiveUtc}Z`;
}

export function formatDateTimeTr(isoNaiveUtc: string | null): string {
  if (!isoNaiveUtc) return '—';
  try {
    return new Date(toExplicitUtcIso(isoNaiveUtc)).toLocaleString('tr-TR', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'Europe/Istanbul',
    });
  } catch {
    return '—';
  }
}

export function formatDateTr(isoNaiveUtc: string | null): string {
  if (!isoNaiveUtc) return '—';
  try {
    return new Date(toExplicitUtcIso(isoNaiveUtc)).toLocaleDateString('tr-TR', {
      timeZone: 'Europe/Istanbul',
    });
  } catch {
    return '—';
  }
}
