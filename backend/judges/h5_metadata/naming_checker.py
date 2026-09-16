"""Deterministic naming convention engine for the H5 Metadata judge."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_FILE_NAME_LENGTH = 64
MAX_ATTRIBUTE_NAME_LENGTH = 30
MAX_DESCRIPTION_LENGTH = 256
MAX_ALIAS_LENGTH = 20

REQUIRED_SUFFIXES: dict[str, list[str]] = {
    "_CD": ["code", "status_code", "type_code", "category_code"],
    "_ID": ["identifier", "key", "surrogate_key"],
    "_DT": ["date", "datetime", "timestamp"],
    "_IND": ["indicator", "flag", "boolean"],
    "_NM": ["name", "label", "title"],
    "_AMT": ["amount", "dollar", "currency", "monetary"],
    "_CNT": ["count", "quantity", "number_of"],
    "_PCT": ["percent", "percentage", "rate"],
    "_TXT": ["text", "description_long", "notes", "comments"],
    "_ADDR": ["address", "street", "location"],
    "_NBR": ["number", "numeric_id"],
}

FORBIDDEN_PREFIXES = [
    "TMP_", "TEMP_", "TEST_", "OLD_", "BKP_", "BACKUP_",
    "FLG_", "NUM_", "STR_",
]

RESERVED_WORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "GROUP", "ORDER", "BY", "HAVING",
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TABLE",
    "INDEX", "VIEW", "NULL", "TRUE", "FALSE", "AND", "OR", "NOT",
    "IN", "LIKE", "BETWEEN", "EXISTS", "CASE", "WHEN", "THEN", "ELSE",
    "END", "AS", "ON", "INNER", "OUTER", "LEFT", "RIGHT", "FULL",
    "UNION", "ALL", "DISTINCT", "LIMIT", "OFFSET", "WITH", "CTE",
}

FILE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,62}[A-Z0-9]$")
ATTRIBUTE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,28}[A-Z0-9]$")

VALID_DATA_TYPES = {
    "VARCHAR", "CHAR", "NVARCHAR",
    "INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT",
    "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL",
    "DATE", "DATETIME", "TIMESTAMP", "TIME",
    "BOOLEAN", "BOOL",
    "BLOB", "CLOB", "TEXT",
    "BINARY", "VARBINARY",
}

NUMERIC_TYPES = {"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT",
                 "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL"}
STRING_TYPES = {"VARCHAR", "CHAR", "NVARCHAR", "TEXT", "CLOB"}
DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP", "TIME"}
BINARY_TYPES = {"BLOB", "BINARY", "VARBINARY"}
BOOLEAN_TYPES = {"BOOLEAN", "BOOL"}

_NUMERIC_RANK = {"TINYINT": 1, "SMALLINT": 2, "INT": 3, "INTEGER": 3, "BIGINT": 4}


class NamingViolationType(str, Enum):
    WRONG_CASE = "wrong_case"
    WRONG_SUFFIX = "wrong_suffix"
    FORBIDDEN_PREFIX = "forbidden_prefix"
    RESERVED_WORD = "reserved_word"
    TOO_LONG = "too_long"
    TOO_SHORT = "too_short"
    INVALID_CHARS = "invalid_chars"
    MISSING_SUFFIX = "missing_suffix"
    INVALID_PATTERN = "invalid_pattern"


@dataclass
class NamingViolation:
    field_name: str
    field_path: str
    violation_type: NamingViolationType
    description: str
    severity: str
    auto_correctable: bool
    suggested_correction: str | None


@dataclass
class CastSafetyIssue:
    attribute_name: str
    source_type: str
    target_type: str
    cast_expression: str | None
    issue_type: str
    severity: str
    description: str
    max_safe_value: str | None


def _split_type(type_string: str) -> tuple[str, list[int]]:
    if not type_string:
        return "", []
    base_match = re.match(r"^\s*([A-Za-z_]+)\s*(?:\((.*?)\))?\s*$", type_string.strip())
    if not base_match:
        return type_string.strip().upper(), []
    base = base_match.group(1).upper()
    params_str = base_match.group(2) or ""
    params: list[int] = []
    for part in params_str.split(","):
        part = part.strip()
        if part.isdigit():
            params.append(int(part))
    return base, params


def check_data_type_validity(type_string: str) -> bool:
    if not type_string:
        return False
    base, _ = _split_type(type_string)
    return base in VALID_DATA_TYPES


def check_file_name(file_name: str) -> list[NamingViolation]:
    violations: list[NamingViolation] = []
    name = file_name or ""
    path = "file_metadata.file_name"

    if not name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.TOO_SHORT,
                description="file_name is empty.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )
        return violations

    if name.upper() != name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.WRONG_CASE,
                description="file_name must be UPPER_SNAKE_CASE.",
                severity="warn",
                auto_correctable=True,
                suggested_correction=name.upper(),
            )
        )

    upper_name = name.upper()
    if len(upper_name) > MAX_FILE_NAME_LENGTH:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.TOO_LONG,
                description=f"file_name exceeds {MAX_FILE_NAME_LENGTH} characters.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )
    if len(upper_name) < 3:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.TOO_SHORT,
                description="file_name must be at least 3 characters.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    if upper_name in RESERVED_WORDS:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.RESERVED_WORD,
                description=f"file_name '{name}' is a reserved SQL keyword.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    for prefix in FORBIDDEN_PREFIXES:
        if upper_name.startswith(prefix):
            violations.append(
                NamingViolation(
                    field_name=name,
                    field_path=path,
                    violation_type=NamingViolationType.FORBIDDEN_PREFIX,
                    description=f"file_name uses forbidden prefix '{prefix}'.",
                    severity="block",
                    auto_correctable=False,
                    suggested_correction=None,
                )
            )
            break

    if upper_name.startswith("_") or upper_name.endswith("_"):
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="file_name must not start or end with underscore.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    if "__" in upper_name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="file_name contains consecutive underscores.",
                severity="block",
                auto_correctable=True,
                suggested_correction=re.sub(r"_+", "_", upper_name),
            )
        )

    if not FILE_NAME_PATTERN.match(upper_name) and not any(
        v.violation_type == NamingViolationType.INVALID_PATTERN for v in violations
    ):
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="file_name does not match enterprise pattern.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    return violations


def _expected_suffix(semantic_type: str | None) -> str | None:
    if not semantic_type:
        return None
    sem = semantic_type.strip().lower()
    for suffix, types in REQUIRED_SUFFIXES.items():
        if sem in types:
            return suffix
    return None


def check_attribute_name(
    attr_name: str,
    attr_path: str,
    semantic_type: str | None = None,
) -> list[NamingViolation]:
    violations: list[NamingViolation] = []
    name = attr_name or ""

    if not name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.TOO_SHORT,
                description="Attribute name is empty.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )
        return violations

    if name.upper() != name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.WRONG_CASE,
                description="Attribute name must be UPPER_SNAKE_CASE.",
                severity="warn",
                auto_correctable=True,
                suggested_correction=name.upper(),
            )
        )

    upper_name = name.upper()

    if len(upper_name) > MAX_ATTRIBUTE_NAME_LENGTH:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.TOO_LONG,
                description=f"Attribute name exceeds {MAX_ATTRIBUTE_NAME_LENGTH} characters.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )
    if len(upper_name) < 3:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.TOO_SHORT,
                description="Attribute name must be at least 3 characters.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    for prefix in FORBIDDEN_PREFIXES:
        if upper_name.startswith(prefix):
            violations.append(
                NamingViolation(
                    field_name=name,
                    field_path=attr_path,
                    violation_type=NamingViolationType.FORBIDDEN_PREFIX,
                    description=f"Attribute uses forbidden prefix '{prefix}'.",
                    severity="block",
                    auto_correctable=False,
                    suggested_correction=None,
                )
            )
            break

    for token in upper_name.split("_"):
        if token in RESERVED_WORDS:
            violations.append(
                NamingViolation(
                    field_name=name,
                    field_path=attr_path,
                    violation_type=NamingViolationType.RESERVED_WORD,
                    description=f"Attribute token '{token}' is a reserved SQL keyword.",
                    severity="block",
                    auto_correctable=False,
                    suggested_correction=None,
                )
            )
            break

    expected_suffix = _expected_suffix(semantic_type)
    if expected_suffix:
        suffix_ok = upper_name.endswith(expected_suffix)
        if not suffix_ok:
            base = re.sub(r"_(?:CD|ID|DT|IND|NM|AMT|CNT|PCT|TXT|ADDR|NBR)$", "", upper_name)
            suggested = f"{base}{expected_suffix}"
            violations.append(
                NamingViolation(
                    field_name=name,
                    field_path=attr_path,
                    violation_type=NamingViolationType.WRONG_SUFFIX,
                    description=(
                        f"Semantic type '{semantic_type}' requires suffix "
                        f"'{expected_suffix}' but name ends with a different suffix."
                    ),
                    severity="block",
                    auto_correctable=True,
                    suggested_correction=suggested,
                )
            )

    if upper_name.startswith("_") or upper_name.endswith("_"):
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="Attribute name must not start or end with underscore.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    if "__" in upper_name:
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="Attribute name contains consecutive underscores.",
                severity="block",
                auto_correctable=True,
                suggested_correction=re.sub(r"_+", "_", upper_name),
            )
        )

    if not ATTRIBUTE_NAME_PATTERN.match(upper_name) and not any(
        v.violation_type == NamingViolationType.INVALID_PATTERN for v in violations
    ):
        violations.append(
            NamingViolation(
                field_name=name,
                field_path=attr_path,
                violation_type=NamingViolationType.INVALID_PATTERN,
                description="Attribute name does not match enterprise pattern.",
                severity="block",
                auto_correctable=False,
                suggested_correction=None,
            )
        )

    return violations


def check_all_attribute_names(attributes: list[dict]) -> list[NamingViolation]:
    violations: list[NamingViolation] = []
    for index, attribute in enumerate(attributes or []):
        position = attribute.get("position", index + 1)
        name = attribute.get("name", "")
        semantic_type = attribute.get("semantic_type")
        path = f"attributes[{position}].name"
        violations.extend(check_attribute_name(name, path, semantic_type))
    return violations


def compute_naming_conformance_score(violations: list[NamingViolation]) -> float:
    if not violations:
        return 1.0
    block_violations = [v for v in violations if v.severity == "block"]
    warn_violations = [v for v in violations if v.severity == "warn"]
    auto_corrections = [v for v in violations if v.auto_correctable]

    base_score = 1.0 - (len(block_violations) * 0.10) - (len(warn_violations) * 0.03)
    correction_relief = len(auto_corrections) * 0.05
    score = base_score + correction_relief
    return float(min(1.0, max(0.0, score)))


def analyze_cast_safety(
    source_type: str,
    target_type: str,
    source_precision: int | None = None,
    target_precision: int | None = None,
    source_scale: int | None = None,
    target_scale: int | None = None,
    cast_expression: str | None = None,
    attribute_name: str = "",
) -> CastSafetyIssue | None:
    if not source_type or not target_type:
        return None
    src_base, src_params = _split_type(source_type)
    tgt_base, tgt_params = _split_type(target_type)

    if src_base == tgt_base and src_params == tgt_params:
        return None

    def _issue(issue_type: str, severity: str, description: str, max_safe: str | None = None) -> CastSafetyIssue:
        return CastSafetyIssue(
            attribute_name=attribute_name,
            source_type=source_type,
            target_type=target_type,
            cast_expression=cast_expression,
            issue_type=issue_type,
            severity=severity,
            description=description,
            max_safe_value=max_safe,
        )

    # INCOMPATIBLE
    if src_base in BINARY_TYPES and tgt_base not in BINARY_TYPES:
        return _issue("incompatible", "block", "Binary/BLOB cannot cast to non-binary type.")
    if src_base in DATE_TYPES and tgt_base in {"INTEGER", "INT", "BIGINT", "DECIMAL", "NUMERIC"}:
        return _issue("incompatible", "block", "Date/datetime cast to numeric without explicit transform.")
    if src_base in BOOLEAN_TYPES and tgt_base in STRING_TYPES:
        return _issue("incompatible", "block", "Boolean cast to VARCHAR is ambiguous without explicit transform.")

    # LOSSY — numeric narrowing
    if src_base == "BIGINT" and tgt_base in {"INTEGER", "INT"}:
        return _issue("lossy", "block", "BIGINT narrowed to INTEGER risks overflow.", "2,147,483,647")
    if src_base in {"FLOAT", "DOUBLE", "REAL"} and tgt_base in {"INTEGER", "INT", "BIGINT"}:
        return _issue("lossy", "block", "Floating point cast to integer truncates decimals.")
    if src_base in {"DECIMAL", "NUMERIC"} and tgt_base in {"INTEGER", "INT", "BIGINT"}:
        return _issue("lossy", "block", "Decimal cast to integer truncates decimals.")

    if src_base in {"DECIMAL", "NUMERIC"} and tgt_base in {"DECIMAL", "NUMERIC"}:
        sp = source_precision if source_precision is not None else (src_params[0] if len(src_params) >= 1 else None)
        tp = target_precision if target_precision is not None else (tgt_params[0] if len(tgt_params) >= 1 else None)
        ss = source_scale if source_scale is not None else (src_params[1] if len(src_params) >= 2 else None)
        ts = target_scale if target_scale is not None else (tgt_params[1] if len(tgt_params) >= 2 else None)
        if sp is not None and tp is not None and tp < sp:
            return _issue("lossy", "block", f"Decimal precision reduced from {sp} to {tp}.")
        if ss is not None and ts is not None and ts < ss:
            return _issue("lossy", "block", f"Decimal scale reduced from {ss} to {ts}.")

    # LOSSY — string narrowing
    if src_base in STRING_TYPES and tgt_base in {"VARCHAR", "CHAR", "NVARCHAR"}:
        sp = src_params[0] if src_params else None
        tp = tgt_params[0] if tgt_params else None
        if sp is not None and tp is not None and tp < sp:
            return _issue("lossy", "block", f"String length reduced from {sp} to {tp}; truncation risk.")

    # PRECISION LOSS
    if src_base == "DOUBLE" and tgt_base in {"FLOAT", "REAL"}:
        return _issue("precision_loss", "warn", "DOUBLE→FLOAT reduces precision.")
    if src_base in {"DECIMAL", "NUMERIC"} and tgt_base in {"FLOAT", "DOUBLE", "REAL"}:
        return _issue("precision_loss", "warn", "Decimal→float introduces representation error.")

    # SAFE widening cases
    if src_base in _NUMERIC_RANK and tgt_base in _NUMERIC_RANK:
        if _NUMERIC_RANK[tgt_base] >= _NUMERIC_RANK[src_base]:
            return None

    if src_base == "INTEGER" and tgt_base in {"DECIMAL", "NUMERIC", "BIGINT", "DOUBLE", "FLOAT"}:
        return None
    if src_base in STRING_TYPES and tgt_base in STRING_TYPES:
        sp = src_params[0] if src_params else None
        tp = tgt_params[0] if tgt_params else None
        if sp is None or tp is None or tp >= sp:
            return None
    if src_base == "DATE" and tgt_base in {"DATETIME", "TIMESTAMP"}:
        return None

    # Any-to-large-VARCHAR is safe
    if tgt_base == "VARCHAR":
        tp = tgt_params[0] if tgt_params else None
        if tp is None or tp >= 100:
            return None

    return None


def check_duplicate_attribute_names(attributes: list[dict]) -> list[str]:
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for attribute in attributes or []:
        name = str(attribute.get("name") or "").upper()
        if not name:
            continue
        seen[name] = seen.get(name, 0) + 1
    for name, count in seen.items():
        if count > 1:
            duplicates.append(name)
    return duplicates


def check_position_sequence(attributes: list[dict]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not attributes:
        return issues
    positions = [a.get("position") for a in attributes]
    expected = list(range(1, len(attributes) + 1))

    seen: dict[int, str] = {}
    for attribute in attributes:
        pos = attribute.get("position")
        name = str(attribute.get("name") or "")
        if pos is None or not isinstance(pos, int):
            issues.append(
                {
                    "type": "out_of_range",
                    "position": pos,
                    "attribute_name": name,
                    "description": f"Attribute '{name}' has missing or non-integer position.",
                }
            )
            continue
        if pos < 1:
            issues.append(
                {
                    "type": "out_of_range",
                    "position": pos,
                    "attribute_name": name,
                    "description": f"Attribute '{name}' position {pos} is below 1.",
                }
            )
            continue
        if pos in seen:
            issues.append(
                {
                    "type": "duplicate",
                    "position": pos,
                    "attribute_name": name,
                    "description": f"Position {pos} is reused by '{seen[pos]}' and '{name}'.",
                }
            )
        else:
            seen[pos] = name

    sorted_positions = sorted(p for p in positions if isinstance(p, int))
    if sorted_positions and sorted_positions[0] != 1:
        issues.append(
            {
                "type": "wrong_start",
                "position": sorted_positions[0],
                "attribute_name": "",
                "description": f"Position sequence starts at {sorted_positions[0]} — must start at 1.",
            }
        )
    expected_set = set(expected)
    actual_set = {p for p in sorted_positions}
    for missing in expected_set - actual_set:
        issues.append(
            {
                "type": "gap",
                "position": missing,
                "attribute_name": "",
                "description": f"Position sequence missing position {missing}.",
            }
        )

    return issues
