"""
IndeMap historical mapping helpers for Step 2 evidence enrichment.

Purpose:
  - Provide a dedicated integration point for fetching historical mappings from IndeMap.
  - Normalize and prefilter noisy historical rows before LLM reranking.
  - Keep this module utility-only (no agent/runtime orchestration here).

Important:
  - Historical mappings are helper evidence, not truth.
  - Deterministic schema validation remains authoritative in Step 2.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from agents.mapping_generation.models import RuleType


# TODO(IndeMap history integration):
# Replace this local map with a canonical rule-code dictionary sourced from IndeMap metadata/reference
# so code->rule semantics are centrally governed and versioned.
HISTORY_RULE_TYPE_MAP: dict[str, RuleType] = {
    "DR": RuleType.DIRECT,
    "DIRECT": RuleType.DIRECT,
    "LU": RuleType.LOOKUP,
    "LOOKUP": RuleType.LOOKUP,
    "SK": RuleType.SK,
    "HC": RuleType.HARDCODE,
    "HARDCODE": RuleType.HARDCODE,
    "DF": RuleType.DEFAULT,
    "DEFAULT": RuleType.DEFAULT,
    "SS": RuleType.SUBSTRING,
    "SUBSTRING": RuleType.SUBSTRING,
    "CA": RuleType.CASE,
    "CASE": RuleType.CASE,
    "IF": RuleType.IF_ELSE,
    "IF_ELSE": RuleType.IF_ELSE,
    "TECH": RuleType.TECHNICAL,
    "TECHNICAL": RuleType.TECHNICAL,
}


def fetch_indemap_past_mappings_for_target(
    *,
    database_name: str | None,
    target_table_name: str,
    target_column_name: str,
    top_n: int = 10,
) -> dict[str, Any]:
    """
    Placeholder hook for one target column historical mapping query.

    Input example:
      database_name="DB_AEDWP1V"
      target_table_name="PRV_DATA"
      target_column_name="AEDW_PRV_SK"
      top_n=10

    Expected output example:
      {
        "column_name": "AEDW_PRV_SK",
        "top_n": 10,
        "total_rules": 10,
        "rules": [
          {
            "target_column_name": "AEDW_PRV_SK",
            "interface_code": "DWPV292",
            "rule_type_code": "LU",
            "source_entity_text": "prv_src_loc",
            "source_column_text": "BJF_ID",
            "join_text": null,
            "rule_text": "JOIN ... MOVE ...",
            "rule_sequence_no": 1,
            "special_text": null,
            "filter_text": null,
            "last_updated": "2023-08-16 00:17:04.783000",
            "source_column_name": null
          }
        ],
        "timestamp": "2026-02-23T08:59:08.347802"
      }

    Failure contract:
      - Raise RuntimeError for transport/auth/provider failures.
      - Return a valid payload with empty `rules` for no-match cases.
    """
    db_label = str(database_name or "").strip() or "<optional>"
    table_label = str(target_table_name or "").strip()
    col_label = str(target_column_name or "").strip()
    if not table_label or not col_label:
        raise RuntimeError("target_table_name and target_column_name are required for IndeMap history lookup.")

    raise RuntimeError(
        "IndeMap historical mapping provider is not wired yet. "
        "Implement fetch_indemap_past_mappings_for_target(database_name, target_table_name, target_column_name, top_n) "
        "in server/utils/indemap_history_mapping_utils.py.\n"
        "TODO:\n"
        "  1) Wire auth/credentials for live DB/service.\n"
        "  2) Query by exact (database_name optional, target_table_name, target_column_name).\n"
        "  3) Return payload keys: column_name, top_n, total_rules, rules, timestamp.\n"
        "  4) Add pagination/retry safeguards.\n"
        f"Input received: database_name={db_label}, target_table_name={table_label}, target_column_name={col_label}, top_n={int(top_n)}."
    )


def fetch_indemap_past_mappings_batch(
    *,
    requests: list[dict[str, str]],
    top_n: int = 10,
) -> dict[str, Any]:
    """
    Optional batch helper over explicit target requests.

    Input example:
      requests = [
        {"database_name": "DB_AEDWP1V", "target_table_name": "PRV_DATA", "target_column_name": "AEDW_PRV_SK"},
        {"database_name": "DB_AEDWP1V", "target_table_name": "PRV_DATA", "target_column_name": "PRV_TAX_ID"},
      ]

    Output example:
      {
        "items": [
          {
            "database_name": "DB_AEDWP1V",
            "target_table_name": "PRV_DATA",
            "target_column_name": "AEDW_PRV_SK",
            "payload": {...}
          }
        ],
        "timestamp": "2026-02-23T08:59:08.347802"
      }
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in requests or []:
        if not isinstance(row, dict):
            continue
        db = str(row.get("database_name") or "").strip() or None
        table = str(row.get("target_table_name") or "").strip()
        col = str(row.get("target_column_name") or "").strip()
        if not table or not col:
            continue
        key = ((db or "").upper(), table.upper(), col.upper())
        if key in seen:
            continue
        seen.add(key)
        payload = fetch_indemap_past_mappings_for_target(
            database_name=db,
            target_table_name=table,
            target_column_name=col,
            top_n=int(top_n),
        )
        _validate_history_payload(payload)
        out.append(
            {
                "database_name": db,
                "target_table_name": table,
                "target_column_name": col,
                "payload": payload,
            }
        )
    return {"items": out, "timestamp": datetime.utcnow().isoformat()}


