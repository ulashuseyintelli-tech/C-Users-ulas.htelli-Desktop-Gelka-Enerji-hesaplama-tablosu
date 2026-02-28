"""Invoice validation public API (Faz A / 4.1+)."""

from .error_codes import ValidationErrorCode
from .shadow import (
    ShadowCompareResult,
    compare_validators,
    get_shadow_counters,
    record_shadow_metrics,
    reset_shadow_counters,
    shadow_validate_hook,
)
from .shadow_config import ShadowConfig, is_whitelisted, load_config, should_sample
from .types import (
    ENFORCE_BLOCKED_TOTAL,
    ENFORCE_MODE_GAUGE,
    ENFORCE_SOFTWARN_TOTAL,
    ENFORCE_TOTAL,
    SHADOW_ACTIONABLE_TOTAL,
    SHADOW_METRIC_NAME,
    SHADOW_SAMPLED_TOTAL,
    SHADOW_WHITELISTED_TOTAL,
    InvoiceValidationError,
    InvoiceValidationResult,
    NormalizedInvoice,
    ValidationSeverity,
)
from .validator import validate
from .enforcement_config import (
    CodeSeverity,
    EnforcementConfig,
    ValidationMode,
    load_enforcement_config,
)
from .enforcement import (
    EnforcementDecision,
    ValidationBlockedError,
    canonical_to_validator_dict,
    enforce_validation,
    get_enforcement_counters,
    record_enforcement_metrics,
    reset_enforcement_counters,
)

__all__ = [
    "ValidationErrorCode",
    "ValidationSeverity",
    "InvoiceValidationError",
    "InvoiceValidationResult",
    "NormalizedInvoice",
    "SHADOW_METRIC_NAME",
    "SHADOW_SAMPLED_TOTAL",
    "SHADOW_WHITELISTED_TOTAL",
    "SHADOW_ACTIONABLE_TOTAL",
    "ENFORCE_TOTAL",
    "ENFORCE_BLOCKED_TOTAL",
    "ENFORCE_SOFTWARN_TOTAL",
    "ENFORCE_MODE_GAUGE",
    "ShadowCompareResult",
    "ShadowConfig",
    "compare_validators",
    "get_shadow_counters",
    "is_whitelisted",
    "load_config",
    "record_shadow_metrics",
    "reset_shadow_counters",
    "shadow_validate_hook",
    "should_sample",
    "validate",
    "CodeSeverity",
    "EnforcementConfig",
    "EnforcementDecision",
    "ValidationBlockedError",
    "ValidationMode",
    "canonical_to_validator_dict",
    "enforce_validation",
    "get_enforcement_counters",
    "load_enforcement_config",
    "record_enforcement_metrics",
    "reset_enforcement_counters",
]
