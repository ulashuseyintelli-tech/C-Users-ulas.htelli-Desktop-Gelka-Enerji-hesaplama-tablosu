"""
PDSMR-R4 / FAZ 4D2 — DETERMINISTIK SENTETIK LEGACY TEST FIXTURE.

NEDEN VAR: Faz 4B2 ve 4C1 test paketleri canli production DB'sini fixture
KAYNAGI olarak kullaniyor ve onun parmak izine KALIBRE idi. Faz 4C2
production'i canonical head'e tasiyinca bu paketler SESSIZCE skip'e dustu
(119 test) — "0 failed" gorunuyordu ama KAPSAM KAYBOLMUSTU.

BU MODUL O BAGIMLILIGI KALDIRIR. Fixture:
  - SIFIRDAN, programatik olarak uretilir,
  - YALNIZ sentetik veri tasir (gercek musteri/teklif degeri YOK),
  - production veya recovery DB'sinden KOPYALANMAZ/TURETILMEZ,
  - deterministiktir (ayni girdi -> ayni yapisal parmak izi).

SEMA PROVENANCE: asagidaki DDL manifesti, Faz 4C2 recovery kopyasinin TEK
SEFERLIK, SALT-OKUNUR `sqlite_master` incelemesinden cikarilmistir. YALNIZ
DDL METNI alinmistir — hicbir satir/veri/PII okunmamistir. Manifest gozden
gecirilebilir duz metindir; binary DB artefakti DEGILDIR.

Bu modul URETIM kodundan CAGRILMAZ; yalnizca testler kullanir.

Cagrildigi yerler:
- tests/test_pdsmr_r4b2_unversioned_adoption.py [PDSMR-R4/Faz4D2]
- tests/test_pdsmr_r4c1_production_controller.py [PDSMR-R4/Faz4D2]
- tests/test_pdsmr_r4d2_fixture_provenance.py [PDSMR-R4/Faz4D2]
"""
from __future__ import annotations

import hashlib
import os
import sqlite3

# Determinizm icin SABIT zaman damgasi — datetime.now() KULLANILMAZ.
SABIT_ZAMAN = "2026-01-15 09:00:00"

# Fixture'in KENDI beklenen satir sayilari. Production'daki 2/2/0'i TAKLIT
# ETMEZ; testler bu degerlere baglanir (owner karari, Faz 4D2 madde 4).
BEKLENEN_SATIRLAR = {
    "customers": 3,
    "offers": 5,
    "market_reference_prices": 4,
    "incidents": 0,
}