def _validate_history_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("Invalid IndeMap history payload: expected object.")
    if "column_name" not in payload:
        raise RuntimeError("Invalid IndeMap history payload: missing 'column_name'.")
    if "rules" not in payload or not isinstance(payload.get("rules"), list):
        raise RuntimeError("Invalid IndeMap history payload: missing 'rules' list.")
    if "timestamp" not in payload:
        raise RuntimeError("Invalid IndeMap history payload: missing 'timestamp'.")


def normalize_history_rule_type(rule_type_code: str) -> RuleType | str:
    """
    Map historical rule_type_code to current Step 2 rule enum.
    """
    code = str(rule_type_code or "").strip().upper()
    return HISTORY_RULE_TYPE_MAP.get(code, "UNKNOWN")


def extract_source_hints(rule_row: dict[str, Any]) -> dict[str, Any]:
    """
    Extract compact source hints from historical free-text fields.

    Note:
      - In historical rows, `source_column_text` often carries source column names or close aliases.
      - We intentionally treat both `source_column_name` and `source_column_text` as candidate column hints.
      - Parsing remains conservative; semantic interpretation happens in the LLM rerank stage.
      - TODO: Add richer tokenization/alias normalization rules for mixed prose values.
    """
    entity_text = str(rule_row.get("source_entity_text") or "").strip()
    column_text = str(rule_row.get("source_column_text") or "").strip()
    source_col_name = str(rule_row.get("source_column_name") or "").strip()
    join_text = str(rule_row.get("join_text") or "").strip()
    rule_text = str(rule_row.get("rule_text") or "").strip()
    filter_text = str(rule_row.get("filter_text") or "").strip()

    explicit_columns: list[str] = []
    for raw in [source_col_name, column_text]:
        if not raw:
            continue
        for token in _split_candidate_tokens(raw):
            if token not in explicit_columns:
                explicit_columns.append(token)

    return {
        "source_entity_text": entity_text or None,
        "source_column_text": column_text or None,
        "source_column_name": source_col_name or None,
        "source_column_name_hint": source_col_name or column_text or None,
        "explicit_columns": explicit_columns,
        "join_text": join_text or None,
        "rule_text": rule_text or None,
        "filter_text": filter_text or None,
    }


def is_schema_compatible(rule_row: dict[str, Any], source_schema) -> tuple[bool | None, str | None]:
    """
    Check whether explicit source column hints exist in current source schema.

    Returns:
      - (True, None) when explicit hints are present and compatible.
      - (False, reason) when explicit hints are present and incompatible.
      - (None, reason) when no explicit hints can be validated.
    """
    hints = extract_source_hints(rule_row)
    explicit_cols = list(hints.get("explicit_columns") or [])
    if not explicit_cols:
        return (None, "No explicit source columns to validate.")

    known_cols = _build_source_column_normalized_set(source_schema)
    unknown: list[str] = []
    for col in explicit_cols:
        if _normalize_identifier(col) not in known_cols:
            unknown.append(col)

    if unknown:
        return (False, f"Explicit source columns not found in current source schema: {', '.join(unknown[:6])}.")
    return (True, None)


