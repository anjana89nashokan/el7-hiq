"""
Metadata Layer — ADK tool functions.

Data type normalization, naming standardization, and validation.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from google.adk.tools import ToolContext

from agents.extract_agent.pipeline_models import NormalizedMetadata

logger = logging.getLogger(__name__)


# ─── Data Type Normalization Map ─────────────────────────────────────────────

TYPE_NORMALIZATION_MAP: dict[str, str] = {
    # String types
    "varchar": "STRING",
    "char": "STRING",
    "nvarchar": "STRING",
    "nchar": "STRING",
    "text": "STRING",
    "ntext": "STRING",
    "string": "STRING",
    "clob": "STRING",
    # Integer types
    "int": "INTEGER",
    "integer": "INTEGER",
    "bigint": "INTEGER",
    "smallint": "INTEGER",
    "tinyint": "INTEGER",
    "mediumint": "INTEGER",
    "int64": "INTEGER",
    "int32": "INTEGER",
    # Date types
    "date": "DATE",
    "datetime": "DATE",
    "datetime2": "DATE",
    "timestamp": "DATE",
    "smalldatetime": "DATE",
    "datetimeoffset": "DATE",
    "time": "DATE",
    # Decimal types
    "decimal": "DECIMAL",
    "numeric": "DECIMAL",
    "float": "DECIMAL",
    "real": "DECIMAL",
    "double": "DECIMAL",
    "money": "DECIMAL",
    "smallmoney": "DECIMAL",
    "number": "DECIMAL",
    "float64": "DECIMAL",
    # Boolean types
    "bit": "BOOLEAN",
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",
    # Binary types
    "binary": "STRING",
    "varbinary": "STRING",
    "image": "STRING",
    "blob": "STRING",
}

# ─── Common Healthcare Abbreviations ────────────────────────────────────────

ABBREVIATION_EXPANSIONS: dict[str, str] = {
    "mbr": "member",
    "mem": "member",
    "prv": "provider",
    "prov": "provider",
    "clm": "claim",
    "elig": "eligibility",
    "grp": "group",
    "svc": "service",
    "diag": "diagnosis",
    "proc": "procedure",
    "rx": "prescription",
    "pharm": "pharmacy",
    "auth": "authorization",
    "addr": "address",
    "dob": "date_of_birth",
    "ssn": "social_security_number",
    "npi": "national_provider_identifier",
    "tin": "tax_identification_number",
    "sk": "surrogate_key",
    "cd": "code",
    "nm": "name",
    "dsc": "description",
    "dt": "date",
    "ind": "indicator",
    "no": "number",
    "amt": "amount",
    "qty": "quantity",
    "pct": "percent",
    "cnt": "count",
    "typ": "type",
    "tp": "type",
    "val": "value",
    "id": "identifier",
}


def _normalize_type(raw_type: str) -> str:
    """Normalize a raw data type string to a standard type."""
    if not raw_type:
        return "STRING"
    # Strip length/precision info: VARCHAR(50) → VARCHAR
    base = re.sub(r"\(.*\)", "", raw_type).strip().lower()
    return TYPE_NORMALIZATION_MAP.get(base, "STRING")


def _standardize_name(raw_name: str) -> str:
    """
    Convert a field name to snake_case standard.

    Handles: CamelCase, UPPER_CASE, MixedCase, dot.notation, space separated.
    """
    if not raw_name:
        return ""

    # Replace dots and spaces with underscores
    name = raw_name.replace(".", "_").replace(" ", "_").replace("-", "_")

    # Insert underscores before uppercase letters in CamelCase
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)

    # Convert to lowercase
    name = name.lower()

    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)

    # Strip leading/trailing underscores
    name = name.strip("_")

    return name


# ─── ADK Tools ──────────────────────────────────────────────────────────────


def normalize_data_types(
    tool_context: ToolContext,
    fields: List[Dict[str, Any]],
) -> str:
    """
    Normalize data types for all fields to enterprise standards.

    Each field dict should have: field_name, data_type, length (optional), precision (optional).
    Results are saved to session state as 'normalized_types'.
    """
    normalized = []
    warnings = []

    for f in fields:
        field_name = f.get("field_name", "")
        raw_type = f.get("data_type", "")
        norm_type = _normalize_type(raw_type)

        # Warn on potentially lossy conversions
        base = re.sub(r"\(.*\)", "", raw_type).strip().lower()
        if base in ("float", "real", "double") and norm_type == "DECIMAL":
            warnings.append(
                f"{field_name}: {raw_type} → DECIMAL (potential precision loss)"
            )

        normalized.append(
            {
                "field_name": field_name,
                "source_data_type": raw_type,
                "normalized_data_type": norm_type,
                "length": f.get("length"),
                "precision": f.get("precision"),
            }
        )

    tool_context.state["normalized_types"] = normalized

    msg = f"Normalized {len(normalized)} field types."
    if warnings:
        msg += f" {len(warnings)} warning(s): {'; '.join(warnings[:5])}"

    logger.info(msg)
    return msg


def standardize_field_names(
    tool_context: ToolContext,
    field_names: List[str],
) -> str:
    """
    Standardize field names to snake_case naming convention.

    Results are saved to session state as 'standardized_names'.
    """
    results = []
    expansions = []

    for name in field_names:
        std_name = _standardize_name(name)

        # Track abbreviation expansions
        expanded = []
        for abbr, full in ABBREVIATION_EXPANSIONS.items():
            if abbr in std_name.split("_"):
                expanded.append(f"{abbr}→{full}")

        results.append(
            {
                "original_name": name,
                "standardized_name": std_name,
                "abbreviations_expanded": expanded,
            }
        )
        if expanded:
            expansions.extend(expanded)

    tool_context.state["standardized_names"] = results

    msg = f"Standardized {len(results)} field names."
    if expansions:
        msg += f" Abbreviations noted: {', '.join(set(expansions[:10]))}"

    logger.info(msg)
    return msg


def validate_metadata(
    tool_context: ToolContext,
) -> str:
    """
    Validate normalized metadata for consistency issues.

    Checks for: duplicate names, missing types, format inconsistencies.
    Produces a validation report saved to session state.
    """
    normalized_types = tool_context.state.get("normalized_types", [])
    standardized_names = tool_context.state.get("standardized_names", [])

    issues = []

    # Check for duplicate standardized names
    name_counts: dict[str, int] = {}
    for n in standardized_names:
        std = n.get("standardized_name", "")
        name_counts[std] = name_counts.get(std, 0) + 1
    for name, count in name_counts.items():
        if count > 1:
            issues.append(
                {
                    "field_name": name,
                    "issue_type": "name_conflict",
                    "severity": "HIGH",
                    "description": f"Duplicate standardized name '{name}' appears {count} times",
                    "suggested_fix": "Add disambiguating prefix (e.g., source table name)",
                }
            )

    # Check for unknown/missing types
    for t in normalized_types:
        if (
            t.get("normalized_data_type") == "STRING"
            and t.get("source_data_type", "").lower() not in TYPE_NORMALIZATION_MAP
        ):
            issues.append(
                {
                    "field_name": t.get("field_name", ""),
                    "issue_type": "missing_type",
                    "severity": "MEDIUM",
                    "description": f"Unknown source type '{t.get('source_data_type', '')}' defaulted to STRING",
                    "suggested_fix": "Verify source type and add to normalization map if needed",
                }
            )

    # Build normalized metadata records
    name_lookup = {n["original_name"]: n for n in standardized_names}
    type_lookup = {t["field_name"]: t for t in normalized_types}

    normalized_metadata = []
    for t in normalized_types:
        field = t["field_name"]
        name_info = name_lookup.get(field, {})
        normalized_metadata.append(
            NormalizedMetadata(
                field_name=field,
                normalized_name=name_info.get(
                    "standardized_name", _standardize_name(field)
                ),
                normalized_data_type=t["normalized_data_type"],
                source_data_type=t.get("source_data_type", ""),
                length=t.get("length"),
                precision=t.get("precision"),
            ).model_dump()
        )

    tool_context.state["normalized_metadata"] = normalized_metadata
    tool_context.state["metadata_validation_issues"] = issues
    tool_context.state["metadata_summary"] = {
        "total_fields": len(normalized_metadata),
        "total_issues": len(issues),
        "high_severity": sum(1 for i in issues if i.get("severity") == "HIGH"),
        "medium_severity": sum(1 for i in issues if i.get("severity") == "MEDIUM"),
    }

    msg = (
        f"Metadata validation complete. {len(normalized_metadata)} fields normalized. "
        f"{len(issues)} issues found ({sum(1 for i in issues if i.get('severity') == 'HIGH')} HIGH)."
    )
    logger.info(msg)
    return msg

def extract_metadata_template_values(
    tool_context: ToolContext,
    extracted_metadata_json: str,
) -> str:
    """
    Save the extracted metadata template values.

    extracted_metadata_json MUST be a JSON STRING encoding an object with two keys:
    - "filespecs": object mapping FileSpecs field names to values
    - "file1": object with header fields and an "attributes" list of column-level rows

    (A JSON string param is used instead of a raw object so the model can reliably
    populate it.) For backward compatibility, a flat object is treated as filespecs.
    """
    import json as _json
    import re as _re

    # Parse the JSON string the model passed (tolerate ```json fences / prose).
    if isinstance(extracted_metadata_json, dict):
        extracted_metadata = extracted_metadata_json  # already-parsed (defensive)
    else:
        raw = str(extracted_metadata_json or "").strip()
        m = _re.search(r"```(?:json)?\s*(.*?)\s*```", raw, _re.DOTALL)
        if m:
            raw = m.group(1).strip()
        else:
            s, e = raw.find("{"), raw.rfind("}")
            if s != -1 and e > s:
                raw = raw[s : e + 1]
        try:
            extracted_metadata = _json.loads(raw) if raw else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("extract_metadata_template_values: JSON parse failed: %s", exc)
            extracted_metadata = {}

    # Store the full structure
    tool_context.state["extracted_metadata"] = extracted_metadata

    # Split into separate state keys for downstream consumers
    if "filespecs" in extracted_metadata:
        tool_context.state["extracted_filespecs"] = extracted_metadata["filespecs"]
    else:
        # Backward compatibility: treat the whole dict as filespecs
        tool_context.state["extracted_filespecs"] = extracted_metadata

    if "file1" in extracted_metadata:
        tool_context.state["extracted_file1"] = extracted_metadata["file1"]
    else:
        tool_context.state["extracted_file1"] = {}

    filespecs_count = len(tool_context.state.get("extracted_filespecs", {}))
    file1_data = tool_context.state.get("extracted_file1", {})
    attributes_count = len(file1_data.get("attributes", [])) if isinstance(file1_data, dict) else 0

    msg = (
        f"Extracted {filespecs_count} FileSpecs values "
        f"and {attributes_count} file1 attribute rows."
    )
    logger.info(msg)
    return msg