# LEGACY SEMA MANIFESTI (DDL-only, 29 tablo)
LEGACY_TABLES: tuple[tuple[str, str], ...] = (
    ('activities',
     'CREATE TABLE activities ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, offer_id INTEGER, contract_id INTEGER, activity_type VARCHAR(30) NOT NULL, title VARCHAR(255), body TEXT, occurred_at DATETIME NOT NULL, created_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id), FOREIGN KEY(offer_id) REFERENCES offers (id), FOREIGN KEY(contract_id) REFERENCES contracts (id) )'),
    ('analysis_cache',
     'CREATE TABLE analysis_cache ( id INTEGER NOT NULL, cache_key VARCHAR(64) NOT NULL, customer_id VARCHAR(100) NOT NULL, period VARCHAR(7) NOT NULL, params_hash VARCHAR(64) NOT NULL, result_json TEXT NOT NULL, created_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, hit_count INTEGER NOT NULL, PRIMARY KEY (id), UNIQUE (cache_key) )'),
    ('audit_logs',
     'CREATE TABLE audit_logs ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, actor_type VARCHAR(50) NOT NULL, actor_id VARCHAR(100), action VARCHAR(20) NOT NULL, target_type VARCHAR(50), target_id VARCHAR(100), details_json JSON, ip_address VARCHAR(45), user_agent VARCHAR(500), created_at DATETIME, PRIMARY KEY (id) )'),
    ('consumption_hourly_data',
     'CREATE TABLE consumption_hourly_data ( id INTEGER NOT NULL, profile_id INTEGER NOT NULL, date VARCHAR(10) NOT NULL, hour INTEGER NOT NULL, consumption_kwh FLOAT NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_consumption_hourly UNIQUE (profile_id, date, hour), FOREIGN KEY(profile_id) REFERENCES consumption_profiles (id) ON DELETE CASCADE )'),
    ('consumption_profiles',
     'CREATE TABLE consumption_profiles ( id INTEGER NOT NULL, customer_id VARCHAR(100) NOT NULL, customer_name VARCHAR(255), period VARCHAR(7) NOT NULL, profile_type VARCHAR(20) NOT NULL, template_name VARCHAR(100), total_kwh FLOAT NOT NULL, source VARCHAR(30) NOT NULL, version INTEGER NOT NULL, is_active INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_consumption_customer_period_version UNIQUE (customer_id, period, version) )'),
    ('contracts',
     'CREATE TABLE contracts ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, offer_id INTEGER NOT NULL, legal_profile_id INTEGER, authorized_representative_id INTEGER, contract_number VARCHAR(50), status VARCHAR(30) NOT NULL, start_date DATETIME, end_date DATETIME, template_version VARCHAR(20), extraction_snapshot_json JSON, contract_snapshot_json JSON, pdf_storage_ref VARCHAR(500), pdf_sha256 VARCHAR(64), created_by VARCHAR(100), created_at DATETIME, finalized_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id), FOREIGN KEY(offer_id) REFERENCES offers (id), FOREIGN KEY(legal_profile_id) REFERENCES customer_legal_profiles (id), FOREIGN KEY(authorized_representative_id) REFERENCES customer_authorized_representatives (id) )'),
    ('customer_authorized_representatives',
     'CREATE TABLE customer_authorized_representatives ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, legal_profile_id INTEGER, full_name VARCHAR(255) NOT NULL, national_id VARCHAR(11) NOT NULL, authority_type VARCHAR(50), authority_scope VARCHAR(255), authority_start_date DATETIME, authority_end_date DATETIME, is_indefinite BOOLEAN NOT NULL, source_document_id INTEGER, verification_status VARCHAR(20) NOT NULL, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id), FOREIGN KEY(legal_profile_id) REFERENCES customer_legal_profiles (id), FOREIGN KEY(source_document_id) REFERENCES uploaded_reference_documents (id) )'),
    ('customer_legal_profiles',
     'CREATE TABLE customer_legal_profiles ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, legal_name VARCHAR(255) NOT NULL, tax_number VARCHAR(20) NOT NULL, tax_office VARCHAR(255) NOT NULL, mersis_number VARCHAR(32), trade_registry_number VARCHAR(64), registered_address TEXT NOT NULL, facility_address TEXT, notification_address TEXT, verification_status VARCHAR(20) NOT NULL, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id) )'),
    ('customers',
     'CREATE TABLE customers ( id INTEGER NOT NULL, name VARCHAR(255) NOT NULL, company VARCHAR(255), email VARCHAR(255), phone VARCHAR(50), address TEXT, notes TEXT, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id) )'),
    ('data_versions',
     'CREATE TABLE data_versions ( id INTEGER NOT NULL, data_type VARCHAR(30) NOT NULL, period VARCHAR(7) NOT NULL, customer_id VARCHAR(100), version INTEGER NOT NULL, uploaded_by VARCHAR(100), upload_filename VARCHAR(255), row_count INTEGER NOT NULL, quality_score INTEGER, is_active INTEGER NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_data_version UNIQUE (data_type, period, customer_id, version) )'),
    ('distribution_tariffs',
     'CREATE TABLE distribution_tariffs ( id INTEGER NOT NULL, valid_from VARCHAR(10) NOT NULL, valid_to VARCHAR(10), tariff_group VARCHAR(20) NOT NULL, voltage_level VARCHAR(5) NOT NULL, term_type VARCHAR(20) NOT NULL, unit_price_tl_per_kwh FLOAT NOT NULL, source_note VARCHAR(500), created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id) )'),
    ('document_extraction_runs',
     'CREATE TABLE document_extraction_runs ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, document_id INTEGER NOT NULL, extractor_type VARCHAR(50) NOT NULL, extractor_version VARCHAR(20) NOT NULL, model_name VARCHAR(50) NOT NULL, prompt_version VARCHAR(20) NOT NULL, status VARCHAR(20) NOT NULL, started_at DATETIME, completed_at DATETIME, raw_response_ref VARCHAR(500), error_code VARCHAR(100), PRIMARY KEY (id), FOREIGN KEY(document_id) REFERENCES uploaded_reference_documents (id) )'),
    ('document_field_candidates',
     'CREATE TABLE document_field_candidates ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, extraction_run_id INTEGER NOT NULL, document_id INTEGER NOT NULL, field_name VARCHAR(100) NOT NULL, raw_value TEXT, normalized_value TEXT, confidence FLOAT NOT NULL, source_page INTEGER NOT NULL, source_text TEXT, validation_status VARCHAR(20) NOT NULL, conflict_status VARCHAR(20) NOT NULL, user_decision VARCHAR(20), corrected_value TEXT, decided_by VARCHAR(100), decided_at DATETIME, created_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(extraction_run_id) REFERENCES document_extraction_runs (id), FOREIGN KEY(document_id) REFERENCES uploaded_reference_documents (id) )'),
    ('hourly_market_prices',
     'CREATE TABLE hourly_market_prices ( id INTEGER NOT NULL, period VARCHAR(7) NOT NULL, date VARCHAR(10) NOT NULL, hour INTEGER NOT NULL, ptf_tl_per_mwh FLOAT NOT NULL, smf_tl_per_mwh FLOAT NOT NULL, currency VARCHAR(3) NOT NULL, source VARCHAR(30) NOT NULL, version INTEGER NOT NULL, is_active INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_hourly_period_date_hour_version UNIQUE (period, date, hour, version) )'),
    ('incidents',
     'CREATE TABLE incidents ( id INTEGER NOT NULL, trace_id VARCHAR(50) NOT NULL, tenant_id VARCHAR(50) NOT NULL, invoice_id VARCHAR(50), offer_id INTEGER, provider VARCHAR(100), period VARCHAR(7), severity VARCHAR(5) NOT NULL, category VARCHAR(50) NOT NULL, message VARCHAR(1000) NOT NULL, details_json JSON, primary_flag VARCHAR(50), action_type VARCHAR(30), action_owner VARCHAR(30), action_code VARCHAR(50), all_flags JSON, secondary_flags JSON, deduction_total INTEGER, routed_payload JSON, dedupe_key VARCHAR(64), dedupe_bucket INTEGER, occurrence_count INTEGER NOT NULL, first_seen_at DATETIME, last_seen_at DATETIME, retry_attempt_count INTEGER, retry_eligible_at DATETIME, retry_last_attempt_at DATETIME, retry_lock_until DATETIME, retry_lock_by VARCHAR(100), retry_exhausted_at DATETIME, external_issue_id VARCHAR(100), external_issue_url VARCHAR(500), reported_at DATETIME, reclassified_at DATETIME, previous_primary_flag VARCHAR(50), recompute_count INTEGER, retry_success BOOLEAN, resolution_reason VARCHAR(50), feedback_json JSON, status VARCHAR(20) NOT NULL, resolution_note VARCHAR(1000), resolved_by VARCHAR(100), resolved_at DATETIME, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id) )'),
    ('invoices',
     'CREATE TABLE invoices ( id VARCHAR(36) NOT NULL, tenant_id VARCHAR(64) NOT NULL, source_filename VARCHAR(255) NOT NULL, content_type VARCHAR(100) NOT NULL, storage_original_ref VARCHAR(700) NOT NULL, storage_page1_ref VARCHAR(700), file_hash VARCHAR(64), vendor_guess VARCHAR(50), invoice_period VARCHAR(10), extraction_json JSON, validation_json JSON, status VARCHAR(11), error_message TEXT, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id) )'),
    ('jobs',
     'CREATE TABLE jobs ( id VARCHAR(36) NOT NULL, tenant_id VARCHAR(64) NOT NULL, invoice_id VARCHAR(36), job_type VARCHAR(20) NOT NULL, status VARCHAR(9), payload_json JSON, result_json JSON, error VARCHAR(2000), created_at DATETIME, started_at DATETIME, finished_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(invoice_id) REFERENCES invoices (id) )'),
    ('market_reference_prices',
     'CREATE TABLE market_reference_prices ( id INTEGER NOT NULL, price_type VARCHAR(20) NOT NULL, period VARCHAR(7) NOT NULL, ptf_tl_per_mwh FLOAT NOT NULL, yekdem_tl_per_mwh FLOAT NOT NULL, status VARCHAR(20) NOT NULL, source VARCHAR(30) NOT NULL, captured_at DATETIME NOT NULL, source_note VARCHAR(500), change_reason TEXT, is_locked INTEGER, updated_by VARCHAR(100), created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), CONSTRAINT uq_market_reference_prices_price_type_period UNIQUE (price_type, period) )'),
    ('monthly_yekdem_prices',
     'CREATE TABLE monthly_yekdem_prices ( id INTEGER NOT NULL, period VARCHAR(7) NOT NULL, yekdem_tl_per_mwh FLOAT NOT NULL, source VARCHAR(30) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id) )'),
    ('offers',
     'CREATE TABLE offers ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, vendor VARCHAR(50), invoice_period VARCHAR(10), consumption_kwh FLOAT NOT NULL, current_unit_price FLOAT NOT NULL, distribution_unit_price FLOAT, demand_qty FLOAT, demand_unit_price FLOAT, weighted_ptf FLOAT NOT NULL, yekdem FLOAT NOT NULL, agreement_multiplier FLOAT NOT NULL, current_total FLOAT NOT NULL, offer_total FLOAT NOT NULL, savings_amount FLOAT NOT NULL, savings_ratio FLOAT NOT NULL, extra_items_json JSON, extra_items_total_tl FLOAT, calculation_result JSON, extraction_result JSON, created_at DATETIME, pdf_ref VARCHAR(700), status VARCHAR(50), PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id) )'),
    ('price_change_history',
     'CREATE TABLE price_change_history ( id INTEGER NOT NULL, price_record_id INTEGER NOT NULL, price_type VARCHAR(20) NOT NULL, period VARCHAR(7) NOT NULL, action VARCHAR(10) NOT NULL, old_value FLOAT, new_value FLOAT NOT NULL, old_status VARCHAR(20), new_status VARCHAR(20) NOT NULL, change_reason TEXT, updated_by VARCHAR(100), source VARCHAR(30), created_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(price_record_id) REFERENCES market_reference_prices (id) ON DELETE RESTRICT )'),
    ('profile_templates',
     'CREATE TABLE profile_templates ( id INTEGER NOT NULL, name VARCHAR(100) NOT NULL, display_name VARCHAR(200) NOT NULL, description TEXT, hourly_weights TEXT NOT NULL, is_builtin INTEGER NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (name) )'),
    ('prospect_companies',
     'CREATE TABLE prospect_companies ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, legal_name VARCHAR(255), trade_name VARCHAR(255), normalized_name VARCHAR(255), website VARCHAR(500), normalized_domain VARCHAR(255), sector VARCHAR(255), city VARCHAR(100), district VARCHAR(100), industrial_zone VARCHAR(255), address TEXT, phone VARCHAR(50), status VARCHAR(20) NOT NULL, qualification_reason VARCHAR(50), qualification_note TEXT, duplicate_of_id INTEGER, customer_id INTEGER, discovered_at DATETIME, last_verified_at DATETIME, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(duplicate_of_id) REFERENCES prospect_companies (id), FOREIGN KEY(customer_id) REFERENCES customers (id) )'),
    ('prospect_contacts',
     'CREATE TABLE prospect_contacts ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, prospect_company_id INTEGER NOT NULL, full_name VARCHAR(255), job_title VARCHAR(255), email VARCHAR(255), phone VARCHAR(50), contact_type VARCHAR(30) NOT NULL, verification_status VARCHAR(30) NOT NULL, source_id INTEGER, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(prospect_company_id) REFERENCES prospect_companies (id), FOREIGN KEY(source_id) REFERENCES prospect_sources (id) )'),
    ('prospect_sources',
     'CREATE TABLE prospect_sources ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, prospect_company_id INTEGER NOT NULL, source_url VARCHAR(1000) NOT NULL, source_type VARCHAR(30) NOT NULL, source_title VARCHAR(500), evidence_text TEXT, content_hash VARCHAR(64), fetch_status VARCHAR(30) NOT NULL, discovered_at DATETIME, last_checked_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(prospect_company_id) REFERENCES prospect_companies (id) )'),
    ('tasks',
     'CREATE TABLE tasks ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, offer_id INTEGER, contract_id INTEGER, title VARCHAR(255) NOT NULL, description TEXT, due_at DATETIME, status VARCHAR(20) NOT NULL, completed_at DATETIME, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customers (id), FOREIGN KEY(offer_id) REFERENCES offers (id), FOREIGN KEY(contract_id) REFERENCES contracts (id) )'),
    ('uploaded_reference_documents',
     'CREATE TABLE uploaded_reference_documents ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, customer_id INTEGER, document_type VARCHAR(30) NOT NULL, original_filename VARCHAR(500) NOT NULL, mime_type VARCHAR(100) NOT NULL, file_size INTEGER NOT NULL, sha256 VARCHAR(64) NOT NULL, storage_ref VARCHAR(500) NOT NULL, document_date DATETIME, processing_status VARCHAR(20) NOT NULL, uploaded_at DATETIME, PRIMARY KEY (id), CONSTRAINT uq_reference_doc_dedup UNIQUE (tenant_id, customer_id, sha256, document_type), FOREIGN KEY(customer_id) REFERENCES customers (id) )'),
    ('webhook_configs',
     'CREATE TABLE webhook_configs ( id INTEGER NOT NULL, tenant_id VARCHAR(64) NOT NULL, url VARCHAR(2000) NOT NULL, events JSON NOT NULL, secret VARCHAR(255), headers_json JSON, is_active INTEGER, last_triggered_at DATETIME, success_count INTEGER, failure_count INTEGER, created_at DATETIME, updated_at DATETIME, PRIMARY KEY (id) )'),
    ('webhook_deliveries',
     'CREATE TABLE webhook_deliveries ( id INTEGER NOT NULL, webhook_config_id INTEGER, event_type VARCHAR(100) NOT NULL, payload_json JSON NOT NULL, status VARCHAR(20) NOT NULL, response_status_code INTEGER, response_body TEXT, error_message TEXT, attempt_count INTEGER, next_retry_at DATETIME, created_at DATETIME, delivered_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(webhook_config_id) REFERENCES webhook_configs (id) )'),
)

