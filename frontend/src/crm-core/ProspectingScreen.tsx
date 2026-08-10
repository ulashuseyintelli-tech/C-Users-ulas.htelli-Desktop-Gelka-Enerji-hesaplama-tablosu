// =============================================================================
// S4 — Prospecting — Prospectler ekranı: keşif/arama + kayıtlı liste.
// =============================================================================
//
// CustomersScreen.tsx (S1) ile AYNI liste iskeleti (arama + tablo + satır
// tıklama → detay), üstüne owner'ın istediği discovery paneli eklendi.
//
// Owner: "Discovery results must NOT be written directly to DB, only
// after explicit 'Prospect Olarak Kaydet'" — arama sonuçları (candidate)
// SADECE state'te tutulur, kullanıcı açıkça bir candidate'ı seçip
// kaydetmedikçe hiçbir POST /prospects çağrısı yapılmaz.
//
// Discovery arama best-effort'tur (bkz. backend discovery.py — DuckDuckGo
// bot-challenge sunabilir, CAPTCHA bypass edilmez) — bu yüzden "Web
// Sitesi Ekle" (manuel/doğrudan URL) HER ZAMAN birincil, güvenilir yol
// olarak da sunulur, aramaya bağımlı değildir.
import { useCallback, useEffect, useState } from 'react';
import { Search, Loader2, AlertCircle, Radar, Plus, ExternalLink } from 'lucide-react';
import {
  listProspects,
  discoverProspects,
  createProspect,
  ProspectCompanyOut,
  ProspectStatus,
  DiscoverCandidateOut,
  DedupMatchOut,
} from './prospectingApi';
import { ProspectDetailScreen } from './ProspectDetailScreen';
import type { Subject } from './crmApi';

const STATUS_OPTIONS: Array<{ value: ProspectStatus | ''; label: string }> = [
  { value: '', label: 'Tüm durumlar' },
  { value: 'DISCOVERED', label: 'Keşfedildi' },
  { value: 'VERIFIED', label: 'Doğrulandı' },
  { value: 'QUALIFIED', label: 'Uygun' },
  { value: 'DISQUALIFIED', label: 'Uygun Değil' },
  { value: 'CONVERTED', label: 'Müşteriye Dönüştürüldü' },
];
const STATUS_COLORS: Record<string, string> = {
  DISCOVERED: 'bg-gray-100 text-gray-600', VERIFIED: 'bg-blue-100 text-blue-700', QUALIFIED: 'bg-green-100 text-green-700',
  DISQUALIFIED: 'bg-red-100 text-red-700', CONVERTED: 'bg-primary-100 text-primary-700',
};
const STATUS_LABELS: Record<string, string> = {
  DISCOVERED: 'Keşfedildi', VERIFIED: 'Doğrulandı', QUALIFIED: 'Uygun', DISQUALIFIED: 'Uygun Değil', CONVERTED: 'Dönüştürüldü',
};

interface ProspectingScreenProps {
  onOpenSubject: (subject: Subject) => void;
}

