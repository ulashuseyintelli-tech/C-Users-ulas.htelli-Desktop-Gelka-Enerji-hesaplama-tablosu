// =============================================================================
// S5 — Outreach — Prospect Detay içinde "Tanışma E-postası" paneli.
// =============================================================================
//
// Owner'ın WB7 talebi: kullanıcı TEK BAKIŞTA Alıcı/Contact Type/Recipient
// Legal Type/Kaynak URL/KVKK/İYS/Suppression durumunu ve CAN SEND veya
// BLOCKED + TAM (genel değil) nedenleri görmeli. Taslak→Düzenle→Onayla→Gönder
// akışı — Onayla HİÇBİR KOŞULDA otomatik Gönder tetiklemez (iki ayrı buton,
// iki ayrı kullanıcı tıklaması).
//
// HARD GATE hatırlatması: buradaki can_send/BLOCKED gösterimi YALNIZ backend'in
// SON compliance_snapshot_json'unun bir YANSIMASIDIR — gerçek karar HER ZAMAN
// backend'de (approve/send anında taze) verilir; bu component hiçbir şeyi
// bypass ETMEZ, yalnız gösterir.
import { useCallback, useEffect, useState } from 'react';
import {
  Loader2, AlertCircle, CheckCircle2, XCircle, Send, Edit3, RefreshCw,
  ShieldAlert, ShieldCheck, ExternalLink, ClipboardList, Ban,
} from 'lucide-react';
import {
  createDraftMessage,
  listOutreachMessages,
  refreshOutreachCompliance,
  finalizeDraftMessage,
  approveOutreachMessage,
  sendOutreachMessage,
  createFollowUpTask,
  createSuppression,
  listSuppressions,
  OutreachMessageOut,
  OutreachMessageStatus,
  SuppressionEntryOut,
  SuppressionReason,
} from './outreachApi';
import { listProspectContacts, ProspectContactOut } from './prospectingApi';

