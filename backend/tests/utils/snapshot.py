"""
Snapshot Normalizer - Sprint 5.3

Incident snapshot'larini normalize eder.
Sadece behavioral contract field'lari tutar, volatile field'lari atar.

Contract fields (stable):
- primary_flag, category, severity
- action.type, action.owner, action.code
- all_flags, secondary_flags, deduction_total

Volatile fields (dropped):
- action.hint_text (UI copy, can change)
- message, details, timestamps, trace_id
"""

from typing import Dict, Any, List


def normalize_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    """
    Incident'i normalize eder - sadece contract field'lari tutar.
    
    Tutulan field'lar:
    - primary_flag: Ana hata flag'i
    - category: Incident kategorisi
    - severity: S1/S2/S3/S4
    - action: {type, owner, code} - hint_text DROP edilir
    - all_flags: Tum flag'ler (sorted + unique)
    - secondary_flags: Primary haric diger flag'ler
    - deduction_total: Toplam puan dusumu
    
    Atilan field'lar:
    - action.hint_text: Volatile (UI copy)
    - message: Volatile (degisebilir)
    - details: Cok fazla detay
    - trace_id, invoice_id: Test'e ozel
    - timestamps: Volatile
    """
    details = incident.get("details", {}) or {}
    
    # Action bilgisini al - sadece contract fields
    action_info = None
    action_data = details.get("action") if "details" in incident else incident.get("action")
    if action_data and isinstance(action_data, dict):
        action_info = {
            "type": action_data.get("type"),
            "owner": action_data.get("owner"),
            "code": action_data.get("code"),
            # hint_text DROP - volatile
        }
    
    # Deduction total hesapla
    deduction_total = 0
    flag_details = details.get("flag_details", [])
    for fd in flag_details:
        deduction_total += fd.get("deduction", 0)
    
    # Primary flag - details icinden veya direkt incident'ten
    primary_flag = details.get("primary_flag") or incident.get("primary_flag")
    
    # All flags - details icinden veya direkt incident'ten
    all_flags = details.get("all_flags") or incident.get("all_flags", [])
    
    # Secondary flags
    secondary_flags = details.get("secondary_flags") or incident.get("secondary_flags", [])
    
    return {
        "primary_flag": primary_flag,
        "category": incident.get("category"),
        "severity": incident.get("severity"),
        "action": action_info,
        "all_flags": all_flags,
        "secondary_flags": secondary_flags,
        "deduction_total": deduction_total,
    }


def compare_incidents(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    """
    Iki normalize edilmis incident'i karsilastirir.
    Farklilik varsa hata mesajlari doner.
    """
    errors = []
    
    # Primary flag
    if actual.get("primary_flag") != expected.get("primary_flag"):
        errors.append(
            f"primary_flag mismatch: got {actual.get('primary_flag')}, "
            f"expected {expected.get('primary_flag')}"
        )
    
    # Category
    if actual.get("category") != expected.get("category"):
        errors.append(
            f"category mismatch: got {actual.get('category')}, "
            f"expected {expected.get('category')}"
        )
    
    # Severity
    if actual.get("severity") != expected.get("severity"):
        errors.append(
            f"severity mismatch: got {actual.get('severity')}, "
            f"expected {expected.get('severity')}"
        )
    
    # All flags (order matters)
    if actual.get("all_flags") != expected.get("all_flags"):
        errors.append(
            f"all_flags mismatch: got {actual.get('all_flags')}, "
            f"expected {expected.get('all_flags')}"
        )
    
    # Secondary flags (order matters)
    if actual.get("secondary_flags") != expected.get("secondary_flags"):
        errors.append(
            f"secondary_flags mismatch: got {actual.get('secondary_flags')}, "
            f"expected {expected.get('secondary_flags')}"
        )
    
    # Action (if expected has action)
    if expected.get("action"):
        actual_action = actual.get("action") or {}
        expected_action = expected.get("action")
        
        if actual_action.get("type") != expected_action.get("type"):
            errors.append(
                f"action.type mismatch: got {actual_action.get('type')}, "
                f"expected {expected_action.get('type')}"
            )
        if actual_action.get("owner") != expected_action.get("owner"):
            errors.append(
                f"action.owner mismatch: got {actual_action.get('owner')}, "
                f"expected {expected_action.get('owner')}"
            )
        if actual_action.get("code") != expected_action.get("code"):
            errors.append(
                f"action.code mismatch: got {actual_action.get('code')}, "
                f"expected {expected_action.get('code')}"
            )
    
    return errors