# LEGACY INDEX MANIFESTI (116 index)
LEGACY_INDEXES: tuple[tuple[str, str], ...] = (
    ('idx_cache_customer_period',
     'CREATE INDEX idx_cache_customer_period ON analysis_cache (customer_id, period)'),
    ('idx_cache_expires',
     'CREATE INDEX idx_cache_expires ON analysis_cache (expires_at)'),
    ('idx_consumption_active',
     'CREATE INDEX idx_consumption_active ON consumption_profiles (customer_id, period, is_active)'),
    ('idx_data_versions_lookup',
     'CREATE INDEX idx_data_versions_lookup ON data_versions (data_type, period, customer_id)'),
    ('idx_hourly_market_date_hour',
     'CREATE INDEX idx_hourly_market_date_hour ON hourly_market_prices (date, hour)'),
    ('idx_hourly_market_period_active',
     'CREATE INDEX idx_hourly_market_period_active ON hourly_market_prices (period, is_active)'),
    ('ix_activities_activity_type',
     'CREATE INDEX ix_activities_activity_type ON activities (activity_type)'),
    ('ix_activities_contract_id',
     'CREATE INDEX ix_activities_contract_id ON activities (contract_id)'),
    ('ix_activities_customer_id',
     'CREATE INDEX ix_activities_customer_id ON activities (customer_id)'),
    ('ix_activities_id',
     'CREATE INDEX ix_activities_id ON activities (id)'),
    ('ix_activities_offer_id',
     'CREATE INDEX ix_activities_offer_id ON activities (offer_id)'),
    ('ix_activities_tenant_id',
     'CREATE INDEX ix_activities_tenant_id ON activities (tenant_id)'),
    ('ix_analysis_cache_id',
     'CREATE INDEX ix_analysis_cache_id ON analysis_cache (id)'),
    ('ix_audit_logs_created_at',
     'CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at)'),
    ('ix_audit_logs_id',
     'CREATE INDEX ix_audit_logs_id ON audit_logs (id)'),
    ('ix_audit_logs_tenant_id',
     'CREATE INDEX ix_audit_logs_tenant_id ON audit_logs (tenant_id)'),
    ('ix_consumption_hourly_data_id',
     'CREATE INDEX ix_consumption_hourly_data_id ON consumption_hourly_data (id)'),
    ('ix_consumption_hourly_data_profile_id',
     'CREATE INDEX ix_consumption_hourly_data_profile_id ON consumption_hourly_data (profile_id)'),
    ('ix_consumption_profiles_customer_id',
     'CREATE INDEX ix_consumption_profiles_customer_id ON consumption_profiles (customer_id)'),
    ('ix_consumption_profiles_id',
     'CREATE INDEX ix_consumption_profiles_id ON consumption_profiles (id)'),
    ('ix_consumption_profiles_period',
     'CREATE INDEX ix_consumption_profiles_period ON consumption_profiles (period)'),
    ('ix_contracts_contract_number',
     'CREATE UNIQUE INDEX ix_contracts_contract_number ON contracts (contract_number)'),
    ('ix_contracts_customer_id',
     'CREATE INDEX ix_contracts_customer_id ON contracts (customer_id)'),
    ('ix_contracts_id',
     'CREATE INDEX ix_contracts_id ON contracts (id)'),
    ('ix_contracts_offer_id',
     'CREATE INDEX ix_contracts_offer_id ON contracts (offer_id)'),
    ('ix_contracts_status',
     'CREATE INDEX ix_contracts_status ON contracts (status)'),
    ('ix_contracts_tenant_id',
     'CREATE INDEX ix_contracts_tenant_id ON contracts (tenant_id)'),
    ('ix_customer_authorized_representatives_customer_id',
     'CREATE INDEX ix_customer_authorized_representatives_customer_id ON customer_authorized_representatives (customer_id)'),
    ('ix_customer_authorized_representatives_id',
     'CREATE INDEX ix_customer_authorized_representatives_id ON customer_authorized_representatives (id)'),
    ('ix_customer_authorized_representatives_legal_profile_id',
     'CREATE INDEX ix_customer_authorized_representatives_legal_profile_id ON customer_authorized_representatives (legal_profile_id)'),
    ('ix_customer_authorized_representatives_tenant_id',
     'CREATE INDEX ix_customer_authorized_representatives_tenant_id ON customer_authorized_representatives (tenant_id)'),
    ('ix_customer_authorized_representatives_verification_status',
     'CREATE INDEX ix_customer_authorized_representatives_verification_status ON customer_authorized_representatives (verification_status)'),
    ('ix_customer_legal_profiles_customer_id',
     'CREATE INDEX ix_customer_legal_profiles_customer_id ON customer_legal_profiles (customer_id)'),
    ('ix_customer_legal_profiles_id',
     'CREATE INDEX ix_customer_legal_profiles_id ON customer_legal_profiles (id)'),
    ('ix_customer_legal_profiles_tenant_id',
     'CREATE INDEX ix_customer_legal_profiles_tenant_id ON customer_legal_profiles (tenant_id)'),
    ('ix_customer_legal_profiles_verification_status',
     'CREATE INDEX ix_customer_legal_profiles_verification_status ON customer_legal_profiles (verification_status)'),
    ('ix_customers_id',
     'CREATE INDEX ix_customers_id ON customers (id)'),
    ('ix_customers_name',
     'CREATE INDEX ix_customers_name ON customers (name)'),
    ('ix_data_versions_id',
     'CREATE INDEX ix_data_versions_id ON data_versions (id)'),
    ('ix_distribution_tariffs_id',
     'CREATE INDEX ix_distribution_tariffs_id ON distribution_tariffs (id)'),
    ('ix_distribution_tariffs_valid_from',
     'CREATE INDEX ix_distribution_tariffs_valid_from ON distribution_tariffs (valid_from)'),
    ('ix_document_extraction_runs_document_id',
     'CREATE INDEX ix_document_extraction_runs_document_id ON document_extraction_runs (document_id)'),
    ('ix_document_extraction_runs_id',
     'CREATE INDEX ix_document_extraction_runs_id ON document_extraction_runs (id)'),
    ('ix_document_extraction_runs_status',
     'CREATE INDEX ix_document_extraction_runs_status ON document_extraction_runs (status)'),
    ('ix_document_extraction_runs_tenant_id',
     'CREATE INDEX ix_document_extraction_runs_tenant_id ON document_extraction_runs (tenant_id)'),
    ('ix_document_field_candidates_conflict_status',
     'CREATE INDEX ix_document_field_candidates_conflict_status ON document_field_candidates (conflict_status)'),
    ('ix_document_field_candidates_document_id',
     'CREATE INDEX ix_document_field_candidates_document_id ON document_field_candidates (document_id)'),
    ('ix_document_field_candidates_extraction_run_id',
     'CREATE INDEX ix_document_field_candidates_extraction_run_id ON document_field_candidates (extraction_run_id)'),
    ('ix_document_field_candidates_field_name',
     'CREATE INDEX ix_document_field_candidates_field_name ON document_field_candidates (field_name)'),
    ('ix_document_field_candidates_id',
     'CREATE INDEX ix_document_field_candidates_id ON document_field_candidates (id)'),
    ('ix_document_field_candidates_tenant_id',
     'CREATE INDEX ix_document_field_candidates_tenant_id ON document_field_candidates (tenant_id)'),
    ('ix_document_field_candidates_validation_status',
     'CREATE INDEX ix_document_field_candidates_validation_status ON document_field_candidates (validation_status)'),
    ('ix_hourly_market_prices_id',
     'CREATE INDEX ix_hourly_market_prices_id ON hourly_market_prices (id)'),
    ('ix_hourly_market_prices_period',
     'CREATE INDEX ix_hourly_market_prices_period ON hourly_market_prices (period)'),
    ('ix_incidents_action_type',
     'CREATE INDEX ix_incidents_action_type ON incidents (action_type)'),
    ('ix_incidents_category',
     'CREATE INDEX ix_incidents_category ON incidents (category)'),
    ('ix_incidents_created_at',
     'CREATE INDEX ix_incidents_created_at ON incidents (created_at)'),
    ('ix_incidents_dedupe_bucket',
     'CREATE INDEX ix_incidents_dedupe_bucket ON incidents (dedupe_bucket)'),
    ('ix_incidents_dedupe_key',
     'CREATE INDEX ix_incidents_dedupe_key ON incidents (dedupe_key)'),
    ('ix_incidents_id',
     'CREATE INDEX ix_incidents_id ON incidents (id)'),
    ('ix_incidents_invoice_id',
     'CREATE INDEX ix_incidents_invoice_id ON incidents (invoice_id)'),
    ('ix_incidents_period',
     'CREATE INDEX ix_incidents_period ON incidents (period)'),
    ('ix_incidents_primary_flag',
     'CREATE INDEX ix_incidents_primary_flag ON incidents (primary_flag)'),
    ('ix_incidents_provider',
     'CREATE INDEX ix_incidents_provider ON incidents (provider)'),
    ('ix_incidents_retry_eligible_at',
     'CREATE INDEX ix_incidents_retry_eligible_at ON incidents (retry_eligible_at)'),
    ('ix_incidents_severity',
     'CREATE INDEX ix_incidents_severity ON incidents (severity)'),
    ('ix_incidents_status',
     'CREATE INDEX ix_incidents_status ON incidents (status)'),
    ('ix_incidents_tenant_id',
     'CREATE INDEX ix_incidents_tenant_id ON incidents (tenant_id)'),
    ('ix_incidents_trace_id',
     'CREATE INDEX ix_incidents_trace_id ON incidents (trace_id)'),
    ('ix_invoices_file_hash',
     'CREATE INDEX ix_invoices_file_hash ON invoices (file_hash)'),
    ('ix_invoices_tenant_id',
     'CREATE INDEX ix_invoices_tenant_id ON invoices (tenant_id)'),
    ('ix_jobs_invoice_id',
     'CREATE INDEX ix_jobs_invoice_id ON jobs (invoice_id)'),
    ('ix_jobs_tenant_id',
     'CREATE INDEX ix_jobs_tenant_id ON jobs (tenant_id)'),
    ('ix_market_reference_prices_id',
     'CREATE INDEX ix_market_reference_prices_id ON market_reference_prices (id)'),
    ('ix_market_reference_prices_period',
     'CREATE INDEX ix_market_reference_prices_period ON market_reference_prices (period)'),
    ('ix_market_reference_prices_price_type',
     'CREATE INDEX ix_market_reference_prices_price_type ON market_reference_prices (price_type)'),
    ('ix_monthly_yekdem_prices_id',
     'CREATE INDEX ix_monthly_yekdem_prices_id ON monthly_yekdem_prices (id)'),
    ('ix_monthly_yekdem_prices_period',
     'CREATE UNIQUE INDEX ix_monthly_yekdem_prices_period ON monthly_yekdem_prices (period)'),
    ('ix_offers_id',
     'CREATE INDEX ix_offers_id ON offers (id)'),
    ('ix_offers_tenant_id',
     'CREATE INDEX ix_offers_tenant_id ON offers (tenant_id)'),
    ('ix_price_change_history_created_at',
     'CREATE INDEX ix_price_change_history_created_at ON price_change_history (created_at)'),
    ('ix_price_change_history_id',
     'CREATE INDEX ix_price_change_history_id ON price_change_history (id)'),
    ('ix_price_change_history_price_record_id',
     'CREATE INDEX ix_price_change_history_price_record_id ON price_change_history (price_record_id)'),
    ('ix_profile_templates_id',
     'CREATE INDEX ix_profile_templates_id ON profile_templates (id)'),
    ('ix_prospect_companies_city',
     'CREATE INDEX ix_prospect_companies_city ON prospect_companies (city)'),
    ('ix_prospect_companies_customer_id',
     'CREATE INDEX ix_prospect_companies_customer_id ON prospect_companies (customer_id)'),
    ('ix_prospect_companies_id',
     'CREATE INDEX ix_prospect_companies_id ON prospect_companies (id)'),
    ('ix_prospect_companies_normalized_domain',
     'CREATE INDEX ix_prospect_companies_normalized_domain ON prospect_companies (normalized_domain)'),
    ('ix_prospect_companies_normalized_name',
     'CREATE INDEX ix_prospect_companies_normalized_name ON prospect_companies (normalized_name)'),
    ('ix_prospect_companies_status',
     'CREATE INDEX ix_prospect_companies_status ON prospect_companies (status)'),
    ('ix_prospect_companies_tenant_id',
     'CREATE INDEX ix_prospect_companies_tenant_id ON prospect_companies (tenant_id)'),
    ('ix_prospect_contacts_email',
     'CREATE INDEX ix_prospect_contacts_email ON prospect_contacts (email)'),
    ('ix_prospect_contacts_id',
     'CREATE INDEX ix_prospect_contacts_id ON prospect_contacts (id)'),
    ('ix_prospect_contacts_prospect_company_id',
     'CREATE INDEX ix_prospect_contacts_prospect_company_id ON prospect_contacts (prospect_company_id)'),
    ('ix_prospect_contacts_tenant_id',
     'CREATE INDEX ix_prospect_contacts_tenant_id ON prospect_contacts (tenant_id)'),
    ('ix_prospect_sources_content_hash',
     'CREATE INDEX ix_prospect_sources_content_hash ON prospect_sources (content_hash)'),
    ('ix_prospect_sources_id',
     'CREATE INDEX ix_prospect_sources_id ON prospect_sources (id)'),
    ('ix_prospect_sources_prospect_company_id',
     'CREATE INDEX ix_prospect_sources_prospect_company_id ON prospect_sources (prospect_company_id)'),
    ('ix_prospect_sources_tenant_id',
     'CREATE INDEX ix_prospect_sources_tenant_id ON prospect_sources (tenant_id)'),
    ('ix_tasks_contract_id',
     'CREATE INDEX ix_tasks_contract_id ON tasks (contract_id)'),
    ('ix_tasks_customer_id',
     'CREATE INDEX ix_tasks_customer_id ON tasks (customer_id)'),
    ('ix_tasks_due_at',
     'CREATE INDEX ix_tasks_due_at ON tasks (due_at)'),
    ('ix_tasks_id',
     'CREATE INDEX ix_tasks_id ON tasks (id)'),
    ('ix_tasks_offer_id',
     'CREATE INDEX ix_tasks_offer_id ON tasks (offer_id)'),
    ('ix_tasks_status',
     'CREATE INDEX ix_tasks_status ON tasks (status)'),
    ('ix_tasks_tenant_id',
     'CREATE INDEX ix_tasks_tenant_id ON tasks (tenant_id)'),
    ('ix_uploaded_reference_documents_customer_id',
     'CREATE INDEX ix_uploaded_reference_documents_customer_id ON uploaded_reference_documents (customer_id)'),
    ('ix_uploaded_reference_documents_document_type',
     'CREATE INDEX ix_uploaded_reference_documents_document_type ON uploaded_reference_documents (document_type)'),
    ('ix_uploaded_reference_documents_id',
     'CREATE INDEX ix_uploaded_reference_documents_id ON uploaded_reference_documents (id)'),
    ('ix_uploaded_reference_documents_processing_status',
     'CREATE INDEX ix_uploaded_reference_documents_processing_status ON uploaded_reference_documents (processing_status)'),
    ('ix_uploaded_reference_documents_sha256',
     'CREATE INDEX ix_uploaded_reference_documents_sha256 ON uploaded_reference_documents (sha256)'),
    ('ix_uploaded_reference_documents_tenant_id',
     'CREATE INDEX ix_uploaded_reference_documents_tenant_id ON uploaded_reference_documents (tenant_id)'),
    ('ix_webhook_configs_id',
     'CREATE INDEX ix_webhook_configs_id ON webhook_configs (id)'),
    ('ix_webhook_configs_tenant_id',
     'CREATE INDEX ix_webhook_configs_tenant_id ON webhook_configs (tenant_id)'),
    ('ix_webhook_deliveries_id',
     'CREATE INDEX ix_webhook_deliveries_id ON webhook_deliveries (id)'),
    ('ix_webhook_deliveries_webhook_config_id',
     'CREATE INDEX ix_webhook_deliveries_webhook_config_id ON webhook_deliveries (webhook_config_id)'),
)


