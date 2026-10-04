"""Declarative mappings for authorized vendor JSON/structured exports; no scripts or SQL."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from cbin.schemas import Invoice, StrictModel


class MappingError(ValueError):
    pass


class FieldRule(StrictModel):
    path: str | None = Field(default=None, min_length=1, max_length=200)
    constant: Any = None
    transform: Literal["identity", "decimal_string", "major_to_minor"] = "identity"
    minor_exponent: int = Field(default=2, ge=0, le=4)

    @model_validator(mode="after")
    def source(self):
        if (self.path is None) == (self.constant is None):
            raise ValueError("Choose one source path or non-null constant")
        return self


class MappingProfile(StrictModel):
    reviewed: bool = False
    evidence_reference: str = Field(min_length=1, max_length=200)
    fields: dict[str, FieldRule] = Field(min_length=1, max_length=100)
    line_items_path: str = Field(min_length=1, max_length=200)
    line_fields: dict[str, FieldRule] = Field(min_length=1, max_length=20)


class MappedSubmission(StrictModel):
    profile_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    vendor_payload: dict[str, Any]


def read_path(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise MappingError("SOURCE_FIELD_MISSING")
        value = value[part]
    return value


def apply_rule(payload, rule):
    value = read_path(payload, rule.path) if rule.path else rule.constant
    if rule.transform == "identity":
        return value
    # Reject binary floats so vendor imports cannot silently lose precision.
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise MappingError("EXACT_DECIMAL_INPUT_REQUIRED")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or abs(amount) > Decimal("1e16"):
            raise MappingError("DECIMAL_OUT_OF_RANGE")
        if rule.transform == "decimal_string":
            return str(amount)
        minor = amount * Decimal(10) ** rule.minor_exponent
        rounded = minor.quantize(Decimal("1"), ROUND_HALF_UP)
        if minor != rounded:
            raise MappingError("MINOR_UNIT_PRECISION_LOSS")
        return int(rounded)
    except MappingError:
        raise
    except (ValueError, ArithmeticError):
        raise MappingError("INVALID_DECIMAL_INPUT") from None


def assign(value, path, result):
    parts = path.split(".")
    if any(not part or part.startswith("_") for part in parts):
        raise MappingError("INVALID_TARGET_PATH")
    for part in parts[:-1]:
        previous = value.setdefault(part, {})
        if not isinstance(previous, dict):
            raise MappingError("TARGET_PATH_CONFLICT")
        value = previous
    if parts[-1] in value:
        raise MappingError("DUPLICATE_TARGET_FIELD")
    value[parts[-1]] = result


def normalize(payload, profile):
    if not profile.reviewed:
        raise MappingError("PROFILE_NOT_REVIEWED")
    canonical = {}
    for field, rule in profile.fields.items():
        assign(canonical, field, apply_rule(payload, rule))
    lines = read_path(payload, profile.line_items_path)
    if not isinstance(lines, list) or not 1 <= len(lines) <= 1000:
        raise MappingError("INVALID_SOURCE_LINES")
    if "line_items" in canonical:
        raise MappingError("TARGET_PATH_CONFLICT")
    canonical["line_items"] = []
    for source in lines:
        line = {}
        for field, rule in profile.line_fields.items():
            assign(line, field, apply_rule(source, rule))
        canonical["line_items"].append(line)
    try:
        return Invoice.model_validate(canonical)
    except ValidationError:
        raise MappingError("MAPPED_INVOICE_INVALID") from None
