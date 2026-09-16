from __future__ import annotations

import logging
from typing import Any, Dict, List

from utils.gcs_artifact_utils import download_json_uri

from ..prompts import driver as driver_prompt
from ..schemas import JudgeDriverRequest, LayerJudgmentResponse
from .base import run_layer_judge


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Required / produced enumeration
# --------------------------------------------------------------------------- #


def _normalize_requirement_layer(brd_payload: Dict[str, Any]) -> Dict[str, Any]:
    """The BRD URI may point at a wrapper {"validated_requirement_layer": {...}}."""
    return brd_payload.get("validated_requirement_layer") or brd_payload


def _enumerate_required_items(req_layer: Dict[str, Any]) -> List[dict]:
    """
    Required items form the completeness checklist: only what the driver layer
    is expected to implement from the BRD requirement layer.

    We enumerate **exactly three** top-level keys and ignore all others (e.g.
    ``scope``, ``file_specs``, narrative sections) — they are not part of the
    driver KPI denominator.

      • filters_and_parameters — every filter concept the BRD listed
      • requirements — every numbered/named functional requirement
      • generic_tables — every target table the driver must reference
    """
    items: List[dict] = []

    filters = req_layer.get("filters_and_parameters") or {}
    if isinstance(filters, dict):
        flat: List[Any] = []
        for value in filters.values():
            if isinstance(value, list):
                flat.extend(value)
            elif value:
                flat.append(value)
        filter_list: List[Any] = flat
    elif isinstance(filters, list):
        filter_list = filters
    else:
        filter_list = []

    for i, f in enumerate(filter_list):
        label = (
            (isinstance(f, dict) and (f.get("concept") or f.get("name") or f.get("field")))
            or (isinstance(f, str) and f)
            or f"filter_{i}"
        )
        items.append(
            {
                "item_id": f"brd.filter.{i}.{label}",
                "category": "brd_filter",
                "label": label,
                "payload": f,
            }
        )

    requirements = req_layer.get("requirements") or []
    if isinstance(requirements, list):
        for i, r in enumerate(requirements):
            label = (
                (isinstance(r, dict) and (r.get("id") or r.get("title") or r.get("name")))
                or (isinstance(r, str) and r[:60])
                or f"req_{i}"
            )
            items.append(
                {
                    "item_id": f"brd.requirement.{i}.{label}",
                    "category": "brd_requirement",
                    "label": label,
                    "payload": r,
                }
            )

    tables = req_layer.get("generic_tables") or []
    if isinstance(tables, list):
        for i, t in enumerate(tables):
            label = (
                (isinstance(t, dict) and (t.get("name") or t.get("table") or t.get("id")))
                or (isinstance(t, str) and t)
                or f"table_{i}"
            )
            items.append(
                {
                    "item_id": f"brd.generic_table.{i}.{label}",
                    "category": "brd_generic_table",
                    "label": str(label),
                    "payload": t,
                }
            )

    return items


def _enumerate_produced_items(
    driver_mapping: Dict[str, Any],
    driver_logic: Dict[str, Any],
    driver_validation: Dict[str, Any],
) -> List[dict]:
    """
    Produced items are everything we enumerate from the three driver outputs
    (mapping, logic, validation): filter candidates, unmapped concepts, common
    filters, ``sql_where_clause``, validation issue lists, and decision flags.
    Each row is graded for hallucination, groundedness, and instruction adherence.
    """
    items: List[dict] = []

    for i, fc in enumerate(driver_mapping.get("filter_candidates") or []):
        label = (
            (isinstance(fc, dict) and (fc.get("filter_id") or fc.get("dart_field") or fc.get("concept")))
            or f"candidate_{i}"
        )
        items.append(
            {
                "item_id": f"driver_mapping.filter_candidate.{i}.{label}",
                "category": "filter_candidate",
                "label": label,
                "payload": fc,
            }
        )

    for i, uc in enumerate(driver_mapping.get("unmapped_concepts") or []):
        label = (
            (isinstance(uc, dict) and (uc.get("concept") or uc.get("name")))
            or (isinstance(uc, str) and uc[:60])
            or f"unmapped_{i}"
        )
        items.append(
            {
                "item_id": f"driver_mapping.unmapped_concept.{i}.{label}",
                "category": "unmapped_concept",
                "label": label,
                "payload": uc,
            }
        )

    for i, cf in enumerate(driver_logic.get("common_filters") or []):
        label = (
            (isinstance(cf, dict) and (cf.get("filter_id") or cf.get("dart_field")))
            or f"filter_{i}"
        )
        items.append(
            {
                "item_id": f"driver_logic.common_filter.{i}.{label}",
                "category": "common_filter",
                "label": label,
                "payload": cf,
            }
        )

    sql_where = driver_logic.get("sql_where_clause")
    if sql_where:
        items.append(
            {
                "item_id": "driver_logic.sql_where_clause",
                "category": "sql_where_clause",
                "label": "sql_where_clause",
                "payload": sql_where,
            }
        )

    issues_seen = 0
    for key in ("issues", "validation_issues", "findings", "high_issues", "medium_issues"):
        raw = driver_validation.get(key)
        if not isinstance(raw, list):
            continue
        for j, issue in enumerate(raw):
            label = (
                (isinstance(issue, dict) and (issue.get("id") or issue.get("title") or issue.get("severity")))
                or f"issue_{j}"
            )
            items.append(
                {
                    "item_id": f"driver_validation.{key}.{j}.{label}",
                    "category": "validation_issue",
                    "label": label,
                    "payload": issue,
                }
            )
            issues_seen += 1

    if "can_proceed" in driver_validation:
        items.append(
            {
                "item_id": "driver_validation.can_proceed",
                "category": "validation_decision",
                "label": "can_proceed",
                "payload": driver_validation.get("can_proceed"),
            }
        )
    if "standards_compliant" in driver_validation:
        items.append(
            {
                "item_id": "driver_validation.standards_compliant",
                "category": "validation_decision",
                "label": "standards_compliant",
                "payload": driver_validation.get("standards_compliant"),
            }
        )

    return items


# --------------------------------------------------------------------------- #
# Judge orchestration
# --------------------------------------------------------------------------- #


async def judge_driver(req: JudgeDriverRequest) -> LayerJudgmentResponse:
    brd_payload = download_json_uri(req.brd_uri)
    req_layer = _normalize_requirement_layer(brd_payload)

    required_items = _enumerate_required_items(req_layer)
    produced_items = _enumerate_produced_items(
        req.driver_mapping, req.driver_logic, req.driver_validation
    )

    user_prompt = driver_prompt.build_user_prompt(
        brd_requirement_layer=req_layer,
        driver_mapping=req.driver_mapping,
        driver_logic=req.driver_logic,
        driver_validation=req.driver_validation,
        required_items=required_items,
        produced_items=produced_items,
    )

    return await run_layer_judge(
        layer="driver",
        session_id=req.sessionId,
        user_id=req.userId,
        revision_number=req.revision_number,
        system_instruction=driver_prompt.SYSTEM_INSTRUCTION,
        user_prompt=user_prompt,
        required_items=required_items,
        produced_items=produced_items,
        extra_artifact_context={"brd_uri": req.brd_uri},
    )
