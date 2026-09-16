"""
Step 4 - normalization utilities (deterministic, no LLM).

Why this exists:
  - After rule changes, some fields become irrelevant (e.g., joins for DIRECT).
  - We keep logic centralized in utils, not in agents or routers.
"""

from __future__ import annotations

from typing import Dict, Set

from agents.mapping_generation.models import JoinCondition, JoinKeyPair, MappingRow, RuleType


def build_allowed_identifiers_for_row(row: MappingRow) -> set[str]:
    """
    Build an allowlist of identifiers already present in the row state.

    Used to prevent LLM text regeneration from introducing new identifiers.
    """
    out: set[str] = set()

    # Target identifiers (immutable but allowed to mention).
    out.add(f"{row.target_table.entity_id}.{row.target_column_name}")
    out.add(row.target_table.entity_id)
    out.add(row.target_column_name)

    # Source identifiers.
    if row.source_entity:
        out.add(row.source_entity.entity_id)
        for c in (row.source_field_names or []):
            out.add(c)
            out.add(f"{row.source_entity.entity_id}.{c}")

    for lt in (row.lookup_tables or []):
        out.add(lt.entity_id)

    if row.join_condition and (row.join_condition.join_text or ""):
        out.add(row.join_condition.join_text)

    return out


def _source_signature(row: MappingRow) -> tuple[str | None, tuple[str, ...]]:
    source_id = row.source_entity.entity_id if row.source_entity else None
    source_fields = tuple(str(x) for x in (row.source_field_names or []))
    return source_id, source_fields


def _lookup_tables_signature(row: MappingRow) -> tuple[tuple[str, str], ...]:
    pairs = []
    for lt in row.lookup_tables or []:
        pairs.append((str(lt.entity_type), str(lt.entity_id)))
    return tuple(sorted(pairs))


def _join_key_pair_signature(pair: JoinKeyPair) -> tuple:
    return (
        str(pair.left_entity.entity_type),
        str(pair.left_entity.entity_id),
        tuple(str(c) for c in (pair.left_columns or [])),
        str(pair.right_entity.entity_type),
        str(pair.right_entity.entity_id),
        tuple(str(c) for c in (pair.right_columns or [])),
    )


def _join_signature(join: JoinCondition | None) -> tuple | None:
    if not join:
        return None
    keys = tuple(_join_key_pair_signature(k) for k in (join.join_keys or []))
    return (
        bool(join.is_required),
        bool(join.is_unknown),
        str(join.join_text or ""),
        keys,
    )


def apply_rule_family_normalization(
    *,
    row: MappingRow,
    baseline_row: MappingRow | None,
    locked_fields: Set[str],
) -> None:
    """
    Normalize row fields after structural mapping changes.

    Policy (deterministic):
      - If rule_type is not LOOKUP:
          * join_condition is cleared
          * lookup_tables is cleared
      - If any structural mapping input changed vs baseline (rule/source/lookup/join),
        clear unlocked text fields so Step 4 text regeneration rewrites them from current state.

    Notes:
      - We intentionally keep this minimal and conservative.
      - Text regeneration is handled by Subagent D.
    """
    rt = row.rule_type

    if rt != RuleType.LOOKUP:
        # Clear join/lookup artifacts that no longer apply.
        row.join_condition = None
        row.lookup_tables = []

    structural_changed = False
    if baseline_row is not None:
        structural_changed = any(
            [
                baseline_row.rule_type != row.rule_type,
                _source_signature(baseline_row) != _source_signature(row),
                _lookup_tables_signature(baseline_row) != _lookup_tables_signature(row),
                _join_signature(baseline_row.join_condition) != _join_signature(row.join_condition),
            ]
        )

    # If structural mapping changed, clear stale text fields (unless locked by feedback).
    if structural_changed:
        if "transformation_rules_text" not in locked_fields:
            row.transformation_rules_text = None
        if "row_filter_text" not in locked_fields:
            row.row_filter_text = None
        if "special_considerations_text" not in locked_fields:
            row.special_considerations_text = None


def build_feedback_locks_from_change_log(*, change_log: list) -> dict[str, set[str]]:
    """
    Build a per-row lock set for fields set by BSA feedback.
    """
    out: dict[str, set[str]] = {}
    for ch in change_log or []:
        if getattr(ch, "source", None) and ch.source.value == "BSA_FEEDBACK":
            out.setdefault(ch.row_id, set()).add(ch.field_name)
    return out


def filter_plans_by_locked_fields(
    *,
    plans: list,
    locked_fields_by_row_id: Dict[str, Set[str]],
) -> list:
    """
    Remove updates that attempt to override feedback-locked fields unless they are also feedback-driven.
    """
    filtered = []
    for plan in plans or []:
        locks = locked_fields_by_row_id.get(plan.row_id, set())
        if not locks:
            filtered.append(plan)
            continue

        kept_updates = []
        for u in plan.updates or []:
            if u.field_name in locks and u.source.value != "BSA_FEEDBACK":
                # Skip non-feedback updates to feedback-locked fields.
                continue
            kept_updates.append(u)
        filtered.append(plan.model_copy(update={"updates": kept_updates}))
    return filtered

