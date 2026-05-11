# Bugfix Requirements Document

## Introduction

Risk analizi hesaplanırken frontend "hesaplanıyor..." mesajında takılıp kalıyor. Kök neden: `backend/app/pricing/router.py` dosyasındaki `_get_or_generate_consumption()` fonksiyonu, `generate_hourly_consumption()` çağrısında zorunlu `db: Session` parametresini geçirmiyor. Bu durum, şablon tabanlı tüketim profili üretimi sırasında pozisyonel argüman uyumsuzluğuna yol açarak fonksiyonun hata vermesine ve risk analizinin tamamlanamamasına neden oluyor.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `use_template=True` with valid `template_name` and `template_monthly_kwh` THEN the system calls `generate_hourly_consumption(template_name, template_monthly_kwh, period)` without the required `db: Session` parameter, causing a positional argument mismatch where `period` (a string) is passed in the position where `db` (a Session) is expected

1.2 WHEN the risk analysis endpoint is invoked with template-based consumption THEN the system fails silently or raises a runtime error, leaving the frontend stuck in the "hesaplanıyor..." (calculating) state indefinitely

### Expected Behavior (Correct)

2.1 WHEN `use_template=True` with valid `template_name` and `template_monthly_kwh` THEN the system SHALL call `generate_hourly_consumption(template_name, template_monthly_kwh, period, db)` passing all four required arguments including the `db: Session` parameter

2.2 WHEN the risk analysis endpoint is invoked with template-based consumption THEN the system SHALL successfully generate hourly consumption records from the template and return the completed risk analysis result to the frontend

### Unchanged Behavior (Regression Prevention)

3.1 WHEN `customer_id` is provided and real consumption records exist in the database THEN the system SHALL CONTINUE TO load consumption records from the database via `_load_consumption_records()` without using template generation

3.2 WHEN neither template parameters nor valid `customer_id` with records are provided THEN the system SHALL CONTINUE TO raise an HTTP 422 error with the "missing_consumption_data" message

3.3 WHEN `generate_hourly_consumption()` receives an invalid `period` format THEN the system SHALL CONTINUE TO raise a `ValueError` with the "Geçersiz dönem formatı" message

3.4 WHEN `generate_hourly_consumption()` receives a `template_name` that does not exist in the database THEN the system SHALL CONTINUE TO raise a `ValueError` with the "Profil şablonu bulunamadı" message