const STATUS_LABELS: Record<OutreachMessageStatus, string> = {
  DRAFT: 'Taslak', READY_FOR_REVIEW: 'İncelemeye Hazır', APPROVED: 'Onaylandı', SENDING: 'Gönderiliyor',
  SENT: 'Gönderildi', FAILED: 'Başarısız', BOUNCED: 'Geri Döndü', REPLIED: 'Yanıtlandı',
  SUPPRESSED: 'Engellendi', CANCELLED: 'İptal Edildi',
};
const STATUS_COLORS: Record<OutreachMessageStatus, string> = {
  DRAFT: 'bg-gray-100 text-gray-600', READY_FOR_REVIEW: 'bg-blue-100 text-blue-700', APPROVED: 'bg-amber-100 text-amber-700',
  SENDING: 'bg-blue-100 text-blue-700', SENT: 'bg-green-100 text-green-700', FAILED: 'bg-red-100 text-red-700',
  BOUNCED: 'bg-red-100 text-red-700', REPLIED: 'bg-primary-100 text-primary-700', SUPPRESSED: 'bg-red-100 text-red-700',
  CANCELLED: 'bg-gray-100 text-gray-500',
};
const RECIPIENT_LEGAL_TYPE_LABELS: Record<string, string> = { TACIR: 'Tacir', ESNAF: 'Esnaf', BIREYSEL: 'Bireysel', UNKNOWN: 'Bilinmiyor (doğrulanmamış)' };
const CONTACT_TYPE_LABELS: Record<string, string> = {
  GENERAL_CORPORATE: 'Genel kurumsal', DEPARTMENT: 'Departman', NAMED_CORPORATE_PERSON: 'Kişiye özel',
  PERSONAL_OR_FREE_MAIL: 'Kişisel/ücretsiz e-posta', OTHER: 'Diğer',
};
const KVKK_STATUS_LABELS: Record<string, string> = { OK: 'Uygun', REVIEW_REQUIRED: 'İnceleme Gerekli', OPT_IN_REQUIRED: 'Onay Gerekli', NOT_APPLICABLE: 'Kapsam Dışı' };
const SUPPRESSION_STATUS_LABELS: Record<string, string> = { CLEAR: 'Temiz', SUPPRESSED: 'Engellenmiş', UNKNOWN: 'Bilinmiyor' };
const SOURCE_STATUS_LABELS: Record<string, string> = { EVIDENCED: 'Kanıtlanmış', MISSING: 'Eksik', NOT_APPLICABLE: 'Kapsam Dışı' };
const IYS_STATUS_LABELS: Record<string, string> = {
  IYS_UNKNOWN: 'Bilinmiyor', IYS_VERIFIED: 'Doğrulandı', IYS_BLOCKED: 'Engellendi',
  IYS_NOT_REQUIRED_OR_SPECIAL_CASE: 'Gerekmiyor / Özel Durum', IYS_NOT_APPLICABLE_TEST_RECIPIENT: 'Kapsam Dışı (Test Adresi)',
};
const REASON_CODE_LABELS: Record<string, string> = {
  EMAIL_MISSING: 'E-posta adresi eksik',
  EMAIL_INVALID_SYNTAX: 'E-posta sözdizimi geçersiz',
  RECIPIENT_CONTEXT_MISSING: 'Alıcı bağlamı eksik',
  CONTACT_NOT_FOUND: 'İletişim kaydı bulunamadı',
  CUSTOMER_NOT_FOUND: 'Müşteri kaydı bulunamadı',
  PROSPECT_COMPANY_NOT_FOUND: 'Prospect şirket kaydı bulunamadı',
  SUPPRESSED: 'Bu adres engellenmiş (suppression listesinde)',
  SOURCE_EVIDENCE_MISSING: 'Kaynak kanıtı eksik',
  KVKK_REVIEW_REQUIRED: 'KVKK — manuel inceleme gerekli (kişiye özel adres)',
  KVKK_OPT_IN_REQUIRED: 'KVKK — önceden onay gerekli (muhtemel bireysel adres)',
  CONTACT_TYPE_UNRESOLVED: 'İletişim türü sınıflandırılamadı',
  RECIPIENT_LEGAL_TYPE_UNVERIFIED: 'Alıcının hukuki statüsü (tacir/esnaf) doğrulanmamış',
  IYS_STATUS_UNKNOWN: 'İYS durumu bilinmiyor',
  IYS_STATUS_BLOCKED: 'İYS tarafından engellenmiş',
};
const SUPPRESSION_REASON_LABELS: Record<SuppressionReason, string> = {
  USER_REJECTED: 'Kullanıcı reddetti', DO_NOT_CONTACT: 'İletişim kurulmasın', PERMANENT_BOUNCE: 'Kalıcı geri dönüş (bounce)',
  LEGAL_BLOCK: 'Yasal engel', MANUAL_BLOCK: 'Manuel engelleme',
};

function formatDateTimeTr(iso: string | null): string {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString('tr-TR'); } catch { return '—'; }
}

