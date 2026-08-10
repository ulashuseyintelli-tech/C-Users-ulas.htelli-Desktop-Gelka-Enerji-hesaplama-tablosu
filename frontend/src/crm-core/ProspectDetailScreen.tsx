// =============================================================================
// S4 — Prospecting — Prospect Detay: Genel Bilgiler / İletişimler / Kaynaklar.
// =============================================================================
//
// CustomerDetailScreen.tsx (S1) ile AYNI iskelet deseni (üst kart + border-b-2
// alt-sekme nav) — ama Aktiviteler/Görevler alt-sekmeleri BİLİNÇLİ OLARAK
// YOK (owner: S4 V1'de mandatory değil, S2'ye entegrasyon DEFERRED).
//
// Aksiyonlar status'e göre koşullu render edilir (owner: HUMAN-IN-THE-LOOP —
// verify/qualify/disqualify/convert HER ZAMAN açık kullanıcı aksiyonu,
// hiçbiri otomatik tetiklenmez). CONVERTED terminal'dir — hiçbir aksiyon
// gösterilmez, yalnız bağlı Customer'a deep-link (onOpenSubject reuse,
// S3'teki PipelineCard.onOpenOffer/onOpenContract ile AYNI mekanizma).
import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Loader2, AlertCircle, ExternalLink, RefreshCw, CheckCircle2, XCircle, UserCheck, Mail, Phone } from 'lucide-react';
import {
  getProspect,
  listProspectContacts,
  listProspectSources,
  verifyProspect,
  qualifyProspect,
  disqualifyProspect,
  enrichProspect,
  convertProspect,
  ProspectCompanyOut,
  ProspectContactOut,
  ProspectSourceOut,
  QualificationReason,
  CustomerMatchOut,
} from './prospectingApi';
import type { Subject } from './crmApi';

type DetailTab = 'general' | 'contacts' | 'sources';

const STATUS_LABELS: Record<string, string> = {
  DISCOVERED: 'Keşfedildi', VERIFIED: 'Doğrulandı', QUALIFIED: 'Uygun', DISQUALIFIED: 'Uygun Değil', CONVERTED: 'Müşteriye Dönüştürüldü',
};
const STATUS_COLORS: Record<string, string> = {
  DISCOVERED: 'bg-gray-100 text-gray-600', VERIFIED: 'bg-blue-100 text-blue-700', QUALIFIED: 'bg-green-100 text-green-700',
  DISQUALIFIED: 'bg-red-100 text-red-700', CONVERTED: 'bg-primary-100 text-primary-700',
};
const REASON_LABELS: Record<QualificationReason, string> = {
  sector_fit: 'Sektör uygunluğu', location_fit: 'Konum uygunluğu', has_corporate_contact: 'Kurumsal iletişim mevcut',
  energy_intensive_signal: 'Enerji-yoğun sinyal', too_small_unsuitable: 'Çok küçük / uygun değil', duplicate: 'Mükerrer', other: 'Diğer',
};
const CONTACT_TYPE_LABELS: Record<string, string> = {
  GENERAL_CORPORATE: 'Genel kurumsal', DEPARTMENT: 'Departman', NAMED_CORPORATE_PERSON: 'Kişiye özel',
  PERSONAL_OR_FREE_MAIL: 'Kişisel/ücretsiz e-posta', OTHER: 'Diğer',
};
const FETCH_STATUS_LABELS: Record<string, string> = {
  PENDING: 'Bekliyor', OK: 'Başarılı', FAILED: 'Başarısız', BLOCKED_SSRF: 'Güvenlik nedeniyle engellendi',
  TIMEOUT: 'Zaman aşımı', TOO_LARGE: 'Çok büyük', UNSUPPORTED_CONTENT_TYPE: 'Desteklenmeyen içerik türü',
};

function formatDateTimeTr(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('tr-TR');
  } catch {
    return '—';
  }
}

interface ProspectDetailScreenProps {
  prospectId: number;
  onBack: () => void;
  onOpenSubject: (subject: Subject) => void;
}

