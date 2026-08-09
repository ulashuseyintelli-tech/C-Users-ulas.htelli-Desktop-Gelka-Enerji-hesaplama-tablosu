// =============================================================================
// S1 — CRM Core shell/navigation.
// =============================================================================
//
// Mevcut App.tsx boolean-state "sayfa" deseninin (showAdminPanel/showReconPage
// → AdminPanel/ReconPage, onBack prop'lu) ÜÇÜNCÜ örneği. React Router
// eklenmedi (owner kararı, S1 preflight) — kendi içindeki 4 sekme de
// AdminPanel.tsx'teki activeTab + border-b-2 deseninin birebir taklididir.
//
// Kapsam (S1 kilitli tanım): Bugün / Müşteriler / Teklifler / Sözleşmeler.
// Activity/Task/Prospect/Outreach/Pipeline/Opportunity/Notification YOK.
// =============================================================================

import { useState } from 'react';
import { ArrowLeft, LayoutDashboard, Users, FileText, FileCheck } from 'lucide-react';
import { TodayScreen } from './TodayScreen';
import { CustomersScreen } from './CustomersScreen';
import { OffersScreen } from './OffersScreen';
import { ContractsScreen } from './ContractsScreen';
import type { Subject } from './crmApi';

type CrmTab = 'today' | 'customers' | 'offers' | 'contracts';

interface CrmCorePageProps {
  onBack: () => void;
}

export default function CrmCorePage({ onBack }: CrmCorePageProps) {
  const [activeTab, setActiveTab] = useState<CrmTab>('today');
  // Müşteri Detay ekranı (WB-5) bu sayfanın içinde açılır — "Sözleşmeler"
  // sekmesine geçiş de customer_id ile filtrelenmiş liste göstermek için
  // aynı mekanizmayı (state, yeni route değil) kullanır.
  const [selectedCustomerId, setSelectedCustomerId] = useState<number | null>(null);

  // Today ekranındaki "Kaydı Aç" quick action'ı — Customer'a deep-link
  // (detay sayfası açılır); Offer/Contract'ın kendi detay sayfası S1'de
  // hiç eklenmediği için (yalnız liste), en yakın karşılığı ilgili
  // sekmeye geçmek (owner talimatı bunu S2'nin scope'unu genişletmeden
  // karşılayan minimal davranış).
  const handleOpenSubject = (subject: Subject) => {
    if (subject.customer_id !== undefined) {
      setActiveTab('customers');
      setSelectedCustomerId(subject.customer_id);
    } else if (subject.offer_id !== undefined) {
      setActiveTab('offers');
    } else if (subject.contract_id !== undefined) {
      setActiveTab('contracts');
    }
  };

  const tabs: Array<{ key: CrmTab; label: string; icon: typeof LayoutDashboard }> = [
    { key: 'today', label: 'Bugün', icon: LayoutDashboard },
    { key: 'customers', label: 'Müşteriler', icon: Users },
    { key: 'offers', label: 'Teklifler', icon: FileText },
    { key: 'contracts', label: 'Sözleşmeler', icon: FileCheck },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center gap-3">
            <button onClick={onBack} className="p-2 hover:bg-gray-100 rounded-lg" aria-label="Ana sayfaya dön">
              <ArrowLeft className="w-5 h-5 text-gray-600" />
            </button>
            <div className="w-10 h-10 bg-primary-600 rounded-lg flex items-center justify-center">
              <Users className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-900">CRM Core</h1>
              <p className="text-sm text-gray-500">Müşteriler, teklifler ve sözleşmeler — tek bakışta</p>
            </div>
          </div>
        </div>
      </header>

      {/* Tabs */}
      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-6">
          <nav className="flex gap-6">
            {tabs.map(({ key, label, icon: Icon }) => (
              <button
                key={key}
                onClick={() => {
                  setActiveTab(key);
                  setSelectedCustomerId(null); // sekme değişince müşteri detayından çık
                }}
                className={`py-4 px-2 border-b-2 font-medium text-sm transition-colors ${
                  activeTab === key
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                <Icon className="w-4 h-4 inline mr-2" />
                {label}
              </button>
            ))}
          </nav>
        </div>
      </div>

      {/* Content */}
      <main className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'today' && <TodayScreen onOpenSubject={handleOpenSubject} />}
        {activeTab === 'customers' && (
          <CustomersScreen
            selectedCustomerId={selectedCustomerId}
            onSelectCustomer={setSelectedCustomerId}
            onCloseDetail={() => setSelectedCustomerId(null)}
          />
        )}
        {activeTab === 'offers' && <OffersScreen />}
        {activeTab === 'contracts' && <ContractsScreen />}
      </main>
    </div>
  );
}
