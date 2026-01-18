import { useState, useEffect, useCallback } from 'react';
import { 
  Settings, Database, DollarSign, Lock, Unlock, Plus, 
  RefreshCw, AlertCircle, CheckCircle, Search, ArrowLeft,
  AlertTriangle, Eye, CheckSquare
} from 'lucide-react';
import {
  getMarketPrices, upsertMarketPrice, lockMarketPrice, unlockMarketPrice,
  getDistributionTariffs, lookupDistributionTariff,
  setAdminApiKey, getAdminApiKey, clearAdminApiKey,
  getIncidents, updateIncidentStatus, getIncidentStats,
  MarketPrice, DistributionTariff, TariffLookupResult, Incident, IncidentStatsResponse
} from './api';

type Tab = 'market-prices' | 'distribution-tariffs' | 'tariff-lookup' | 'incidents';

interface AdminPanelProps {
  onBack: () => void;
}

export default function AdminPanel({ onBack }: AdminPanelProps) {
  const [activeTab, setActiveTab] = useState<Tab>('market-prices');
  const [apiKey, setApiKey] = useState('');
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Market Prices state
  const [marketPrices, setMarketPrices] = useState<MarketPrice[]>([]);
  const [loadingPrices, setLoadingPrices] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newPeriod, setNewPeriod] = useState('');
  const [newPtf, setNewPtf] = useState(2974.1);
  const [newYekdem, setNewYekdem] = useState(364.0);
  const [newSourceNote, setNewSourceNote] = useState('');

  // Distribution Tariffs state
  const [tariffs, setTariffs] = useState<DistributionTariff[]>([]);
  const [loadingTariffs, setLoadingTariffs] = useState(false);

  // Tariff Lookup state
  const [lookupGroup, setLookupGroup] = useState('Sanayi');
  const [lookupVoltage, setLookupVoltage] = useState('OG');
  const [lookupTerm, setLookupTerm] = useState('Çift Terim');
  const [lookupResult, setLookupResult] = useState<TariffLookupResult | null>(null);
  const [lookupLoading, setLookupLoading] = useState(false);

  // Incidents state
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [incidentStats, setIncidentStats] = useState<IncidentStatsResponse | null>(null);
  const [loadingIncidents, setLoadingIncidents] = useState(false);
  const [incidentFilter, setIncidentFilter] = useState<{status?: string; severity?: string}>({});

  const handleLogin = () => {
    if (apiKey.trim()) {
      setAdminApiKey(apiKey.trim());
      setIsAuthenticated(true);
      setError(null);
    }
  };

  const handleLogout = () => {
    clearAdminApiKey();
    setIsAuthenticated(false);
    setApiKey('');
  };

  const loadMarketPrices = useCallback(async () => {
    setLoadingPrices(true);
    setError(null);
    try {
      const response = await getMarketPrices(24);
      setMarketPrices(response.prices);
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Fiyatlar yüklenemedi: ${msg}`);
      if (err.response?.status === 401 || err.response?.status === 403) {
        setIsAuthenticated(false);
      }
    } finally {
      setLoadingPrices(false);
    }
  }, []);

  const loadTariffs = useCallback(async () => {
    setLoadingTariffs(true);
    setError(null);
    try {
      const response = await getDistributionTariffs();
      setTariffs(response.tariffs);
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Tarifeler yüklenemedi: ${msg}`);
    } finally {
      setLoadingTariffs(false);
    }
  }, []);

  const loadIncidents = useCallback(async () => {
    setLoadingIncidents(true);
    setError(null);
    try {
      const [incidentsRes, statsRes] = await Promise.all([
        getIncidents({ ...incidentFilter, limit: 100 }),
        getIncidentStats()
      ]);
      setIncidents(incidentsRes.incidents);
      setIncidentStats(statsRes);
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Incident'lar yüklenemedi: ${msg}`);
    } finally {
      setLoadingIncidents(false);
    }
  }, [incidentFilter]);

  useEffect(() => {
    if (isAuthenticated) {
      if (activeTab === 'market-prices') {
        loadMarketPrices();
      } else if (activeTab === 'distribution-tariffs') {
        loadTariffs();
      } else if (activeTab === 'incidents') {
        loadIncidents();
      }
    }
  }, [isAuthenticated, activeTab, loadMarketPrices, loadTariffs, loadIncidents]);

  const handleAddPrice = async () => {
    if (!newPeriod || newPtf <= 0) {
      setError('Dönem ve PTF zorunlu');
      return;
    }
    
    setError(null);
    try {
      await upsertMarketPrice(newPeriod, newPtf, newYekdem, newSourceNote || undefined);
      setSuccess(`Dönem ${newPeriod} eklendi/güncellendi`);
      setShowAddForm(false);
      setNewPeriod('');
      setNewSourceNote('');
      loadMarketPrices();
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Kaydetme hatası: ${msg}`);
    }
  };

  const handleLock = async (period: string) => {
    try {
      await lockMarketPrice(period);
      setSuccess(`Dönem ${period} kilitlendi`);
      loadMarketPrices();
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Kilitleme hatası: ${msg}`);
    }
  };

  const handleUnlock = async (period: string) => {
    if (!confirm(`${period} döneminin kilidini kaldırmak istediğinize emin misiniz?`)) return;
    try {
      await unlockMarketPrice(period);
      setSuccess(`Dönem ${period} kilidi kaldırıldı`);
      loadMarketPrices();
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Kilit kaldırma hatası: ${msg}`);
    }
  };

  const handleLookup = async () => {
    setLookupLoading(true);
    setError(null);
    try {
      const result = await lookupDistributionTariff(lookupGroup, lookupVoltage, lookupTerm);
      setLookupResult(result);
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Lookup hatası: ${msg}`);
    } finally {
      setLookupLoading(false);
    }
  };

  const handleUpdateIncident = async (id: number, status: 'OPEN' | 'ACK' | 'RESOLVED') => {
    try {
      await updateIncidentStatus(id, status);
      setSuccess(`Incident #${id} durumu güncellendi`);
      loadIncidents();
    } catch (err: any) {
      const msg = err.response?.data?.detail?.message || err.response?.data?.detail || err.message;
      setError(`Güncelleme hatası: ${msg}`);
    }
  };

  // Clear messages after 5 seconds
  useEffect(() => {
    if (success) {
      const timer = setTimeout(() => setSuccess(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [success]);

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex items-center justify-center">
        <div className="card max-w-md w-full">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
              <Settings className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">Admin Panel</h1>
              <p className="text-sm text-gray-500">Yetkilendirme gerekli</p>
            </div>
          </div>
          
          <div className="space-y-4">
            <div>
              <label className="label">Admin API Key</label>
              <input
                type="password"
                className="input"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="X-Admin-Key"
                onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
              />
            </div>
            
            <button onClick={handleLogin} className="btn-primary w-full">
              Giriş Yap
            </button>
            
            <button onClick={onBack} className="btn-secondary w-full flex items-center justify-center gap-2">
              <ArrowLeft className="w-4 h-4" />
              Ana Sayfaya Dön
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <button onClick={onBack} className="p-2 hover:bg-gray-100 rounded-lg">
                <ArrowLeft className="w-5 h-5 text-gray-600" />
              </button>
              <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
                <Settings className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">Admin Panel</h1>
                <p className="text-sm text-gray-500">Piyasa Fiyatları & Tarifeler</p>
              </div>
            </div>
            <button onClick={handleLogout} className="text-sm text-gray-500 hover:text-gray-700">
              Çıkış
            </button>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6">
          <nav className="flex gap-6">
            <button
              onClick={() => setActiveTab('market-prices')}
              className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
                activeTab === 'market-prices'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <DollarSign className="w-4 h-4 inline mr-2" />
              PTF/YEKDEM
            </button>
            <button
              onClick={() => setActiveTab('distribution-tariffs')}
              className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
                activeTab === 'distribution-tariffs'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <Database className="w-4 h-4 inline mr-2" />
              EPDK Tarifeleri
            </button>
            <button
              onClick={() => setActiveTab('tariff-lookup')}
              className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
                activeTab === 'tariff-lookup'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <Search className="w-4 h-4 inline mr-2" />
              Tarife Test
            </button>
            <button
              onClick={() => setActiveTab('incidents')}
              className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
                activeTab === 'incidents'
                  ? 'border-primary-600 text-primary-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              <AlertTriangle className="w-4 h-4 inline mr-2" />
              Incidents
              {incidentStats && incidentStats.by_status?.OPEN > 0 && (
                <span className="ml-2 px-2 py-0.5 text-xs bg-red-100 text-red-700 rounded-full">
                  {incidentStats.by_status.OPEN}
                </span>
              )}
            </button>
          </nav>
        </div>
      </div>

      {/* Messages */}
      <div className="max-w-7xl mx-auto px-6 pt-4">
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3 mb-4">
            <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}
        {success && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-start gap-3 mb-4">
            <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
            <p className="text-sm text-green-700">{success}</p>
          </div>
        )}
      </div>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'market-prices' && (
          <MarketPricesTab
            prices={marketPrices}
            loading={loadingPrices}
            onRefresh={loadMarketPrices}
            onLock={handleLock}
            onUnlock={handleUnlock}
            showAddForm={showAddForm}
            setShowAddForm={setShowAddForm}
            newPeriod={newPeriod}
            setNewPeriod={setNewPeriod}
            newPtf={newPtf}
            setNewPtf={setNewPtf}
            newYekdem={newYekdem}
            setNewYekdem={setNewYekdem}
            newSourceNote={newSourceNote}
            setNewSourceNote={setNewSourceNote}
            onAdd={handleAddPrice}
          />
        )}

        {activeTab === 'distribution-tariffs' && (
          <DistributionTariffsTab
            tariffs={tariffs}
            loading={loadingTariffs}
            onRefresh={loadTariffs}
          />
        )}

        {activeTab === 'tariff-lookup' && (
          <TariffLookupTab
            group={lookupGroup}
            setGroup={setLookupGroup}
            voltage={lookupVoltage}
            setVoltage={setLookupVoltage}
            term={lookupTerm}
            setTerm={setLookupTerm}
            result={lookupResult}
            loading={lookupLoading}
            onLookup={handleLookup}
          />
        )}

        {activeTab === 'incidents' && (
          <IncidentsTab
            incidents={incidents}
            stats={incidentStats}
            loading={loadingIncidents}
            filter={incidentFilter}
            setFilter={setIncidentFilter}
            onRefresh={loadIncidents}
            onUpdateStatus={handleUpdateIncident}
          />
        )}
      </main>
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════════
// Market Prices Tab
// ═══════════════════════════════════════════════════════════════════════════════

interface MarketPricesTabProps {
  prices: MarketPrice[];
  loading: boolean;
  onRefresh: () => void;
  onLock: (period: string) => void;
  onUnlock: (period: string) => void;
  showAddForm: boolean;
  setShowAddForm: (show: boolean) => void;
  newPeriod: string;
  setNewPeriod: (period: string) => void;
  newPtf: number;
  setNewPtf: (ptf: number) => void;
  newYekdem: number;
  setNewYekdem: (yekdem: number) => void;
  newSourceNote: string;
  setNewSourceNote: (note: string) => void;
  onAdd: () => void;
}

function MarketPricesTab({
  prices, loading, onRefresh, onLock, onUnlock,
  showAddForm, setShowAddForm,
  newPeriod, setNewPeriod, newPtf, setNewPtf, newYekdem, setNewYekdem,
  newSourceNote, setNewSourceNote, onAdd
}: MarketPricesTabProps) {
  // Generate period options (last 12 months + next 3 months)
  const periodOptions = [];
  const now = new Date();
  for (let i = -3; i <= 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    periodOptions.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }

  return (
    <div className="space-y-6">
      {/* Actions */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">PTF/YEKDEM Referans Fiyatları</h2>
        <div className="flex gap-2">
          <button
            onClick={onRefresh}
            disabled={loading}
            className="btn-secondary flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Yenile
          </button>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="btn-primary flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Yeni Dönem
          </button>
        </div>
      </div>

      {/* Add Form */}
      {showAddForm && (
        <div className="card bg-primary-50 border-primary-200">
          <h3 className="font-medium text-gray-900 mb-4">Yeni Dönem Ekle / Güncelle</h3>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            <div>
              <label className="label">Dönem</label>
              <select
                className="input"
                value={newPeriod}
                onChange={(e) => setNewPeriod(e.target.value)}
              >
                <option value="">Seçin...</option>
                {periodOptions.map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">PTF (TL/MWh)</label>
              <input
                type="number"
                className="input"
                value={newPtf}
                onChange={(e) => setNewPtf(parseFloat(e.target.value) || 0)}
                step="0.1"
                min="0"
              />
            </div>
            <div>
              <label className="label">YEKDEM (TL/MWh)</label>
              <input
                type="number"
                className="input"
                value={newYekdem}
                onChange={(e) => setNewYekdem(parseFloat(e.target.value) || 0)}
                step="0.1"
                min="0"
              />
            </div>
            <div>
              <label className="label">Kaynak Notu</label>
              <input
                type="text"
                className="input"
                value={newSourceNote}
                onChange={(e) => setNewSourceNote(e.target.value)}
                placeholder="EPİAŞ, manuel, vb."
              />
            </div>
            <div className="flex items-end">
              <button onClick={onAdd} className="btn-primary w-full">
                Kaydet
              </button>
            </div>
          </div>
          
          {/* Validation warnings */}
          {newPtf > 0 && (newPtf < 500 || newPtf > 10000) && (
            <p className="text-xs text-amber-600 mt-2">
              ⚠️ PTF değeri olağandışı görünüyor (beklenen: 500-10000 TL/MWh)
            </p>
          )}
        </div>
      )}

      {/* Table */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Dönem</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">PTF (TL/MWh)</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">YEKDEM (TL/MWh)</th>
                <th className="text-center py-3 px-4 text-sm font-medium text-gray-500">Durum</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">İşlem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-500">
                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
                    Yükleniyor...
                  </td>
                </tr>
              ) : prices.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-500">
                    Henüz veri yok
                  </td>
                </tr>
              ) : (
                prices.map((price) => (
                  <tr key={price.period} className={price.is_locked ? 'bg-gray-50' : ''}>
                    <td className="py-3 px-4 text-sm font-medium text-gray-900">
                      {price.period}
                    </td>
                    <td className="py-3 px-4 text-sm text-right text-gray-900 font-mono">
                      {price.ptf_tl_per_mwh.toLocaleString('tr-TR', { minimumFractionDigits: 1 })}
                    </td>
                    <td className="py-3 px-4 text-sm text-right text-gray-900 font-mono">
                      {price.yekdem_tl_per_mwh.toLocaleString('tr-TR', { minimumFractionDigits: 1 })}
                    </td>
                    <td className="py-3 px-4 text-center">
                      {price.is_locked ? (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium bg-gray-200 text-gray-700">
                          <Lock className="w-3 h-3" />
                          Kilitli
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">
                          <Unlock className="w-3 h-3" />
                          Açık
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {price.is_locked ? (
                        <button
                          onClick={() => onUnlock(price.period)}
                          className="text-xs text-amber-600 hover:text-amber-700 font-medium"
                        >
                          Kilidi Kaldır
                        </button>
                      ) : (
                        <button
                          onClick={() => onLock(price.period)}
                          className="text-xs text-gray-600 hover:text-gray-700 font-medium"
                        >
                          Kilitle
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Distribution Tariffs Tab
// ═══════════════════════════════════════════════════════════════════════════════

interface DistributionTariffsTabProps {
  tariffs: DistributionTariff[];
  loading: boolean;
  onRefresh: () => void;
}

function DistributionTariffsTab({ tariffs, loading, onRefresh }: DistributionTariffsTabProps) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">EPDK Dağıtım Tarifeleri</h2>
          <p className="text-sm text-gray-500">Ocak 2025 tarifeleri (in-memory)</p>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="btn-secondary flex items-center gap-2"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Yenile
        </button>
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Tarife Grubu</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Gerilim</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Terim Tipi</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">Birim Fiyat (TL/kWh)</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Key</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-500">
                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
                    Yükleniyor...
                  </td>
                </tr>
              ) : tariffs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-gray-500">
                    Henüz veri yok
                  </td>
                </tr>
              ) : (
                tariffs.map((tariff, idx) => (
                  <tr key={idx}>
                    <td className="py-3 px-4 text-sm text-gray-900">{tariff.tariff_group}</td>
                    <td className="py-3 px-4 text-sm text-gray-900">{tariff.voltage_level}</td>
                    <td className="py-3 px-4 text-sm text-gray-900">{tariff.term_type}</td>
                    <td className="py-3 px-4 text-sm text-right text-gray-900 font-mono">
                      {tariff.unit_price_tl_per_kwh.toFixed(6)}
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-500 font-mono">{tariff.key}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// Tariff Lookup Tab
// ═══════════════════════════════════════════════════════════════════════════════

interface TariffLookupTabProps {
  group: string;
  setGroup: (g: string) => void;
  voltage: string;
  setVoltage: (v: string) => void;
  term: string;
  setTerm: (t: string) => void;
  result: TariffLookupResult | null;
  loading: boolean;
  onLookup: () => void;
}

function TariffLookupTab({
  group, setGroup, voltage, setVoltage, term, setTerm,
  result, loading, onLookup
}: TariffLookupTabProps) {
  const groups = ['Sanayi', 'Kamu', 'Ticarethane', 'Mesken', 'Tarımsal Sulama', 'Aydınlatma'];
  const voltages = ['AG', 'OG'];
  const terms = ['Tek Terim', 'Çift Terim'];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-900">Tarife Lookup Test</h2>
        <p className="text-sm text-gray-500">Tarife bilgilerine göre dağıtım birim fiyatını sorgula</p>
      </div>

      <div className="card">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div>
            <label className="label">Tarife Grubu</label>
            <select className="input" value={group} onChange={(e) => setGroup(e.target.value)}>
              {groups.map(g => <option key={g} value={g}>{g}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Gerilim Seviyesi</label>
            <select className="input" value={voltage} onChange={(e) => setVoltage(e.target.value)}>
              {voltages.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Terim Tipi</label>
            <select className="input" value={term} onChange={(e) => setTerm(e.target.value)}>
              {terms.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex items-end">
            <button
              onClick={onLookup}
              disabled={loading}
              className="btn-primary w-full flex items-center justify-center gap-2"
            >
              {loading ? (
                <RefreshCw className="w-4 h-4 animate-spin" />
              ) : (
                <Search className="w-4 h-4" />
              )}
              Sorgula
            </button>
          </div>
        </div>
      </div>

      {result && (
        <div className={`card ${result.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
          <h3 className="font-medium text-gray-900 mb-4">Sonuç</h3>
          
          {result.success ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-gray-500">Birim Fiyat</p>
                  <p className="text-2xl font-bold text-green-700 font-mono">
                    {result.unit_price_tl_per_kwh?.toFixed(6)} TL/kWh
                  </p>
                </div>
                <div>
                  <p className="text-sm text-gray-500">Tarife Key</p>
                  <p className="text-lg font-medium text-gray-900 font-mono">
                    {result.tariff_key}
                  </p>
                </div>
              </div>
              <div className="pt-3 border-t border-green-200">
                <p className="text-sm text-gray-500">Normalize Edilmiş Değerler:</p>
                <p className="text-sm text-gray-700">
                  Grup: <span className="font-medium">{result.normalized.tariff_group}</span> | 
                  Gerilim: <span className="font-medium">{result.normalized.voltage_level}</span> | 
                  Terim: <span className="font-medium">{result.normalized.term_type}</span>
                </p>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
              <div>
                <p className="text-sm text-red-700 font-medium">Tarife bulunamadı</p>
                {result.error_message && (
                  <p className="text-sm text-red-600 mt-1">{result.error_message}</p>
                )}
                <p className="text-xs text-gray-500 mt-2">
                  Aranan: {result.normalized.tariff_group} / {result.normalized.voltage_level} / {result.normalized.term_type}
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ═══════════════════════════════════════════════════════════════════════════════
// Incidents Tab (Sprint 3)
// ═══════════════════════════════════════════════════════════════════════════════

interface IncidentsTabProps {
  incidents: Incident[];
  stats: IncidentStatsResponse | null;
  loading: boolean;
  filter: { status?: string; severity?: string };
  setFilter: (f: { status?: string; severity?: string }) => void;
  onRefresh: () => void;
  onUpdateStatus: (id: number, status: 'OPEN' | 'ACK' | 'RESOLVED') => void;
}

function IncidentsTab({
  incidents, stats, loading, filter, setFilter, onRefresh, onUpdateStatus
}: IncidentsTabProps) {
  const severityColors: Record<string, string> = {
    S1: 'bg-red-100 text-red-800 border-red-200',
    S2: 'bg-orange-100 text-orange-800 border-orange-200',
    S3: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    S4: 'bg-blue-100 text-blue-800 border-blue-200',
  };

  const statusColors: Record<string, string> = {
    OPEN: 'bg-red-100 text-red-700',
    ACK: 'bg-yellow-100 text-yellow-700',
    RESOLVED: 'bg-green-100 text-green-700',
  };

  return (
    <div className="space-y-6">
      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="card bg-red-50 border-red-200">
            <p className="text-sm text-red-600">Açık (OPEN)</p>
            <p className="text-2xl font-bold text-red-700">{stats.by_status?.OPEN || 0}</p>
          </div>
          <div className="card bg-yellow-50 border-yellow-200">
            <p className="text-sm text-yellow-600">İnceleniyor (ACK)</p>
            <p className="text-2xl font-bold text-yellow-700">{stats.by_status?.ACK || 0}</p>
          </div>
          <div className="card bg-green-50 border-green-200">
            <p className="text-sm text-green-600">Çözüldü</p>
            <p className="text-2xl font-bold text-green-700">{stats.by_status?.RESOLVED || 0}</p>
          </div>
          <div className="card">
            <p className="text-sm text-gray-600">Toplam</p>
            <p className="text-2xl font-bold text-gray-900">{stats.total || 0}</p>
          </div>
        </div>
      )}

      {/* Filters & Actions */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-4">
          <div>
            <label className="label text-xs">Durum</label>
            <select
              className="input py-1 text-sm"
              value={filter.status || ''}
              onChange={(e) => setFilter({ ...filter, status: e.target.value || undefined })}
            >
              <option value="">Tümü</option>
              <option value="OPEN">Açık</option>
              <option value="ACK">İnceleniyor</option>
              <option value="RESOLVED">Çözüldü</option>
            </select>
          </div>
          <div>
            <label className="label text-xs">Severity</label>
            <select
              className="input py-1 text-sm"
              value={filter.severity || ''}
              onChange={(e) => setFilter({ ...filter, severity: e.target.value || undefined })}
            >
              <option value="">Tümü</option>
              <option value="S1">S1 - Kritik</option>
              <option value="S2">S2 - Yüksek</option>
              <option value="S3">S3 - Orta</option>
              <option value="S4">S4 - Düşük</option>
            </select>
          </div>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="btn-secondary flex items-center gap-2"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Yenile
        </button>
      </div>

      {/* Incidents List */}
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">ID</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Severity</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Kategori</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Mesaj</th>
                <th className="text-center py-3 px-4 text-sm font-medium text-gray-500">Tekrar</th>
                <th className="text-center py-3 px-4 text-sm font-medium text-gray-500">Durum</th>
                <th className="text-left py-3 px-4 text-sm font-medium text-gray-500">Son Görülme</th>
                <th className="text-right py-3 px-4 text-sm font-medium text-gray-500">İşlem</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-gray-500">
                    <RefreshCw className="w-6 h-6 animate-spin mx-auto mb-2" />
                    Yükleniyor...
                  </td>
                </tr>
              ) : incidents.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-gray-500">
                    <CheckCircle className="w-8 h-8 mx-auto mb-2 text-green-500" />
                    Incident bulunamadı
                  </td>
                </tr>
              ) : (
                incidents.map((incident) => (
                  <tr key={incident.id} className={incident.status === 'OPEN' ? 'bg-red-50/30' : ''}>
                    <td className="py-3 px-4 text-sm font-mono text-gray-600">
                      #{incident.id}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`inline-flex px-2 py-1 rounded text-xs font-medium border ${severityColors[incident.severity] || 'bg-gray-100'}`}>
                        {incident.severity}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-900">
                      {incident.category}
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-700 max-w-xs truncate" title={incident.message}>
                      {incident.message}
                    </td>
                    <td className="py-3 px-4 text-center">
                      {(incident.occurrence_count || 1) > 1 ? (
                        <span 
                          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800"
                          title={`İlk: ${incident.first_seen_at ? new Date(incident.first_seen_at).toLocaleString('tr-TR') : '-'}`}
                        >
                          {incident.occurrence_count}×
                        </span>
                      ) : (
                        <span className="text-xs text-gray-400">1×</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-center">
                      <span className={`inline-flex px-2 py-1 rounded-full text-xs font-medium ${statusColors[incident.status]}`}>
                        {incident.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-500">
                      {incident.last_seen_at 
                        ? new Date(incident.last_seen_at).toLocaleString('tr-TR')
                        : new Date(incident.created_at).toLocaleString('tr-TR')}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {incident.status === 'OPEN' && (
                          <button
                            onClick={() => onUpdateStatus(incident.id, 'ACK')}
                            className="p-1 hover:bg-yellow-100 rounded text-yellow-600"
                            title="İncele"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                        )}
                        {incident.status !== 'RESOLVED' && (
                          <button
                            onClick={() => onUpdateStatus(incident.id, 'RESOLVED')}
                            className="p-1 hover:bg-green-100 rounded text-green-600"
                            title="Çözüldü"
                          >
                            <CheckSquare className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Severity Legend */}
      <div className="text-xs text-gray-500 flex items-center gap-4">
        <span className="font-medium">Severity:</span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-red-200"></span> S1 - Kritik (hesaplama yapılamadı)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-orange-200"></span> S2 - Yüksek (yanlış hesaplama riski)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-yellow-200"></span> S3 - Orta (uyarı)
        </span>
        <span className="flex items-center gap-1">
          <span className="w-3 h-3 rounded bg-blue-200"></span> S4 - Düşük (bilgi)
        </span>
      </div>
    </div>
  );
}