export function ProspectDetailScreen({ prospectId, onBack, onOpenSubject }: ProspectDetailScreenProps) {
  const [tab, setTab] = useState<DetailTab>('general');
  const [prospect, setProspect] = useState<ProspectCompanyOut | null>(null);
  const [contacts, setContacts] = useState<ProspectContactOut[] | null>(null);
  const [sources, setSources] = useState<ProspectSourceOut[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [qualifyMode, setQualifyMode] = useState<'qualify' | 'disqualify' | null>(null);
  const [reason, setReason] = useState<QualificationReason>('sector_fit');
  const [note, setNote] = useState('');
  const [convertMatches, setConvertMatches] = useState<CustomerMatchOut[] | null>(null);
  const [convertWithTask, setConvertWithTask] = useState(false);
  const [taskTitle, setTaskTitle] = useState('İlk görüşme / teklif hazırlığı');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getProspect(prospectId);
      setProspect(data);
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Prospect yüklenemedi.');
    } finally {
      setLoading(false);
    }
  }, [prospectId]);

  useEffect(() => {
    setTab('general');
    setConvertMatches(null);
    setActionMessage(null);
    load();
  }, [prospectId, load]);

  useEffect(() => {
    if (tab === 'contacts' && contacts === null) {
      listProspectContacts(prospectId).then(setContacts).catch(() => setContacts([]));
    }
    if (tab === 'sources' && sources === null) {
      listProspectSources(prospectId).then(setSources).catch(() => setSources([]));
    }
  }, [tab, prospectId, contacts, sources]);

  const refreshAll = async () => {
    setContacts(null);
    setSources(null);
    await load();
  };

  const runAction = async (fn: () => Promise<any>, successMessage: string) => {
    setActionBusy(true);
    setActionMessage(null);
    setError(null);
    try {
      await fn();
      setActionMessage(successMessage);
      await refreshAll();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'İşlem başarısız oldu.');
    } finally {
      setActionBusy(false);
    }
  };

  const handleQualifySubmit = () => {
    if (qualifyMode === 'qualify') {
      runAction(() => qualifyProspect(prospectId, reason, note || undefined), 'Prospect "Uygun" olarak işaretlendi.');
    } else if (qualifyMode === 'disqualify') {
      runAction(() => disqualifyProspect(prospectId, reason, note || undefined), 'Prospect "Uygun Değil" olarak işaretlendi.');
    }
    setQualifyMode(null);
    setNote('');
  };

  const handleEnrich = () => {
    runAction(async () => {
      const result = await enrichProspect(prospectId);
      setActionMessage(
        `${result.pages_fetched} sayfa denendi — ${result.new_contacts.length} yeni iletişim, ${result.new_sources.length} yeni kaynak eklendi.`
      );
    }, '');
  };

  const handleConvert = async (options?: { existing_customer_id?: number; force_create_new_customer?: boolean }) => {
    setActionBusy(true);
    setError(null);
    try {
      const result = await convertProspect(prospectId, {
        ...options,
        create_activity: true,
        create_first_task: convertWithTask,
        first_task_title: convertWithTask ? taskTitle : undefined,
      });
      if (result.status === 'confirmation_required') {
        setConvertMatches(result.potential_matches);
      } else {
        setConvertMatches(null);
        setActionMessage(
          `Müşteriye dönüştürüldü (Customer #${result.customer_id}).` +
          (result.warnings.length > 0 ? ` Uyarı: ${result.warnings.join(' ')}` : '')
        );
        await refreshAll();
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Dönüştürme başarısız oldu.');
    } finally {
      setActionBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="p-8 text-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
        Yükleniyor...
      </div>
    );
  }

  if (error && !prospect) {
    return (
      <div className="space-y-4">
        <button onClick={onBack} className="btn-secondary flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" /> Prospectler listesine dön
        </button>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      </div>
    );
  }

  if (!prospect) return null;

  const displayName = prospect.trade_name || prospect.legal_name || 'İsimsiz Prospect';
  const isConverted = prospect.status === 'CONVERTED';

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="btn-secondary flex items-center gap-2">
        <ArrowLeft className="w-4 h-4" /> Prospectler listesine dön
      </button>

      <div className="card p-4 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-gray-900">{displayName}</h2>
            {prospect.website && (
              <a href={prospect.website} target="_blank" rel="noopener noreferrer" className="text-sm text-primary-600 hover:underline inline-flex items-center gap-1">
                {prospect.normalized_domain || prospect.website} <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
          <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_COLORS[prospect.status]}`}>
            {STATUS_LABELS[prospect.status]}
          </span>
        </div>
        <div className="text-sm text-gray-500 flex flex-wrap gap-x-4">
          {prospect.sector && <span>Sektör: {prospect.sector}</span>}
          {prospect.city && <span>Şehir: {prospect.city}{prospect.district ? ` / ${prospect.district}` : ''}</span>}
          <span>{prospect.contact_count} iletişim</span>
          <span>{prospect.source_count} kaynak</span>
        </div>
        {prospect.qualification_reason && (
          <div className="text-sm text-gray-600">
            Gerekçe: {REASON_LABELS[prospect.qualification_reason]}
            {prospect.qualification_note && ` — ${prospect.qualification_note}`}
          </div>
        )}
        {isConverted && prospect.customer_id && (
          <button
            onClick={() => onOpenSubject({ customer_id: prospect.customer_id! })}
            className="text-sm text-primary-600 hover:underline inline-flex items-center gap-1 w-fit"
          >
            <UserCheck className="w-4 h-4" /> Müşteri kaydını aç
          </button>
        )}
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}
      {actionMessage && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-700">{actionMessage}</div>
      )}

      {!isConverted && (
        <div className="card p-4 space-y-3">
          <div className="flex flex-wrap gap-2">
            {prospect.status === 'DISCOVERED' && (
              <button disabled={actionBusy} onClick={() => runAction(() => verifyProspect(prospectId), 'Prospect doğrulandı.')} className="btn-secondary text-sm flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" /> Doğrula
              </button>
            )}
            {prospect.status !== 'DISCOVERED' && prospect.status !== 'QUALIFIED' && (
              <button disabled={actionBusy} onClick={() => { setQualifyMode('qualify'); setReason('sector_fit'); }} className="btn-secondary text-sm flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4 text-green-600" /> Uygun İşaretle
              </button>
            )}
            {prospect.status !== 'DISQUALIFIED' && prospect.status !== 'DISCOVERED' && (
              <button disabled={actionBusy} onClick={() => { setQualifyMode('disqualify'); setReason('too_small_unsuitable'); }} className="btn-secondary text-sm flex items-center gap-1.5">
                <XCircle className="w-4 h-4 text-red-600" /> Uygun Değil
              </button>
            )}
            {prospect.website && (
              <button disabled={actionBusy} onClick={handleEnrich} className="btn-secondary text-sm flex items-center gap-1.5">
                {actionBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Web Sitesinden Zenginleştir
              </button>
            )}
            {prospect.status === 'QUALIFIED' && (
              <button disabled={actionBusy} onClick={() => handleConvert()} className="btn-primary text-sm flex items-center gap-1.5">
                <UserCheck className="w-4 h-4" /> Müşteriye Dönüştür
              </button>
            )}
          </div>

          {qualifyMode && (
            <div className="border-t border-gray-100 pt-3 space-y-2">
              <label className="text-sm font-medium text-gray-700">Gerekçe</label>
              <select value={reason} onChange={(e) => setReason(e.target.value as QualificationReason)} className="input w-full max-w-sm">
                {(Object.keys(REASON_LABELS) as QualificationReason[]).map((r) => (
                  <option key={r} value={r}>{REASON_LABELS[r]}</option>
                ))}
              </select>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Not (opsiyonel)" className="input w-full max-w-sm" rows={2} />
              <div className="flex gap-2">
                <button onClick={handleQualifySubmit} className="btn-primary text-sm">Onayla</button>
                <button onClick={() => setQualifyMode(null)} className="btn-secondary text-sm">Vazgeç</button>
              </div>
            </div>
          )}

          {prospect.status === 'QUALIFIED' && !convertMatches && (
            <div className="border-t border-gray-100 pt-3 flex items-center gap-2 text-sm text-gray-600">
              <input type="checkbox" checked={convertWithTask} onChange={(e) => setConvertWithTask(e.target.checked)} id="convert-task" />
              <label htmlFor="convert-task">Dönüştürünce ilk görevi de oluştur:</label>
              {convertWithTask && (
                <input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} className="input text-sm flex-1 max-w-xs" />
              )}
            </div>
          )}

          {convertMatches && (
            <div className="border-t border-gray-100 pt-3 space-y-2">
              <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
                ⚠ Mevcut müşteri kayıtlarında olası eşleşme(ler) bulundu — mükerrer Customer oluşturmamak için seçim yapın.
              </p>
              {convertMatches.map((m) => (
                <div key={m.customer_id} className="flex items-center justify-between text-sm bg-gray-50 rounded px-3 py-2">
                  <span>{m.company || m.name} {m.email ? `(${m.email})` : ''}</span>
                  <button onClick={() => handleConvert({ existing_customer_id: m.customer_id })} className="btn-secondary text-xs">Bu müşteriye bağla</button>
                </div>
              ))}
              <button onClick={() => handleConvert({ force_create_new_customer: true })} className="btn-primary text-sm">Yine de Yeni Müşteri Oluştur</button>
            </div>
          )}
        </div>
      )}

      <div className="border-b border-gray-200">
        <nav className="flex gap-6">
          {(
            [
              ['general', 'Genel Bilgiler'],
              ['contacts', `İletişimler (${prospect.contact_count})`],
              ['sources', `Kaynaklar (${prospect.source_count})`],
            ] as [DetailTab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`py-3 px-2 border-b-2 font-medium text-sm transition-colors ${
                tab === key ? 'border-primary-600 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {label}
            </button>
          ))}
        </nav>
      </div>

      {tab === 'general' && (
        <div className="card p-4 space-y-2 text-sm">
          <div><span className="text-gray-500">Ticari unvan:</span> {prospect.trade_name || '—'}</div>
          <div><span className="text-gray-500">Tüzel unvan:</span> {prospect.legal_name || '—'}</div>
          <div><span className="text-gray-500">Telefon:</span> {prospect.phone || '—'}</div>
          <div><span className="text-gray-500">Adres:</span> {prospect.address || '—'}</div>
          <div><span className="text-gray-500">Sanayi bölgesi:</span> {prospect.industrial_zone || '—'}</div>
          <div><span className="text-gray-500">Keşif tarihi:</span> {formatDateTimeTr(prospect.discovered_at)}</div>
          <div><span className="text-gray-500">Son doğrulama:</span> {formatDateTimeTr(prospect.last_verified_at)}</div>
          {prospect.duplicate_of_id && (
            <div className="text-amber-700">Olası mükerrer — ilişkili kayıt: #{prospect.duplicate_of_id}</div>
          )}
        </div>
      )}

      {tab === 'contacts' && (
        <div className="space-y-2">
          {contacts === null ? (
            <div className="p-8 text-center text-gray-500"><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Yükleniyor...</div>
          ) : contacts.length === 0 ? (
            <div className="card p-8 text-center text-gray-500">Henüz iletişim bilgisi bulunamadı. "Web Sitesinden Zenginleştir" ile keşfedilebilir.</div>
          ) : (
            contacts.map((c) => (
              <div key={c.id} className="card p-3 flex items-center justify-between">
                <div>
                  <div className="text-sm font-medium text-gray-900 flex items-center gap-2">
                    {c.email ? <Mail className="w-4 h-4 text-gray-400" /> : <Phone className="w-4 h-4 text-gray-400" />}
                    {c.email || c.phone || '—'}
                  </div>
                  {(c.full_name || c.job_title) && (
                    <div className="text-xs text-gray-500">{[c.full_name, c.job_title].filter(Boolean).join(' — ')}</div>
                  )}
                </div>
                <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-gray-100 text-gray-600">
                  {CONTACT_TYPE_LABELS[c.contact_type] || c.contact_type}
                </span>
              </div>
            ))
          )}
        </div>
      )}

      {tab === 'sources' && (
        <div className="space-y-2">
          {sources === null ? (
            <div className="p-8 text-center text-gray-500"><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Yükleniyor...</div>
          ) : sources.length === 0 ? (
            <div className="card p-8 text-center text-gray-500">Henüz kaynak kaydı yok.</div>
          ) : (
            sources.map((s) => (
              <div key={s.id} className="card p-3 space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <a href={s.source_url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary-600 hover:underline inline-flex items-center gap-1 truncate">
                    {s.source_title || s.source_url} <ExternalLink className="w-3 h-3 flex-shrink-0" />
                  </a>
                  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium flex-shrink-0 ${s.fetch_status === 'OK' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
                    {FETCH_STATUS_LABELS[s.fetch_status] || s.fetch_status}
                  </span>
                </div>
                {s.evidence_text && <p className="text-xs text-gray-500 line-clamp-2">{s.evidence_text}</p>}
                <p className="text-xs text-gray-400">Son kontrol: {formatDateTimeTr(s.last_checked_at)}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