def structural_fingerprint(db_path: str) -> str:
    """Semanin (veri DEGIL) deterministik parmak izi."""
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        nesneler = sorted(
            (r[0], r[1], " ".join((r[2] or "").split()))
            for r in con.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
        )
    finally:
        con.close()
    return hashlib.sha256(repr(nesneler).encode("utf-8")).hexdigest()


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for parca in iter(lambda: fh.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def build_legacy_fixture(path: str) -> str:
    """
    SIFIRDAN deterministik, unversioned legacy fixture uretir.

    Faz 4B2'nin kabul ettigi legacy gercegini temsil eder:
      - `alembic_version` YOK (unversioned)
      - `ptf_drift_log` YOK (012 oncesi)
      - S5 tablolari YOK (f4e7efc70c80 oncesi)
      - `prospect_companies.verified_legal_type*` YOK (beda29569b0d oncesi)
      - dokuz tablonun legacy kolon sekli (nullability/default farklari)
      - incidents'in eksik canonical index'leri
      - `updated_by` NULL (accepted legacy data variant)

    Satirlar SENTETIKTIR ve PK/FK iliskilerini gercekten sinar.

    Cagrildigi yerler:
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    - tests/test_pdsmr_r4c1_production_controller.py
    - tests/test_pdsmr_r4d2_fixture_provenance.py
    """
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.isolation_level = None
    try:
        con.execute("BEGIN")
        for _ad, sql in LEGACY_TABLES:
            con.execute(sql)
        for _ad, sql in LEGACY_INDEXES:
            con.execute(sql)

        for i in range(1, BEKLENEN_SATIRLAR["customers"] + 1):
            con.execute(
                "INSERT INTO customers (id, name, company, email, phone, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (i, "SENTETIK MUSTERI %d" % i, "SENTETIK SIRKET %d" % i,
                 "sentetik%d@ornek.gecersiz" % i, "000000000%d" % i,
                 SABIT_ZAMAN, SABIT_ZAMAN))
        for i in range(1, BEKLENEN_SATIRLAR["offers"] + 1):
            con.execute(
                "INSERT INTO offers (id, tenant_id, customer_id, consumption_kwh, "
                "current_unit_price, weighted_ptf, yekdem, agreement_multiplier, "
                "current_total, offer_total, savings_amount, savings_ratio, "
                "created_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, "default", ((i - 1) % BEKLENEN_SATIRLAR["customers"]) + 1,
                 1000.0 * i, 3.0, 2500.0, 400.0, 1.05,
                 5000.0 * i, 4500.0 * i, 500.0 * i, 0.1, SABIT_ZAMAN, "draft"))
        for i, donem in enumerate(("2026-01", "2026-02", "2026-03", "2026-04"), start=1):
            # `updated_by` BILEREK NULL — accepted legacy data variant.
            con.execute(
                "INSERT INTO market_reference_prices (id, price_type, period, "
                "ptf_tl_per_mwh, yekdem_tl_per_mwh, status, source, captured_at, "
                "updated_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,NULL,?,?)",
                (i, "PTF", donem, 2500.0 + i, 400.0 + i, "provisional",
                 "epias_manual", SABIT_ZAMAN, SABIT_ZAMAN, SABIT_ZAMAN))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return path