export function ProspectingScreen({ onOpenSubject }: ProspectingScreenProps) {
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // --- Liste + filtreler ---
  const [items, setItems] = useState<ProspectCompanyOut[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<ProspectStatus | ''>('');
  const [cityFilter, setCityFilter] = useState('');
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  // --- Discovery (arama) paneli ---
  const [mode, setMode] = useState<'search' | 'manual'>('manual');
  const [keyword, setKeyword] = useState('');
  const [searchCity, setSearchCity] = useState('');
  const [searchDistrict, setSearchDistrict] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<DiscoverCandidateOut[] | null>(null);

  // --- Manuel ekleme formu ---
  const [manualName, setManualName] = useState('');
  const [manualWebsite, setManualWebsite] = useState('');
  const [manualCity, setManualCity] = useState('');
  const [manualSector, setManualSector] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveInfo, setSaveInfo] = useState<string | null>(null);
  const [dedupMatches, setDedupMatches] = useState<DedupMatchOut[] | null>(null);

  const loadList = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const data = await listProspects({
        search: search || undefined,
        status: statusFilter || undefined,
        city: cityFilter || undefined,
        limit: 100,
      });
      setItems(data.items);
      setTotal(data.total);
    } catch (err: any) {
      setListError(err?.response?.data?.detail?.message || err?.message || 'Prospect listesi yüklenemedi.');
    } finally {
      setListLoading(false);
    }
  }, [search, statusFilter, cityFilter]);

  useEffect(() => {
    if (selectedId !== null) return;
    const timer = setTimeout(() => loadList(), 300);
    return () => clearTimeout(timer);
  }, [loadList, selectedId]);

  const handleSearch = async () => {
    if (!keyword.trim()) return;
    setSearching(true);
    setSearchError(null);
    setCandidates(null);
    try {
      const result = await discoverProspects(keyword, searchCity || undefined, searchDistrict || undefined);
      if (result.status !== 'OK') {
        setSearchError(result.message || 'Arama şu an kullanılamıyor.');
        setCandidates([]);
      } else {
        setCandidates(result.candidates);
      }
    } catch (err: any) {
      setSearchError(err?.response?.data?.detail?.message || err?.message || 'Arama başarısız oldu.');
    } finally {
      setSearching(false);
    }
  };

  const saveProspect = async (input: Parameters<typeof createProspect>[0]) => {
    setSaving(true);
    setSaveError(null);
    setSaveInfo(null);
    try {
      const result = await createProspect(input);
      if (result.dedup_verdict === 'review_required') {
        setDedupMatches(result.matches);
      } else if (result.dedup_verdict === 'exact_duplicate') {
        setSaveInfo('Bu şirket zaten kayıtlı — mevcut kayıt gösteriliyor.');
        setDedupMatches(null);
        if (result.prospect) setSelectedId(result.prospect.id);
      } else {
        setSaveInfo('Prospect kaydedildi.');
        setDedupMatches(null);
        setManualName(''); setManualWebsite(''); setManualCity(''); setManualSector('');
        await loadList();
      }
    } catch (err: any) {
      setSaveError(err?.response?.data?.detail?.message || err?.message || 'Kaydedilemedi.');
    } finally {
      setSaving(false);
    }
  };

  const handleManualSave = () => {
    if (!manualName.trim() && !manualWebsite.trim()) {
      setSaveError('Firma adı veya web sitesi adreslerinden en az biri gerekli.');
      return;
    }
    saveProspect({ trade_name: manualName || undefined, website: manualWebsite || undefined, city: manualCity || undefined, sector: manualSector || undefined });
  };

  const handleSaveCandidate = (candidate: DiscoverCandidateOut) => {
    saveProspect({ trade_name: candidate.title || undefined, website: candidate.url, source_url: candidate.url, source_type: 'SEARCH_RESULT' });
  };

  if (selectedId !== null) {
    return <ProspectDetailScreen prospectId={selectedId} onBack={() => setSelectedId(null)} onOpenSubject={onOpenSubject} />;
  }

  return (
    <div className="space-y-4">
      <div className="card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Radar className="w-5 h-5 text-primary-600" />
          <h2 className="text-base font-bold text-gray-900">Yeni Prospect Keşfet</h2>
        </div>

        <div className="flex gap-2 text-sm">
          <button onClick={() => setMode('manual')} className={`px-3 py-1.5 rounded-lg font-medium ${mode === 'manual' ? 'bg-primary-100 text-primary-700' : 'text-gray-500 hover:bg-gray-100'}`}>
            Web Sitesi ile Ekle
          </button>
          <button onClick={() => setMode('search')} className={`px-3 py-1.5 rounded-lg font-medium ${mode === 'search' ? 'bg-primary-100 text-primary-700' : 'text-gray-500 hover:bg-gray-100'}`}>
            Sektör/Bölge ile Ara
          </button>
        </div>

        {mode === 'manual' && (
          <div className="space-y-2">
            <p className="text-xs text-gray-500">En güvenilir yol: şirketin bilinen web sitesini doğrudan girin — sistem siteyi tarayıp iletişim bilgilerini keşfeder.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <input value={manualName} onChange={(e) => setManualName(e.target.value)} placeholder="Firma adı" className="input" />
              <input value={manualWebsite} onChange={(e) => setManualWebsite(e.target.value)} placeholder="Web sitesi (ör. sirket.com.tr)" className="input" />
              <input value={manualCity} onChange={(e) => setManualCity(e.target.value)} placeholder="Şehir (opsiyonel)" className="input" />
              <input value={manualSector} onChange={(e) => setManualSector(e.target.value)} placeholder="Sektör (opsiyonel)" className="input" />
            </div>
            <button disabled={saving} onClick={handleManualSave} className="btn-primary text-sm flex items-center gap-1.5">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Prospect Olarak Kaydet
            </button>
          </div>
        )}

        {mode === 'search' && (
          <div className="space-y-2">
            <p className="text-xs text-gray-500">Örnek: "plastik üreticileri" + "Kocaeli" + "Gebze". Arama sonuçları yalnız ADAY listesidir — kaydetmeden hiçbir veri saklanmaz.</p>
            <div className="flex flex-wrap gap-2">
              <input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="Sektör / anahtar kelime" className="input flex-1 min-w-[160px]" />
              <input value={searchCity} onChange={(e) => setSearchCity(e.target.value)} placeholder="Şehir" className="input w-32" />
              <input value={searchDistrict} onChange={(e) => setSearchDistrict(e.target.value)} placeholder="İlçe/OSB (opsiyonel)" className="input w-40" />
              <button disabled={searching || !keyword.trim()} onClick={handleSearch} className="btn-primary text-sm flex items-center gap-1.5">
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />} Ara
              </button>
            </div>

            {searchError && (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-700">{searchError}</div>
            )}

            {candidates !== null && candidates.length > 0 && (
              <div className="space-y-1.5">
                {candidates.map((c, i) => (
                  <div key={i} className="flex items-center justify-between gap-2 bg-gray-50 rounded px-3 py-2">
                    <div className="min-w-0">
                      <a href={c.url} target="_blank" rel="noopener noreferrer" className="text-sm text-primary-600 hover:underline inline-flex items-center gap-1 truncate">
                        {c.title || c.url} <ExternalLink className="w-3 h-3 flex-shrink-0" />
                      </a>
                      {c.snippet && <p className="text-xs text-gray-500 truncate">{c.snippet}</p>}
                    </div>
                    <button disabled={saving} onClick={() => handleSaveCandidate(c)} className="btn-secondary text-xs flex-shrink-0">Prospect Olarak Kaydet</button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {saveError && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">{saveError}</div>
        )}
        {saveInfo && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-700">{saveInfo}</div>
        )}
        {dedupMatches && (
          <div className="border border-amber-200 bg-amber-50 rounded-lg p-3 space-y-2">
            <p className="text-sm text-amber-700">
              ⚠ Olası mükerrer kayıt(lar) bulundu — sistem otomatik birleştirme yapmaz, siz karar verin:
            </p>
            {dedupMatches.map((m) => (
              <div key={m.company_id} className="text-sm text-gray-700 bg-white rounded px-2 py-1.5">
                {m.display_name || '—'} {m.website ? `(${m.website})` : ''} {m.city ? `· ${m.city}` : ''}
                <span className="text-xs text-gray-400"> — eşleşme: {m.match_signal === 'domain' ? 'web sitesi' : m.match_signal === 'name' ? 'firma adı' : 'telefon'}</span>
              </div>
            ))}
            <div className="flex gap-2">
              <button
                onClick={() => { saveProspect({ trade_name: manualName || undefined, website: manualWebsite || undefined, city: manualCity || undefined, sector: manualSector || undefined, force_create_despite_duplicate: true }); }}
                className="btn-secondary text-xs"
              >
                Yine de Ayrı Kayıt Olarak Ekle
              </button>
              <button onClick={() => setDedupMatches(null)} className="btn-secondary text-xs">Vazgeç</button>
            </div>
          </div>
        )}
      </div>

      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap gap-3 items-center">
          <div className="relative flex-1 min-w-[200px]">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Firma veya web sitesi ara..." className="input w-full pl-9" />
          </div>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as ProspectStatus | '')} className="input">
            {STATUS_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
          </select>
          <input value={cityFilter} onChange={(e) => setCityFilter(e.target.value)} placeholder="Şehir" className="input w-32" />
        </div>
      </div>

      {listError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{listError}</p>
        </div>
      )}

      <div className="card overflow-x-auto">
        {listLoading ? (
          <div className="p-8 text-center text-gray-500">
            <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
            Yükleniyor...
          </div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-gray-500">
            {search || statusFilter || cityFilter ? 'Aramayla eşleşen prospect bulunamadı.' : 'Henüz prospect kaydı yok — yukarıdan yeni bir şirket ekleyin.'}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-left text-gray-500">
                <th className="px-4 py-2 font-medium">Firma</th>
                <th className="px-4 py-2 font-medium">Şehir</th>
                <th className="px-4 py-2 font-medium">Sektör</th>
                <th className="px-4 py-2 font-medium">İletişim</th>
                <th className="px-4 py-2 font-medium">Kaynak</th>
                <th className="px-4 py-2 font-medium">Durum</th>
              </tr>
            </thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.id} onClick={() => setSelectedId(p.id)} className="border-b border-gray-100 last:border-0 hover:bg-gray-50 cursor-pointer">
                  <td className="px-4 py-2 font-medium text-gray-900">{p.trade_name || p.legal_name || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{p.city || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{p.sector || '—'}</td>
                  <td className="px-4 py-2 text-gray-600">{p.contact_count}</td>
                  <td className="px-4 py-2 text-gray-600">{p.source_count}</td>
                  <td className="px-4 py-2">
                    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_COLORS[p.status]}`}>
                      {STATUS_LABELS[p.status]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {total > items.length && <p className="text-xs text-gray-400 text-right">{items.length} / {total} gösteriliyor</p>}
    </div>
  );
}