def prefilter_history_rules(
    rules: list[dict[str, Any]],
    source_schema,
    target_table: str,
    target_column: str,
    *,
    max_keep: int = 5,
) -> list[dict[str, Any]]:
    """
    Deterministic prefilter for historical rules before LLM rerank.

    Policy:
      - Keep exact target-column matches only.
      - Drop explicit schema mismatches.
      - Keep unknown-schema rows but mark them as lower-quality candidates.
    """
    out: list[dict[str, Any]] = []
    target_col_u = str(target_column or "").strip().upper()
    for idx, row in enumerate(rules or []):
        if not isinstance(row, dict):
            continue
        row_target_col = str(row.get("target_column_name") or "").strip().upper()
        if row_target_col and target_col_u and row_target_col != target_col_u:
            continue

        canonical = normalize_history_rule_type(str(row.get("rule_type_code") or ""))
        schema_compatible, schema_reason = is_schema_compatible(row, source_schema)
        if schema_compatible is False:
            continue

        enriched = dict(row)
        enriched["candidate_id"] = f"hist_{idx + 1}"
        enriched["target_table_name"] = str(target_table or "").strip() or None
        enriched["target_column_name"] = str(row.get("target_column_name") or target_column or "").strip() or None
        enriched["canonical_rule_type"] = canonical.value if isinstance(canonical, RuleType) else "UNKNOWN"
        enriched["source_hints"] = extract_source_hints(row)
        enriched["schema_compatible"] = schema_compatible
        enriched["schema_compat_reason"] = schema_reason
        enriched["candidate_summary"] = build_history_candidate_summary(enriched)
        out.append(enriched)

    deduped = dedupe_history_rules(out)
    deduped.sort(key=lambda x: _parse_history_timestamp(x.get("last_updated")), reverse=True)
    if max_keep > 0:
        deduped = deduped[: int(max_keep)]
    return deduped


def dedupe_history_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Dedupe by normalized content signature and keep most recent row.
    """
    by_sig: dict[str, dict[str, Any]] = {}
    for row in rules or []:
        if not isinstance(row, dict):
            continue
        sig = _history_signature(row)
        existing = by_sig.get(sig)
        if existing is None:
            by_sig[sig] = row
            continue
        if _parse_history_timestamp(row.get("last_updated")) > _parse_history_timestamp(existing.get("last_updated")):
            by_sig[sig] = row
    return list(by_sig.values())


def build_history_candidate_summary(rule_row: dict[str, Any]) -> str:
    """
    Build a compact textual summary for prompt/rerank/evidence snippets.
    """
    rule_type = str(rule_row.get("canonical_rule_type") or "UNKNOWN").strip()
    src_entity = str(rule_row.get("source_entity_text") or "").strip()
    src_col = str(rule_row.get("source_column_name") or rule_row.get("source_column_text") or "").strip()
    rule_text = str(rule_row.get("rule_text") or "").strip()
    join_text = str(rule_row.get("join_text") or "").strip()
    filter_text = str(rule_row.get("filter_text") or "").strip()

    parts: list[str] = [f"type={rule_type}"]
    if src_entity:
        parts.append(f"src_entity={_truncate(src_entity, 80)}")
    if src_col:
        parts.append(f"src_col={_truncate(src_col, 80)}")
    if join_text:
        parts.append(f"join={_truncate(join_text, 120)}")
    if filter_text:
        parts.append(f"filter={_truncate(filter_text, 100)}")
    if rule_text:
        parts.append(f"rule={_truncate(rule_text, 160)}")
    return " | ".join(parts)


def _build_source_column_normalized_set(source_schema) -> set[str]:
    out: set[str] = set()
    for src in (getattr(source_schema, "files", None) or []):
        for col in (getattr(src, "columns", None) or []):
            physical = str(getattr(col, "physical_name", "") or "").strip()
            logical = str(getattr(col, "logical_name", "") or "").strip()
            if physical:
                out.add(_normalize_identifier(physical))
            if logical:
                out.add(_normalize_identifier(logical))
    return out


def _split_candidate_tokens(text: str) -> list[str]:
    cleaned = str(text or "").replace("\n", " ").replace("\r", " ").strip()
    if not cleaned:
        return []
    raw_tokens = re.split(r"[,\|;/]+", cleaned)
    out: list[str] = []
    for token in raw_tokens:
        token = token.strip()
        if not token:
            continue
        # Preserve likely identifier fragments.
        if len(token) <= 120:
            out.append(token)
    return out


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _parse_history_timestamp(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return datetime.min


def _history_signature(rule_row: dict[str, Any]) -> str:
    parts = [
        str(rule_row.get("canonical_rule_type") or ""),
        str(rule_row.get("source_entity_text") or ""),
        str(rule_row.get("source_column_text") or ""),
        str(rule_row.get("source_column_name") or ""),
        str(rule_row.get("join_text") or ""),
        str(rule_row.get("filter_text") or ""),
        str(rule_row.get("rule_text") or ""),
    ]
    return "|".join(_normalize_identifier(p) for p in parts)


def _truncate(text: str, max_chars: int) -> str:
    t = str(text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max(0, max_chars - 3)].rstrip() + "..."
