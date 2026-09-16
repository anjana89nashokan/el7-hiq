"""Deterministic IndiMap template schema validator for the H5 Metadata judge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from judges.h5_metadata.naming_checker import (
    check_data_type_validity,
    check_duplicate_attribute_names,
    check_position_sequence,
)

REQUIRED_FILE_FIELDS: dict[str, Any] = {
    "file_name": str,
    "file_description": str,
    "extract_frequency": str,
    "file_format": str,
    "delimiter": (str, type(None)),
    "effective_date": str,
    "layout_version": str,
    "domain": str,
    "sub_domain": str,
    "source_system": str,
    "target_system": str,
    "record_count_field": (str, type(None)),
    "driver_reference": str,
    "mapping_reference": str,
    "created_by": str,
    "approved_by": (str, type(None)),
}

REQUIRED_ATTRIBUTE_FIELDS: dict[str, Any] = {
    "position": int,
    "name": str,
    "description": str,
    "data_type": str,
    "length": (int, type(None)),
    "precision": (int, type(None)),
    "scale": (int, type(None)),
    "nullable": bool,
    "source_table": str,
    "source_column": str,
    "join_path": (str, type(None)),
    "transformation": (str, type(None)),
    "match_type": str,
    "confidence_score": float,
    "indimap_reference": (str, type(None)),
    "is_derived": bool,
    "default_value": (str, type(None)),
    "validation_rule": (str, type(None)),
}

NULLABLE_FIELDS = {
    "length", "precision", "scale", "join_path", "transformation",
    "indimap_reference", "default_value", "validation_rule",
    "approved_by", "record_count_field", "delimiter",
}

VALID_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY", "ON_DEMAND", "QUARTERLY", "ANNUAL"}
VALID_FILE_FORMATS = {"FIXED", "DELIMITED", "CSV", "JSON", "XML", "PARQUET"}
VALID_MATCH_TYPES = {"exact", "near_exact", "partial", "transformed", "no_match"}
SEMVER_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")


@dataclass
class SchemaError:
    path: str
    error_type: str
    description: str
    severity: str
    actual_value: str | None = None
    expected: str | None = None


@dataclass
class SchemaValidationResult:
    is_valid: bool
    errors: list[SchemaError] = field(default_factory=list)
    warnings: list[SchemaError] = field(default_factory=list)
    block_count: int = 0
    warn_count: int = 0
    attributes_validated: int = 0
    file_fields_validated: int = 0
    schema_version: str = "indimap-v2"


def _is_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, tuple):
        return any(_is_type(value, t) for t in expected_type)
    if expected_type is type(None):
        return value is None
    if expected_type is bool:
        return isinstance(value, bool)
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type is str:
        return isinstance(value, str)
    return isinstance(value, expected_type)


def _parse_iso_date(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value[:10])
        return True
    except Exception:
        return False


def _validate_file_metadata(file_metadata: dict, errors: list[SchemaError], warnings: list[SchemaError]) -> int:
    fields_validated = 0
    for field_name, expected_type in REQUIRED_FILE_FIELDS.items():
        fields_validated += 1
        if field_name not in file_metadata:
            errors.append(
                SchemaError(
                    path=f"file_metadata.{field_name}",
                    error_type="missing_field",
                    description=f"Required file_metadata field '{field_name}' is missing.",
                    severity="block",
                    expected=str(expected_type),
                )
            )
            continue
        value = file_metadata[field_name]
        if not _is_type(value, expected_type):
            errors.append(
                SchemaError(
                    path=f"file_metadata.{field_name}",
                    error_type="wrong_type",
                    description=f"file_metadata.{field_name} has wrong type.",
                    severity="block",
                    actual_value=str(type(value).__name__),
                    expected=str(expected_type),
                )
            )
            continue
        if expected_type is str and isinstance(value, str) and not value.strip():
            errors.append(
                SchemaError(
                    path=f"file_metadata.{field_name}",
                    error_type="empty_required",
                    description=f"file_metadata.{field_name} is empty.",
                    severity="block",
                )
            )
            continue

    frequency = str(file_metadata.get("extract_frequency") or "").upper()
    if frequency and frequency not in VALID_FREQUENCIES:
        errors.append(
            SchemaError(
                path="file_metadata.extract_frequency",
                error_type="invalid_value",
                description=f"extract_frequency '{frequency}' not allowed.",
                severity="block",
                actual_value=frequency,
                expected=str(sorted(VALID_FREQUENCIES)),
            )
        )

    file_format = str(file_metadata.get("file_format") or "").upper()
    if file_format and file_format not in VALID_FILE_FORMATS:
        errors.append(
            SchemaError(
                path="file_metadata.file_format",
                error_type="invalid_value",
                description=f"file_format '{file_format}' not allowed.",
                severity="block",
                actual_value=file_format,
                expected=str(sorted(VALID_FILE_FORMATS)),
            )
        )

    delimiter = file_metadata.get("delimiter")
    if file_format in {"DELIMITED", "CSV"} and not delimiter:
        errors.append(
            SchemaError(
                path="file_metadata.delimiter",
                error_type="constraint_violation",
                description=f"delimiter is required when file_format is {file_format}.",
                severity="block",
            )
        )

    eff_date = file_metadata.get("effective_date")
    if eff_date and not _parse_iso_date(eff_date):
        errors.append(
            SchemaError(
                path="file_metadata.effective_date",
                error_type="invalid_value",
                description="effective_date must be ISO 8601 (YYYY-MM-DD).",
                severity="block",
                actual_value=str(eff_date),
            )
        )

    version = file_metadata.get("layout_version")
    if version and not SEMVER_PATTERN.match(str(version)):
        errors.append(
            SchemaError(
                path="file_metadata.layout_version",
                error_type="invalid_value",
                description="layout_version must match semver pattern (e.g. 1.0).",
                severity="block",
                actual_value=str(version),
            )
        )

    for ref_field in ("driver_reference", "mapping_reference"):
        ref = file_metadata.get(ref_field)
        if isinstance(ref, str) and not ref.strip():
            errors.append(
                SchemaError(
                    path=f"file_metadata.{ref_field}",
                    error_type="empty_required",
                    description=f"{ref_field} must be non-empty.",
                    severity="block",
                )
            )

    return fields_validated


def _validate_attribute(
    index: int,
    attribute: dict,
    errors: list[SchemaError],
    warnings: list[SchemaError],
) -> int:
    fields_validated = 0
    base_path = f"attributes[{index}]"

    for field_name, expected_type in REQUIRED_ATTRIBUTE_FIELDS.items():
        fields_validated += 1
        if field_name not in attribute:
            errors.append(
                SchemaError(
                    path=f"{base_path}.{field_name}",
                    error_type="missing_field",
                    description=f"Required attribute field '{field_name}' is missing.",
                    severity="block",
                    expected=str(expected_type),
                )
            )
            continue
        value = attribute[field_name]
        if not _is_type(value, expected_type):
            errors.append(
                SchemaError(
                    path=f"{base_path}.{field_name}",
                    error_type="wrong_type",
                    description=f"{field_name} has wrong type.",
                    severity="block",
                    actual_value=str(type(value).__name__),
                    expected=str(expected_type),
                )
            )
            continue
        if expected_type is str and isinstance(value, str) and not value.strip():
            errors.append(
                SchemaError(
                    path=f"{base_path}.{field_name}",
                    error_type="empty_required",
                    description=f"{field_name} is empty.",
                    severity="block",
                )
            )
            continue

    position = attribute.get("position")
    if isinstance(position, int) and position < 1:
        errors.append(
            SchemaError(
                path=f"{base_path}.position",
                error_type="invalid_value",
                description=f"position must be a positive integer; got {position}.",
                severity="block",
                actual_value=str(position),
            )
        )

    data_type = attribute.get("data_type")
    if data_type and not check_data_type_validity(data_type):
        errors.append(
            SchemaError(
                path=f"{base_path}.data_type",
                error_type="invalid_value",
                description=f"data_type '{data_type}' is not a valid IndiMap type.",
                severity="block",
                actual_value=str(data_type),
            )
        )

    match_type = attribute.get("match_type")
    if match_type and match_type not in VALID_MATCH_TYPES:
        errors.append(
            SchemaError(
                path=f"{base_path}.match_type",
                error_type="invalid_value",
                description=f"match_type '{match_type}' is not allowed.",
                severity="block",
                actual_value=str(match_type),
                expected=str(sorted(VALID_MATCH_TYPES)),
            )
        )

    confidence = attribute.get("confidence_score")
    if isinstance(confidence, (int, float)) and not (0.0 <= float(confidence) <= 1.0):
        errors.append(
            SchemaError(
                path=f"{base_path}.confidence_score",
                error_type="invalid_value",
                description="confidence_score must be 0.0–1.0.",
                severity="block",
                actual_value=str(confidence),
            )
        )

    if attribute.get("is_derived") is True and not attribute.get("transformation"):
        errors.append(
            SchemaError(
                path=f"{base_path}.transformation",
                error_type="constraint_violation",
                description="is_derived=True requires a transformation expression.",
                severity="block",
            )
        )

    if match_type == "no_match":
        if attribute.get("source_table") != "PENDING_BSA_CLARIFICATION":
            warnings.append(
                SchemaError(
                    path=f"{base_path}.source_table",
                    error_type="constraint_violation",
                    description="match_type=no_match should set source_table=PENDING_BSA_CLARIFICATION.",
                    severity="warn",
                )
            )
        if attribute.get("source_column") != "PENDING_BSA_CLARIFICATION":
            warnings.append(
                SchemaError(
                    path=f"{base_path}.source_column",
                    error_type="constraint_violation",
                    description="match_type=no_match should set source_column=PENDING_BSA_CLARIFICATION.",
                    severity="warn",
                )
            )
    elif attribute.get("indimap_reference") in (None, ""):
        warnings.append(
            SchemaError(
                path=f"{base_path}.indimap_reference",
                error_type="constraint_violation",
                description="indimap_reference is recommended for non-no_match matches.",
                severity="warn",
            )
        )

    if match_type == "exact" and attribute.get("transformation"):
        warnings.append(
            SchemaError(
                path=f"{base_path}.transformation",
                error_type="constraint_violation",
                description="match_type=exact should not require a transformation.",
                severity="warn",
            )
        )

    if isinstance(confidence, (int, float)):
        if float(confidence) < 0.50 and match_type == "exact":
            warnings.append(
                SchemaError(
                    path=f"{base_path}.confidence_score",
                    error_type="constraint_violation",
                    description="confidence_score < 0.50 inconsistent with match_type=exact.",
                    severity="warn",
                )
            )
        if float(confidence) >= 0.90 and match_type == "no_match":
            warnings.append(
                SchemaError(
                    path=f"{base_path}.confidence_score",
                    error_type="constraint_violation",
                    description="confidence_score >= 0.90 inconsistent with match_type=no_match.",
                    severity="warn",
                )
            )

    return fields_validated


def validate_indimap_template(template: dict) -> SchemaValidationResult:
    errors: list[SchemaError] = []
    warnings: list[SchemaError] = []
    file_fields_validated = 0
    attributes_validated = 0

    if not isinstance(template, dict):
        errors.append(
            SchemaError(
                path="$",
                error_type="wrong_type",
                description="Template must be a JSON object.",
                severity="block",
            )
        )
        return SchemaValidationResult(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            block_count=len(errors),
            warn_count=0,
        )

    file_metadata = template.get("file_metadata")
    attributes = template.get("attributes")

    if not isinstance(file_metadata, dict):
        errors.append(
            SchemaError(
                path="file_metadata",
                error_type="missing_field",
                description="Template must contain a file_metadata object.",
                severity="block",
            )
        )
    if not isinstance(attributes, list):
        errors.append(
            SchemaError(
                path="attributes",
                error_type="missing_field",
                description="Template must contain an attributes array.",
                severity="block",
            )
        )

    if errors:
        return SchemaValidationResult(
            is_valid=False,
            errors=errors,
            warnings=warnings,
            block_count=len(errors),
            warn_count=len(warnings),
        )

    file_fields_validated = _validate_file_metadata(file_metadata, errors, warnings)

    if not attributes:
        errors.append(
            SchemaError(
                path="attributes",
                error_type="empty_required",
                description="attributes array must not be empty.",
                severity="block",
            )
        )
    else:
        for index, attribute in enumerate(attributes):
            if not isinstance(attribute, dict):
                errors.append(
                    SchemaError(
                        path=f"attributes[{index}]",
                        error_type="wrong_type",
                        description="Attribute entry must be an object.",
                        severity="block",
                    )
                )
                continue
            attributes_validated += _validate_attribute(index, attribute, errors, warnings)

        position_issues = check_position_sequence(attributes)
        for issue in position_issues:
            errors.append(
                SchemaError(
                    path=f"attributes.position[{issue.get('position')}]",
                    error_type="constraint_violation",
                    description=issue.get("description", ""),
                    severity="block",
                )
            )

        duplicates = check_duplicate_attribute_names(attributes)
        for duplicate in duplicates:
            errors.append(
                SchemaError(
                    path=f"attributes[name={duplicate}]",
                    error_type="constraint_violation",
                    description=f"Duplicate attribute name '{duplicate}'.",
                    severity="block",
                )
            )

    block_count = len(errors)
    warn_count = len(warnings)
    return SchemaValidationResult(
        is_valid=block_count == 0,
        errors=errors,
        warnings=warnings,
        block_count=block_count,
        warn_count=warn_count,
        attributes_validated=attributes_validated,
        file_fields_validated=file_fields_validated,
    )


def compute_template_completeness_score(template: dict) -> float:
    if not isinstance(template, dict):
        return 0.0
    file_metadata = template.get("file_metadata") or {}
    attributes = template.get("attributes") or []

    file_required = [
        f for f in REQUIRED_FILE_FIELDS if f not in NULLABLE_FIELDS
    ]
    file_populated = sum(
        1
        for f in file_required
        if file_metadata.get(f) not in (None, "", [], {})
    )

    attr_required_fields = [
        f for f in REQUIRED_ATTRIBUTE_FIELDS if f not in NULLABLE_FIELDS
    ]
    attr_required_total = len(attr_required_fields) * len(attributes)
    attr_populated = 0
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        for field_name in attr_required_fields:
            value = attribute.get(field_name)
            if value not in (None, "", [], {}):
                attr_populated += 1

    total_required = len(file_required) + attr_required_total
    if total_required == 0:
        return 0.0
    populated = file_populated + attr_populated
    return round(populated / total_required, 4)