/** Owner'ın örnek formatı: "BLOCKED\n- REASON_1\n- REASON_2" — tek genel mesaj DEĞİL. */
function ComplianceBanner({ m }: { m: OutreachMessageOut }) {
  const c = m.compliance_snapshot_json;
  if (!c) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 bg-gray-50 border border-gray-200 rounded px-3 py-2">
        <AlertCircle className="w-4 h-4" /> Uygunluk durumu henüz değerlendirilmedi.
      </div>
    );
  }
  if (c.can_send) {
    return (
      <div className="flex items-center gap-2 text-sm font-medium text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">
        <ShieldCheck className="w-4 h-4 flex-shrink-0" /> GÖNDERİLEBİLİR
      </div>
    );
  }
  return (
    <div className="text-sm bg-red-50 border border-red-200 rounded px-3 py-2 space-y-1">
      <div className="flex items-center gap-2 font-medium text-red-700">
        <ShieldAlert className="w-4 h-4 flex-shrink-0" /> ENGELLENDİ
      </div>
      <ul className="list-disc list-inside text-red-700 space-y-0.5">
        {c.reason_codes.map((code) => (
          <li key={code}>
            <span className="font-mono text-xs">{code}</span> — {REASON_CODE_LABELS[code] || 'Bilinmeyen neden'}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ComplianceDetailGrid({ m }: { m: OutreachMessageOut }) {
  const c = m.compliance_snapshot_json;
  const s = m.source_snapshot_json;
  const row = (label: string, value: string) => (
    <div className="flex justify-between gap-3 py-1 border-b border-gray-50 last:border-0">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-900 font-medium text-right">{value}</span>
    </div>
  );
  return (
    <div className="text-sm space-y-0.5">
      {row('Alıcı', m.recipient_email_snapshot)}
      {row('Contact Type', c?.contact_type ? (CONTACT_TYPE_LABELS[c.contact_type] || c.contact_type) : '—')}
      {row('Recipient Legal Type', RECIPIENT_LEGAL_TYPE_LABELS[m.recipient_legal_type || 'UNKNOWN'])}
      {row('KVKK durumu', c ? (KVKK_STATUS_LABELS[c.kvkk_status] || c.kvkk_status) : '—')}
      {row('İYS durumu', c ? (IYS_STATUS_LABELS[c.iys_status] || c.iys_status) : '—')}
      {row('Suppression durumu', c ? (SUPPRESSION_STATUS_LABELS[c.suppression_status] || c.suppression_status) : '—')}
      {row('Kaynak kanıtı', c ? (SOURCE_STATUS_LABELS[c.source_status] || c.source_status) : '—')}
      <div className="py-1">
        <div className="text-gray-500 mb-1">Kaynak URL</div>
        {s?.source_urls && s.source_urls.length > 0 ? (
          <div className="space-y-0.5">
            {s.source_urls.map((url) => (
              <a key={url} href={url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-primary-600 hover:underline text-xs truncate">
                {url} <ExternalLink className="w-3 h-3 flex-shrink-0" />
              </a>
            ))}
          </div>
        ) : (
          <span className="text-gray-400 text-xs">Kayıtlı kaynak yok</span>
        )}
      </div>
    </div>
  );
}

interface OutreachPanelProps {
  prospectId: number;
}

export function OutreachPanel({ prospectId }: OutreachPanelProps) {
  const [messages, setMessages] = useState<OutreachMessageOut[] | null>(null);
  const [contacts, setContacts] = useState<ProspectContactOut[]>([]);
  const [selectedContactId, setSelectedContactId] = useState<number | null>(null);
  const [useAi, setUseAi] = useState(false);
  const [busyId, setBusyId] = useState<number | 'new' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editSubject, setEditSubject] = useState('');
  const [editBody, setEditBody] = useState('');
  const [showSuppressions, setShowSuppressions] = useState(false);
  const [suppressions, setSuppressions] = useState<SuppressionEntryOut[] | null>(null);
  const [suppressEmail, setSuppressEmail] = useState('');
  const [suppressReason, setSuppressReason] = useState<SuppressionReason>('USER_REJECTED');

  const load = useCallback(async () => {
    const [msgs, cts] = await Promise.all([
      listOutreachMessages({ prospect_company_id: prospectId }),
      listProspectContacts(prospectId).catch(() => []),
    ]);
    setMessages(msgs);
    const withEmail = cts.filter((c) => c.email);
    setContacts(withEmail);
    setSelectedContactId((prev) => (prev && withEmail.some((c) => c.id === prev) ? prev : withEmail[0]?.id ?? null));
  }, [prospectId]);

  useEffect(() => {
    setMessages(null);
    setEditingId(null);
    setError(null);
    setInfo(null);
    load();
  }, [prospectId, load]);

  const run = async (id: number | 'new', fn: () => Promise<any>, successMsg?: string) => {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      if (successMsg) setInfo(successMsg);
      await load();
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || JSON.stringify(detail) || err?.message || 'İşlem başarısız oldu.';
      setError(msg);
    } finally {
      setBusyId(null);
    }
  };

  const handleCreateDraft = () => {
    if (!selectedContactId) return;
    run('new', () => createDraftMessage({ contact_id: selectedContactId, use_ai: useAi }), 'Taslak oluşturuldu.');
  };

  const startEdit = (m: OutreachMessageOut) => {
    setEditingId(m.id);
    setEditSubject(m.subject);
    setEditBody(m.body_snapshot);
    setInfo(null);
    setError(null);
  };

  const submitEdit = () => {
    if (editingId === null) return;
    run(editingId, () => finalizeDraftMessage(editingId, { subject: editSubject, editable_body: editBody }), 'Taslak tamamlandı — incelemeye hazır.')
      .then(() => setEditingId(null));
  };

  const loadSuppressions = () => {
    setShowSuppressions((v) => !v);
    if (!suppressions) {
      listSuppressions().then(setSuppressions).catch(() => setSuppressions([]));
    }
  };

  const handleAddSuppression = () => {
    if (!suppressEmail.trim()) return;
    run('new', async () => {
      await createSuppression({ email: suppressEmail.trim(), reason: suppressReason });
      setSuppressEmail('');
      const rows = await listSuppressions();
      setSuppressions(rows);
    }, 'Adres engellendi.');
  };

  if (messages === null) {
    return (
      <div className="p-8 text-center text-gray-500">
        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" /> Yükleniyor...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-700 whitespace-pre-wrap">{error}</p>
        </div>
      )}
      {info && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-700">{info}</div>
      )}

      {/* Yeni taslak oluşturma */}
      <div className="card p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-900">Yeni Tanışma E-postası Taslağı</h3>
        {contacts.length === 0 ? (
          <p className="text-sm text-gray-500">Bu prospect'e ait e-postalı bir iletişim kaydı yok — önce "İletişimler" sekmesinden bir kayıt ekleyin veya web sitesinden zenginleştirin.</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={selectedContactId ?? ''}
                onChange={(e) => setSelectedContactId(Number(e.target.value))}
                className="input text-sm max-w-xs"
              >
                {contacts.map((c) => (
                  <option key={c.id} value={c.id}>{c.email} {c.full_name ? `(${c.full_name})` : ''}</option>
                ))}
              </select>
              <label className="flex items-center gap-1.5 text-sm text-gray-600">
                <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
                AI ile taslak öner (yalnız editable metin — yasal footer her zaman sabit)
              </label>
              <button disabled={busyId !== null} onClick={handleCreateDraft} className="btn-primary text-sm flex items-center gap-1.5">
                {busyId === 'new' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Edit3 className="w-4 h-4" />} Taslak Oluştur
              </button>
            </div>
          </>
        )}
      </div>

      {/* Geçmiş / mevcut mesajlar */}
      {messages.length === 0 ? (
        <div className="card p-8 text-center text-gray-500">Henüz bir tanışma e-postası taslağı oluşturulmadı.</div>
      ) : (
        <div className="space-y-3">
          {messages.map((m) => (
            <div key={m.id} className="card p-4 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium text-gray-900 text-sm">{m.subject}</div>
                  <div className="text-xs text-gray-500">Oluşturuldu: {formatDateTimeTr(m.created_at)}</div>
                </div>
                <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium flex-shrink-0 ${STATUS_COLORS[m.status]}`}>
                  {STATUS_LABELS[m.status]}
                </span>
              </div>

              <ComplianceBanner m={m} />
              <ComplianceDetailGrid m={m} />

              {editingId === m.id ? (
                <div className="border-t border-gray-100 pt-3 space-y-2">
                  <label className="text-xs font-medium text-gray-500">Konu</label>
                  <input value={editSubject} onChange={(e) => setEditSubject(e.target.value)} className="input w-full text-sm" />
                  <label className="text-xs font-medium text-gray-500">Gövde (editable — selamlama/tanıtım/görüşme talebi)</label>
                  <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} className="input w-full text-sm" rows={6} />
                  <div>
                    <label className="text-xs font-medium text-gray-500">Yasal footer (sabit — Gelka gönderici profili, düzenlenemez)</label>
                    <pre className="text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded p-2 whitespace-pre-wrap">{m.system_footer_snapshot}</pre>
                  </div>
                  <div className="flex gap-2">
                    <button disabled={busyId === m.id} onClick={submitEdit} className="btn-primary text-sm">
                      {busyId === m.id ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Tamamla — İncelemeye Gönder'}
                    </button>
                    <button onClick={() => setEditingId(null)} className="btn-secondary text-sm">Vazgeç</button>
                  </div>
                </div>
              ) : (
                <div className="border-t border-gray-100 pt-3 flex flex-wrap gap-2">
                  <button
                    disabled={busyId !== null}
                    onClick={() => run(m.id, () => refreshOutreachCompliance(m.id))}
                    className="btn-secondary text-xs flex items-center gap-1.5"
                  >
                    {busyId === m.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Uygunluğu Yenile
                  </button>

                  {m.status === 'DRAFT' && (
                    <button disabled={busyId !== null} onClick={() => startEdit(m)} className="btn-secondary text-xs flex items-center gap-1.5">
                      <Edit3 className="w-3.5 h-3.5" /> Düzenle
                    </button>
                  )}

                  {m.status === 'READY_FOR_REVIEW' && (
                    <button
                      disabled={busyId !== null}
                      onClick={() => run(m.id, () => approveOutreachMessage(m.id), 'Onaylandı — gönderim için hazır (Gönder AYRI bir adımdır).')}
                      className="btn-primary text-xs flex items-center gap-1.5"
                    >
                      {busyId === m.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />} Onayla
                    </button>
                  )}

                  {m.status === 'APPROVED' && (
                    <button
                      disabled={busyId !== null}
                      onClick={() => run(m.id, () => sendOutreachMessage(m.id), 'Gönderim tetiklendi.')}
                      className="text-xs flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                      title="Gerçek SMTP gönderimini tetikler — backend compliance'ı TAZE yeniden değerlendirir."
                    >
                      {busyId === m.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />} Gönder
                    </button>
                  )}

                  {m.status === 'SENT' && (
                    <button
                      disabled={busyId !== null}
                      onClick={() => run(m.id, () => createFollowUpTask(m.id, 3), 'Takip görevi oluşturuldu (3 gün sonrası için).')}
                      className="btn-secondary text-xs flex items-center gap-1.5"
                    >
                      <ClipboardList className="w-3.5 h-3.5" /> Takip Görevi Oluştur (opsiyonel)
                    </button>
                  )}

                  {m.status === 'FAILED' && m.failure_code && (
                    <span className="text-xs text-red-600">Gönderim hatası: <span className="font-mono">{m.failure_code}</span></span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Suppression yönetimi */}
      <div className="card p-4 space-y-3">
        <button onClick={loadSuppressions} className="text-sm font-semibold text-gray-900 flex items-center gap-2">
          <Ban className="w-4 h-4" /> Engellenen Adresler (Suppression) {showSuppressions ? '▾' : '▸'}
        </button>
        {showSuppressions && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={suppressEmail}
                onChange={(e) => setSuppressEmail(e.target.value)}
                placeholder="ornek@sirket.com"
                className="input text-sm max-w-xs"
              />
              <select value={suppressReason} onChange={(e) => setSuppressReason(e.target.value as SuppressionReason)} className="input text-sm">
                {(Object.keys(SUPPRESSION_REASON_LABELS) as SuppressionReason[]).map((r) => (
                  <option key={r} value={r}>{SUPPRESSION_REASON_LABELS[r]}</option>
                ))}
              </select>
              <button disabled={busyId !== null || !suppressEmail.trim()} onClick={handleAddSuppression} className="btn-secondary text-sm flex items-center gap-1.5">
                <XCircle className="w-4 h-4" /> Engelle
              </button>
            </div>
            {suppressions === null ? (
              <div className="text-sm text-gray-500">Yükleniyor...</div>
            ) : suppressions.length === 0 ? (
              <div className="text-sm text-gray-400">Engellenen adres yok.</div>
            ) : (
              <div className="space-y-1">
                {suppressions.map((s) => (
                  <div key={s.id} className="text-xs flex items-center justify-between bg-gray-50 rounded px-2 py-1">
                    <span>{s.email_normalized}</span>
                    <span className="text-gray-500">{SUPPRESSION_REASON_LABELS[s.reason as SuppressionReason] || s.reason} · {formatDateTimeTr(s.created_at)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
