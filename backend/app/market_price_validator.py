"""
MarketPriceValidator - PTF Admin Management için input doğrulama ve normalizasyon.

Tek sorumluluk: Input doğrulama + normalize etme
Kullanım alanları:
- Admin upsert (tek kayıt / bulk)
- Import preview/commit (CSV/Excel satırı)
- JSON API (aynı DTO)

Feature: ptf-admin-management
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import List, Optional, Tuple, Union
from zoneinfo import ZoneInfo


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR CODES (stable contract for API/Import)
# ═══════════════════════════════════════════════════════════════════════════════

class ErrorCode(str, Enum):
    """Validation error codes - stable contract for downstream consumers."""
    # Period errors
    INVALID_PERIOD_FORMAT = "INVALID_PERIOD_FORMAT"
    FUTURE_PERIOD = "FUTURE_PERIOD"
    
    # Value errors
    INVALID_DECIMAL_FORMAT = "INVALID_DECIMAL_FORMAT"
    DECIMAL_COMMA_NOT_ALLOWED = "DECIMAL_COMMA_NOT_ALLOWED"
    VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
    TOO_MANY_DECIMALS = "TOO_MANY_DECIMALS"
    VALUE_REQUIRED = "VALUE_REQUIRED"
    
    # Status errors
    INVALID_STATUS = "INVALID_STATUS"
    
    # Price type errors
    INVALID_PRICE_TYPE = "INVALID_PRICE_TYPE"


@dataclass
class ValidationError:
    """Structured validation error."""
    error_code: ErrorCode
    field: str
    message: str
    
    def to_dict(self) -> dict:
        return {
            "error_code": self.error_code.value,
            "field": self.field,
            "message": self.message
        }


@dataclass
class ValidationResult:
    """Validation result with errors and warnings."""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": self.warnings
        }


@dataclass
class NormalizedMarketPriceInput:
    """Normalized and validated market price input."""
    period: str  # YYYY-MM
    value: Decimal  # TL/MWh, 2 decimal places
    status: str  # provisional | final
    price_type: str = "PTF"
    source_note: Optional[str] = None
    change_reason: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

# Period validation
PERIOD_REGEX = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Value bounds (TL/MWh)
MIN_VALUE = Decimal("0.01")  # Must be positive
MAX_VALUE = Decimal("10000")  # Guardrail - catches obvious typos
WARNING_MIN = Decimal("1000")  # Below this: warning
WARNING_MAX = Decimal("5000")  # Above this: warning

# Decimal precision
MAX_DECIMAL_PLACES = 2  # DB: DECIMAL(12,2)

# Valid statuses
VALID_STATUSES = frozenset({"provisional", "final"})

# Valid price types (extensible for SMF, YEKDEM)
VALID_PRICE_TYPES = frozenset({"PTF"})

# Timezone for future period check
TR_TIMEZONE = ZoneInfo("Europe/Istanbul")


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATOR CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class MarketPriceValidator:
    """
    Piyasa fiyatı validasyonu.
    
    Şimdilik PTF odaklı, price_type ile genişletilebilir.
    Tüm validation fonksiyonları pure - side effect yok.
    """
    
    def validate_period(self, period: str) -> ValidationResult:
        r"""
        Period format validation.
        
        Kabul: YYYY-MM (örn: 2026-02)
        Regex: ^\d{4}-(0[1-9]|1[0-2])$
        
        Normalizasyon: Sadece trim (başta/sonda boşluk)
        Otomatik düzeltme YOK (2026-2 → 2026-02 yapılmaz)
        
        **Validates: Requirements 3.1, 3.7**
        """
        errors: List[ValidationError] = []
        warnings: List[str] = []
        
        # Trim whitespace
        normalized = period.strip() if period else ""
        
        # Empty check
        if not normalized:
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_PERIOD_FORMAT,
                field="period",
                message="Period boş olamaz."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        # Regex validation
        if not PERIOD_REGEX.match(normalized):
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_PERIOD_FORMAT,
                field="period",
                message="Period formatı YYYY-MM olmalı (örn: 2026-02)."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        # Future period check
        if self.is_future_period(normalized):
            errors.append(ValidationError(
                error_code=ErrorCode.FUTURE_PERIOD,
                field="period",
                message=f"Gelecek dönem ({normalized}) için kayıt eklenemez."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        return ValidationResult(is_valid=True, errors=errors, warnings=warnings)
    
    def validate_value(self, value: Union[str, float, Decimal, None]) -> Tuple[ValidationResult, Optional[Decimal]]:
        """
        PTF value validation with decimal parsing.
        
        Parsing kuralları:
        - String: trim, strict decimal regex (nokta only)
        - Virgül (,) → reject with DECIMAL_COMMA_NOT_ALLOWED
        - Scientific notation (1e3) → reject
        
        Bounds:
        - <= 0: reject
        - > 10000: reject (guardrail)
        - (0, 1000) veya (5000, 10000]: warning
        - [1000, 5000]: accept without warning
        
        Precision:
        - Max 2 decimal places (DB: DECIMAL(12,2))
        
        **Validates: Requirements 3.2, 3.3, 3.4**
        """
        errors: List[ValidationError] = []
        warnings: List[str] = []
        parsed_value: Optional[Decimal] = None
        
        # None check
        if value is None:
            errors.append(ValidationError(
                error_code=ErrorCode.VALUE_REQUIRED,
                field="value",
                message="PTF değeri zorunludur."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        
        # Parse string input
        if isinstance(value, str):
            parsed_value = self._parse_decimal_string(value, errors)
            if parsed_value is None:
                return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        elif isinstance(value, Decimal):
            parsed_value = value
        elif isinstance(value, (int, float)):
            # Convert float/int to Decimal
            try:
                parsed_value = Decimal(str(value))
            except (InvalidOperation, ValueError):
                errors.append(ValidationError(
                    error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                    field="value",
                    message="Geçersiz sayı formatı."
                ))
                return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        else:
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                field="value",
                message="Geçersiz değer tipi."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        
        # Check decimal places
        decimal_places = self._get_decimal_places(parsed_value)
        if decimal_places > MAX_DECIMAL_PLACES:
            errors.append(ValidationError(
                error_code=ErrorCode.TOO_MANY_DECIMALS,
                field="value",
                message=f"En fazla {MAX_DECIMAL_PLACES} ondalık basamak kullanılabilir."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        
        # Bounds check
        if parsed_value <= 0:
            errors.append(ValidationError(
                error_code=ErrorCode.VALUE_OUT_OF_RANGE,
                field="value",
                message="PTF değeri 0'dan büyük olmalı."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        
        if parsed_value > MAX_VALUE:
            errors.append(ValidationError(
                error_code=ErrorCode.VALUE_OUT_OF_RANGE,
                field="value",
                message=f"PTF değeri {MAX_VALUE} TL/MWh'den büyük olamaz."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings), None
        
        # Warning range check
        if parsed_value < WARNING_MIN:
            warnings.append(f"PTF değeri ({parsed_value}) olağandışı düşük (< {WARNING_MIN} TL/MWh).")
        elif parsed_value > WARNING_MAX:
            warnings.append(f"PTF değeri ({parsed_value}) olağandışı yüksek (> {WARNING_MAX} TL/MWh).")
        
        return ValidationResult(is_valid=True, errors=errors, warnings=warnings), parsed_value
    
    def _parse_decimal_string(self, value: str, errors: List[ValidationError]) -> Optional[Decimal]:
        """Parse string to Decimal with strict rules."""
        trimmed = value.strip()
        
        # Empty check
        if not trimmed:
            errors.append(ValidationError(
                error_code=ErrorCode.VALUE_REQUIRED,
                field="value",
                message="PTF değeri boş olamaz."
            ))
            return None
        
        # Check for comma (TR format) - specific error
        if "," in trimmed:
            errors.append(ValidationError(
                error_code=ErrorCode.DECIMAL_COMMA_NOT_ALLOWED,
                field="value",
                message="Lütfen ondalık ayırıcı olarak nokta (.) kullanın."
            ))
            return None
        
        # Check for scientific notation
        if "e" in trimmed.lower():
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                field="value",
                message="Bilimsel gösterim (örn: 1e3) desteklenmiyor."
            ))
            return None
        
        # Strict decimal regex: digits, optional single dot, optional more digits
        # Reject: ".", "1.", ".1", "1.2.3", "-1"
        decimal_regex = re.compile(r"^\d+(\.\d+)?$")
        if not decimal_regex.match(trimmed):
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                field="value",
                message="Geçersiz format. 1234.56 formatında girin (nokta ile)."
            ))
            return None
        
        try:
            return Decimal(trimmed)
        except (InvalidOperation, ValueError):
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                field="value",
                message="Geçersiz sayı formatı."
            ))
            return None
    
    def _get_decimal_places(self, value: Decimal) -> int:
        """Get number of decimal places in a Decimal."""
        sign, digits, exponent = value.as_tuple()
        if exponent >= 0:
            return 0
        return abs(exponent)
    
    def validate_status(self, status: str) -> ValidationResult:
        """
        Status enum validation.
        
        Kabul: provisional | final (case-sensitive, lowercase only)
        Normalizasyon: Trim
        
        **Validates: Requirements 3.6**
        """
        errors: List[ValidationError] = []
        warnings: List[str] = []
        
        # Trim whitespace
        normalized = status.strip() if status else ""
        
        # Empty check
        if not normalized:
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_STATUS,
                field="status",
                message="Status boş olamaz."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        # Case-sensitive check
        if normalized not in VALID_STATUSES:
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_STATUS,
                field="status",
                message=f"Status sadece {' | '.join(sorted(VALID_STATUSES))} olabilir."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        return ValidationResult(is_valid=True, errors=errors, warnings=warnings)
    
    def validate_price_type(self, price_type: str) -> ValidationResult:
        """
        Price type validation.
        
        Kabul: PTF (şimdilik, gelecekte SMF, YEKDEM)
        """
        errors: List[ValidationError] = []
        warnings: List[str] = []
        
        # Trim whitespace
        normalized = price_type.strip() if price_type else ""
        
        # Empty check - default to PTF
        if not normalized:
            return ValidationResult(is_valid=True, errors=errors, warnings=warnings)
        
        if normalized not in VALID_PRICE_TYPES:
            errors.append(ValidationError(
                error_code=ErrorCode.INVALID_PRICE_TYPE,
                field="price_type",
                message=f"Price type sadece {' | '.join(sorted(VALID_PRICE_TYPES))} olabilir."
            ))
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)
        
        return ValidationResult(is_valid=True, errors=errors, warnings=warnings)
    
    def is_future_period(self, period: str) -> bool:
        """
        Check if period is in the future (Europe/Istanbul timezone).
        
        Future = period > current_period(TR)
        """
        now_tr = datetime.now(TR_TIMEZONE)
        current_period = now_tr.strftime("%Y-%m")
        return period > current_period
    
    def get_current_period(self) -> str:
        """Get current period in Europe/Istanbul timezone."""
        now_tr = datetime.now(TR_TIMEZONE)
        return now_tr.strftime("%Y-%m")
    
    def validate_entry(
        self,
        period: str,
        value: Union[str, float, Decimal, None],
        status: str,
        price_type: str = "PTF"
    ) -> Tuple[ValidationResult, Optional[NormalizedMarketPriceInput]]:
        """
        Validate complete market price entry.
        
        Returns:
            (ValidationResult, NormalizedMarketPriceInput or None)
        """
        all_errors: List[ValidationError] = []
        all_warnings: List[str] = []
        
        # Validate period
        period_result = self.validate_period(period)
        all_errors.extend(period_result.errors)
        all_warnings.extend(period_result.warnings)
        
        # Validate value
        value_result, parsed_value = self.validate_value(value)
        all_errors.extend(value_result.errors)
        all_warnings.extend(value_result.warnings)
        
        # Validate status
        status_result = self.validate_status(status)
        all_errors.extend(status_result.errors)
        all_warnings.extend(status_result.warnings)
        
        # Validate price_type
        price_type_result = self.validate_price_type(price_type)
        all_errors.extend(price_type_result.errors)
        all_warnings.extend(price_type_result.warnings)
        
        # Build result
        is_valid = len(all_errors) == 0
        result = ValidationResult(is_valid=is_valid, errors=all_errors, warnings=all_warnings)
        
        if not is_valid:
            return result, None
        
        # Build normalized input
        normalized = NormalizedMarketPriceInput(
            period=period.strip(),
            value=parsed_value,
            status=status.strip(),
            price_type=price_type.strip() if price_type else "PTF"
        )
        
        return result, normalized


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

# Singleton instance for convenience
_validator = MarketPriceValidator()


def validate_period(period: str) -> ValidationResult:
    """Validate period format."""
    return _validator.validate_period(period)


def validate_value(value: Union[str, float, Decimal, None]) -> Tuple[ValidationResult, Optional[Decimal]]:
    """Validate PTF value."""
    return _validator.validate_value(value)


def validate_status(status: str) -> ValidationResult:
    """Validate status enum."""
    return _validator.validate_status(status)


def validate_entry(
    period: str,
    value: Union[str, float, Decimal, None],
    status: str,
    price_type: str = "PTF"
) -> Tuple[ValidationResult, Optional[NormalizedMarketPriceInput]]:
    """Validate complete market price entry."""
    return _validator.validate_entry(period, value, status, price_type)


def is_future_period(period: str) -> bool:
    """Check if period is in the future."""
    return _validator.is_future_period(period)


def get_current_period() -> str:
    """Get current period in TR timezone."""
    return _validator.get_current_period()
