import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Upload, CheckCircle2, AlertTriangle, Download } from 'lucide-react';
import {
  FieldCandidate,
  uploadContractDocument,
  startContractExtraction,
  getContractExtractionResult,
  detectContractConflicts,
  resolveContractCandidate,
  saveLegalProfile,
  saveRepresentative,
  createContractDraft,
  previewContract,
  finalizeContract,
  downloadContractPdf,
} from './contractsApi';

// =============================================================================
// Props / step tanımı
// =============================================================================

export interface ContractWizardModalProps {
  open: boolean;
  onClose: () => void;
  offerId: number;
  customerId?: number;
}

type WizardStep = 'upload' | 'processing' | 'review' | 'complete_fields' | 'preview' | 'done';

const STEP_LABELS: Record<WizardStep, string> = {
  upload: '1. Belgeler',
  processing: '2. İşleniyor',
  review: '3. Alan Onayı',
  complete_fields: '4. Sözleşme Bilgileri',
  preview: '5. Önizleme',
  done: '6. Tamamlandı',
};
const STEP_ORDER: WizardStep[] = ['upload', 'processing', 'review', 'complete_fields', 'preview', 'done'];

function StepIndicator({ current }: { current: WizardStep }) {
  const currentIdx = STEP_ORDER.indexOf(current);
  return (
    <div className="mb-6 flex items-center gap-1.5" data-testid="contract-wizard-step-indicator">
      {STEP_ORDER.map((step, idx) => {
        const isActive = idx === currentIdx;
        const isCompleted = idx < currentIdx;
        return (
          <React.Fragment key={step}>
            {idx > 0 && <div className={`h-0.5 flex-1 ${isCompleted ? 'bg-primary-500' : 'bg-gray-200'}`} />}
            <span
              className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium whitespace-nowrap ${
                isActive ? 'bg-primary-100 text-primary-700' : isCompleted ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
              }`}
            >
              {STEP_LABELS[step]}
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

// =============================================================================
// Alan → belge şeması eşlemesi (review adımı için)
// =============================================================================

const LEGAL_PROFILE_FIELDS = ['legal_name', 'tax_number', 'tax_office', 'mersis_number', 'trade_registry_number', 'registered_address', 'facility_address'];
const REPRESENTATIVE_FIELDS = ['representative_full_name', 'representative_national_id', 'authority_type', 'authority_scope', 'authority_start_date', 'authority_end_date', 'is_indefinite'];
const INFO_ONLY_FIELDS = ['activity_code', 'establishment_date', 'notary_name', 'notary_date', 'notary_document_number'];

const FIELD_LABELS: Record<string, string> = {
  legal_name: 'Unvan',
  tax_number: 'Vergi No',
  tax_office: 'Vergi Dairesi',
  mersis_number: 'MERSİS No',
  trade_registry_number: 'Ticaret Sicil No',
  registered_address: 'Kayıtlı Adres',
  facility_address: 'Tesis Adresi',
  representative_full_name: 'Yetkili Ad-Soyad',
  representative_national_id: 'T.C. Kimlik No',
  authority_type: 'Temsil Şekli',
  authority_scope: 'Yetki Kapsamı',
  authority_start_date: 'Yetki Başlangıç',
  authority_end_date: 'Yetki Bitiş',
  is_indefinite: 'Süresiz mi',
  activity_code: 'Faaliyet Kodu',
  establishment_date: 'Kuruluş Tarihi',
  notary_name: 'Noter',
  notary_date: 'Noter Tarihi',
  notary_document_number: 'Noter Belge No',
};

function effectiveValue(candidate: FieldCandidate): string | null {
  if (candidate.user_decision === 'corrected') return candidate.corrected_value;
  if (candidate.user_decision === 'accepted') return candidate.raw_value;
  return null;
}

// =============================================================================
// Component
// =============================================================================

export const ContractWizardModal: React.FC<ContractWizardModalProps> = ({ open, onClose, offerId, customerId }) => {
  const backdropRef = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState<WizardStep>('upload');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // upload
  const [taxCertFile, setTaxCertFile] = useState<File | null>(null);
  const [signatureCircularFile, setSignatureCircularFile] = useState<File | null>(null);

  // review
  const [candidates, setCandidates] = useState<FieldCandidate[]>([]);
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  // draft/contract
  const [contractId, setContractId] = useState<number | null>(null);

  // complete fields
  const [startDate, setStartDate] = useState('');
  const [durationMonths, setDurationMonths] = useState(12);
  const [tariffGroup, setTariffGroup] = useState('');
  const [subscriptionCodes, setSubscriptionCodes] = useState('');

  // preview / finalize
  const [previewSections, setPreviewSections] = useState<string[] | null>(null);
  const [finalizeResult, setFinalizeResult] = useState<{ pdf_sha256: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  useEffect(() => {
    if (open) {
      // her açılışta temiz başla
      setStep('upload'); setError(null); setLoading(false);
      setTaxCertFile(null); setSignatureCircularFile(null);
      setCandidates([]);
      setContractId(null);
      setStartDate(''); setDurationMonths(12); setTariffGroup(''); setSubscriptionCodes('');
      setPreviewSections(null); setFinalizeResult(null);
    }
  }, [open]);

  const hasUnresolvedConflicts = useMemo(() => candidates.some(c => c.conflict_status === 'conflict'), [candidates]);

  const fieldGroups = useMemo(() => {
    const groups = new Map<string, FieldCandidate[]>();
    for (const c of candidates) {
      if (!groups.has(c.field_name)) groups.set(c.field_name, []);
      groups.get(c.field_name)!.push(c);
    }
    return groups;
  }, [candidates]);

  const requiredFieldsReady = useMemo(() => {
    const requiredForLegal = ['legal_name', 'tax_number', 'tax_office', 'registered_address'];
    const requiredForRep = ['representative_full_name', 'representative_national_id'];
    return [...requiredForLegal, ...requiredForRep].every(name => {
      const group = fieldGroups.get(name) || [];
      return group.some(c => effectiveValue(c) !== null);
    });
  }, [fieldGroups]);

  // ── Adım 1 → 2: yükle + extraction + conflict detection ──
  const handleUploadAndProcess = useCallback(async () => {
    if (!taxCertFile || !signatureCircularFile) {
      setError('Vergi levhası ve imza sirküleri belgelerinin ikisi de yüklenmelidir.');
      return;
    }
    setError(null);
    setStep('processing');
    setLoading(true);
    try {
      const [taxUpload, sigUpload] = await Promise.all([
        uploadContractDocument('vergi_levhasi', taxCertFile, customerId),
        uploadContractDocument('imza_sirkusu', signatureCircularFile, customerId),
      ]);
      const ids = [taxUpload.document_id, sigUpload.document_id];

      await Promise.all(ids.map(id => startContractExtraction(id)));
      const results = await Promise.all(ids.map(id => getContractExtractionResult(id)));

      const failed = results.find(r => r.status === 'failed');
      if (failed) {
        throw new Error(`Belge işlenemedi (${failed.document_type}). Lütfen görseli/PDF'i kontrol edip tekrar deneyin.`);
      }

      await detectContractConflicts(ids);
      const refreshed = await Promise.all(ids.map(id => getContractExtractionResult(id)));
      setCandidates(refreshed.flatMap(r => r.candidates));
      setStep('review');
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Belge işleme sırasında hata oluştu.');
      setStep('upload');
    } finally {
      setLoading(false);
    }
  }, [taxCertFile, signatureCircularFile, customerId]);

  // ── Review: bir adayı çöz ──
  const handleResolve = useCallback(async (candidate: FieldCandidate, decision: 'accepted' | 'corrected', correctedValue?: string) => {
    setResolvingId(candidate.id);
    setError(null);
    try {
      const updated = await resolveContractCandidate(candidate.id, decision, correctedValue);
      setCandidates(prev => {
        // sunucu conflict grubundaki KARDEŞ adayları da 'resolved' yapmış olabilir
        // (sibling-cascade) — o yüzden ilgili document'lardan tazelenmiş tam
        // listeyi tekrar çekmek yerine, yalnız bu adayı güncelleyip; conflict
        // grubundaki diğerlerini de conflict_status='resolved' olarak işaretle.
        return prev.map(c => {
          if (c.id === updated.id) return updated;
          if (c.field_name === updated.field_name && c.conflict_status === 'conflict') {
            return { ...c, conflict_status: 'resolved' };
          }
          return c;
        });
      });
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Alan onaylanamadı.');
    } finally {
      setResolvingId(null);
    }
  }, []);

  // ── Adım 3 → 4: legal profile + representative + draft oluştur ──
  const handleConfirmReview = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const get = (name: string): string => {
        const group = fieldGroups.get(name) || [];
        const found = group.map(effectiveValue).find(v => v !== null);
        return found || '';
      };

      const profile = await saveLegalProfile({
        customer_id: customerId,
        legal_name: get('legal_name'),
        tax_number: get('tax_number'),
        tax_office: get('tax_office'),
        mersis_number: get('mersis_number') || undefined,
        trade_registry_number: get('trade_registry_number') || undefined,
        registered_address: get('registered_address'),
        facility_address: get('facility_address') || undefined,
      });

      const rep = await saveRepresentative({
        customer_id: customerId,
        legal_profile_id: profile.id,
        full_name: get('representative_full_name'),
        national_id: get('representative_national_id'),
        authority_type: get('authority_type') || undefined,
        authority_scope: get('authority_scope') || undefined,
        is_indefinite: get('is_indefinite').toLowerCase() === 'true',
      });

      const draft = await createContractDraft(offerId, customerId, profile.id, rep.id);
      setContractId(draft.id);
      setStep('complete_fields');
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Sözleşme taslağı oluşturulamadı.');
    } finally {
      setLoading(false);
    }
  }, [fieldGroups, customerId, offerId]);

  // ── Adım 4 → 5: önizleme ──
  const handlePreview = useCallback(async () => {
    if (!contractId || !startDate || !durationMonths) {
      setError('Tedarik başlangıç tarihi ve sözleşme süresi (ay) zorunludur.');
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const resp = await previewContract(contractId, {
        start_date: startDate,
        duration_months: durationMonths,
        tariff_group: tariffGroup || undefined,
        subscription_codes: subscriptionCodes || undefined,
      });
      setPreviewSections(resp.rendered_html_sections);
      setStep('preview');
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Önizleme oluşturulamadı.');
    } finally {
      setLoading(false);
    }
  }, [contractId, startDate, durationMonths, tariffGroup, subscriptionCodes]);

  // ── Adım 5 → 6: finalize ──
  const handleFinalize = useCallback(async () => {
    if (!contractId) return;
    setError(null);
    setLoading(true);
    try {
      const resp = await finalizeContract(contractId);
      setFinalizeResult({ pdf_sha256: resp.pdf_sha256 });
      setStep('done');
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || err?.message || 'Sözleşme finalize edilemedi.');
    } finally {
      setLoading(false);
    }
  }, [contractId]);

  const [downloadingPdf, setDownloadingPdf] = useState(false);
  const handleDownloadContractPdf = useCallback(async () => {
    if (!contractId) return;
    setError(null);
    setDownloadingPdf(true);
    try {
      await downloadContractPdf(contractId, `sozlesme_${contractId}.pdf`);
    } catch (err: any) {
      setError(err?.message || 'Sözleşme PDF indirilemedi.');
    } finally {
      setDownloadingPdf(false);
    }
  }, [contractId]);

  if (!open) return null;

  return (
    <div
      ref={backdropRef}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => { if (e.target === backdropRef.current) onClose(); }}
      data-testid="contract-wizard-backdrop"
    >
      <div
        className="relative w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-lg bg-white p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-label="Sözleşme Hazırla"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Sözleşme Hazırla</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-gray-400 hover:text-gray-600" aria-label="Kapat">✕</button>
        </div>

        <StepIndicator current={step} />

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
            <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {step === 'upload' && (
          <div className="space-y-4">
            <p className="text-sm text-gray-600">Sözleşme taraflarını ve Ek Protokol bilgilerini otomatik doldurmak için vergi levhası ve imza sirküleri belgelerini yükleyin.</p>
            <FileField label="Vergi Levhası" file={taxCertFile} onChange={setTaxCertFile} />
            <FileField label="İmza Sirküleri" file={signatureCircularFile} onChange={setSignatureCircularFile} />
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" onClick={onClose} className="btn-secondary">Vazgeç</button>
              <button
                type="button"
                onClick={handleUploadAndProcess}
                disabled={!taxCertFile || !signatureCircularFile || loading}
                className="btn-primary flex items-center gap-2"
              >
                <Upload className="w-4 h-4" /> Yükle ve Devam Et
              </button>
            </div>
          </div>
        )}

        {step === 'processing' && (
          <div className="flex flex-col items-center justify-center py-12 gap-3 text-gray-600">
            <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
            <p className="text-sm">Belgeler okunuyor ve karşılaştırılıyor…</p>
          </div>
        )}

        {step === 'review' && (
          <div className="space-y-4">
            {hasUnresolvedConflicts && (
              <div className="flex items-start gap-2 rounded-md bg-amber-50 border border-amber-200 p-3 text-sm text-amber-800">
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>Belgeler arasında çelişki tespit edildi. Devam etmeden önce her çelişkili alan için doğru değeri seçin.</span>
              </div>
            )}
            <div className="space-y-3 max-h-[50vh] overflow-y-auto pr-1">
              {[...fieldGroups.entries()]
                .filter(([name]) => !INFO_ONLY_FIELDS.includes(name))
                .sort(([a], [b]) => {
                  const order = [...LEGAL_PROFILE_FIELDS, ...REPRESENTATIVE_FIELDS];
                  return order.indexOf(a) - order.indexOf(b);
                })
                .map(([name, group]) => (
                  <FieldReviewGroup
                    key={name}
                    label={FIELD_LABELS[name] || name}
                    candidates={group}
                    resolvingId={resolvingId}
                    onResolve={handleResolve}
                  />
                ))}
            </div>
            <div className="flex justify-between pt-2">
              <button type="button" onClick={() => setStep('upload')} className="btn-secondary">Geri</button>
              <button
                type="button"
                onClick={handleConfirmReview}
                disabled={hasUnresolvedConflicts || !requiredFieldsReady || loading}
                className="btn-primary flex items-center gap-2"
              >
                {loading && <Loader2 className="w-4 h-4 animate-spin" />} Devam Et
              </button>
            </div>
            {!hasUnresolvedConflicts && !requiredFieldsReady && (
              <p className="text-xs text-gray-500">Devam etmek için zorunlu alanları (unvan, vergi no, vergi dairesi, adres, yetkili ad-soyad, T.C. no) onaylayın.</p>
            )}
          </div>
        )}

        {step === 'complete_fields' && (
          <div className="space-y-4">
            <div>
              <label className="label">Tedarik Başlangıç Tarihi</label>
              <input type="date" className="input" value={startDate} onChange={e => setStartDate(e.target.value)} />
            </div>
            <div>
              <label className="label">Sözleşme Süresi (ay)</label>
              <input type="number" min={1} className="input" value={durationMonths} onChange={e => setDurationMonths(Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Tarife Grubu (opsiyonel — bulunamazsa elle girin)</label>
              <input type="text" className="input" value={tariffGroup} onChange={e => setTariffGroup(e.target.value)} placeholder="örn. Sanayi OG Çift Terimli" />
            </div>
            <div>
              <label className="label">Kapsamdaki Abonelik Kodları (opsiyonel)</label>
              <input type="text" className="input" value={subscriptionCodes} onChange={e => setSubscriptionCodes(e.target.value)} />
            </div>
            <div className="flex justify-between pt-2">
              <button type="button" onClick={() => setStep('review')} className="btn-secondary">Geri</button>
              <button
                type="button"
                onClick={handlePreview}
                disabled={!startDate || !durationMonths || loading}
                className="btn-primary flex items-center gap-2"
              >
                {loading && <Loader2 className="w-4 h-4 animate-spin" />} Önizle
              </button>
            </div>
          </div>
        )}

        {step === 'preview' && previewSections && (
          <div className="space-y-4">
            <div className="max-h-[55vh] overflow-y-auto rounded border border-gray-200 p-4 space-y-6 bg-gray-50">
              {previewSections.map((html, idx) => (
                <div key={idx} className="bg-white rounded shadow-sm p-4 text-xs" dangerouslySetInnerHTML={{ __html: html }} />
              ))}
            </div>
            <div className="flex justify-between pt-2">
              <button type="button" onClick={() => setStep('complete_fields')} className="btn-secondary">Geri</button>
              <button type="button" onClick={handleFinalize} disabled={loading} className="btn-primary flex items-center gap-2">
                {loading && <Loader2 className="w-4 h-4 animate-spin" />} Sözleşmeyi Onayla ve Oluştur
              </button>
            </div>
          </div>
        )}

        {step === 'done' && contractId && (
          <div className="flex flex-col items-center justify-center py-8 gap-4 text-center">
            <CheckCircle2 className="w-12 h-12 text-green-600" />
            <p className="text-gray-900 font-medium">Sözleşme oluşturuldu.</p>
            {finalizeResult && <p className="text-xs text-gray-400 font-mono">sha256: {finalizeResult.pdf_sha256.slice(0, 16)}…</p>}
            <button type="button" onClick={handleDownloadContractPdf} disabled={downloadingPdf} className="btn-primary flex items-center gap-2">
              {downloadingPdf ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
              Sözleşme PDF İndir
            </button>
            <button type="button" onClick={onClose} className="btn-secondary">Kapat</button>
          </div>
        )}
      </div>
    </div>
  );
};

// =============================================================================
// Alt bileşenler
// =============================================================================

function FileField({ label, file, onChange }: { label: string; file: File | null; onChange: (f: File | null) => void }) {
  return (
    <div>
      <label className="label">{label}</label>
      <input
        type="file"
        accept=".pdf,.png,.jpg,.jpeg"
        onChange={(e) => onChange(e.target.files?.[0] || null)}
        className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-primary-50 file:px-3 file:py-1 file:text-sm file:font-medium file:text-primary-700 hover:file:bg-primary-100"
      />
      {file && <p className="mt-1 text-xs text-gray-500">{file.name}</p>}
    </div>
  );
}

function FieldReviewGroup({
  label, candidates, resolvingId, onResolve,
}: {
  label: string;
  candidates: FieldCandidate[];
  resolvingId: number | null;
  onResolve: (c: FieldCandidate, decision: 'accepted' | 'corrected', correctedValue?: string) => void;
}) {
  const [correctionDraft, setCorrectionDraft] = useState<Record<number, string>>({});
  const isConflict = candidates.some(c => c.conflict_status === 'conflict');

  return (
    <div className={`rounded-md border p-3 ${isConflict ? 'border-amber-300 bg-amber-50/50' : 'border-gray-200'}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-sm font-medium text-gray-700">{label}</span>
        {isConflict && <span className="text-xs font-medium text-amber-700 bg-amber-100 rounded-full px-2 py-0.5">Çelişki</span>}
      </div>
      <div className="space-y-2">
        {candidates.map(c => {
          const resolved = c.user_decision === 'accepted' || c.user_decision === 'corrected';
          return (
            <div key={c.id} className="flex items-center gap-2 text-sm">
              <span className="text-[10px] uppercase tracking-wide text-gray-400 w-20 flex-shrink-0">
                {c.source_document === 'vergi_levhasi' ? 'Vergi Lv.' : 'İmza Sirk.'}
              </span>
              <span className={`flex-1 ${resolved ? 'text-gray-900 font-medium' : 'text-gray-600'}`}>
                {effectiveValue(c) ?? c.raw_value ?? <em className="text-gray-400">boş</em>}
              </span>
              <span className="text-[10px] text-gray-400">%{Math.round(c.confidence * 100)}</span>
              {resolved ? (
                <CheckCircle2 className="w-4 h-4 text-green-600" />
              ) : (
                <>
                  <button
                    type="button"
                    disabled={resolvingId === c.id}
                    onClick={() => onResolve(c, 'accepted')}
                    className="text-xs text-primary-700 hover:underline"
                  >
                    Kabul Et
                  </button>
                  <input
                    type="text"
                    placeholder="düzelt…"
                    value={correctionDraft[c.id] ?? ''}
                    onChange={e => setCorrectionDraft(prev => ({ ...prev, [c.id]: e.target.value }))}
                    className="w-24 rounded border border-gray-200 px-1.5 py-0.5 text-xs"
                  />
                  <button
                    type="button"
                    disabled={resolvingId === c.id || !correctionDraft[c.id]}
                    onClick={() => onResolve(c, 'corrected', correctionDraft[c.id])}
                    className="text-xs text-gray-500 hover:underline"
                  >
                    Düzelt
                  </button>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
