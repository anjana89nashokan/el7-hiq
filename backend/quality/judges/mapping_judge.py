from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import HTTPException

from utils.gcs_artifact_utils import download_json_uri

from ..prompts import mapping as mapping_prompt
from ..schemas import JudgeMappingRequest, LayerJudgmentResponse
from .base import run_layer_judge


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Source-artifact loading
# --------------------------------------------------------------------------- #


def _normalize_requirement_layer(brd_payload: Dict[str, Any]) -> Dict[str, Any]:
    return brd_payload.get("validated_requirement_layer") or brd_payload


def _normalize_driver_layer(driver_payload: Dict[str, Any]) -> Dict[str, Any]:
    return (
        driver_payload.get("approved_driver_layer")
        or driver_payload.get("driver_layer")
        or driver_payload
    )


def _normalize_metadata_layer(metadata_payload: Dict[str, Any]) -> Dict[str, Any]:
    return (
        metadata_payload.get("final_metadata_output")
        or metadata_payload.get("extracted_metadata")
        or metadata_payload
    )


def _load_mapping_result(req: JudgeMappingRequest) -> Dict[str, Any]:
    if req.mapping_result is not None:
        return req.mapping_result
    if req.mapping_uri:
        return download_json_uri(req.mapping_uri)
    raise HTTPException(
        status_code=400,
        detail="Either mapping_result (inline) or mapping_uri must be provided.",
    )


# --------------------------------------------------------------------------- #
# Required / produced enumeration
# --------------------------------------------------------------------------- #


def _enumerate_required_items(
    req_layer: Dict[str, Any],
    metadata_layer: Dict[str, Any],
) -> List[dict]:
    """
    Required = every target attribute the BRD demands that the metadata says exists
    on the source side.  For simplicity we enumerate metadata-declared attributes:
    every one of them is a candidate that the mapping stage must address (either
    map it forward, or document that it's intentionally not mapped).
    """
    items: List[dict] = []

    file1 = metadata_layer.get("extracted_file1") or metadata_layer.get("file1") or {}
    file1_list = file1 if isinstance(file1, list) else [file1] if isinstance(file1, dict) else []
    for f_idx, file_rec in enumerate(file1_list):
        if not isinstance(file_rec, dict):
            continue
        attributes = file_rec.get("attributes") or []
        if not isinstance(attributes, list):
            continue
        for a_idx, attr in enumerate(attributes):
            attr_name = (
                (isinstance(attr, dict) and (attr.get("name") or attr.get("attribute") or attr.get("column")))
                or f"attr_{a_idx}"
            )
            items.append(
                {
                    "item_id": f"required.target_attribute.file{f_idx + 1}.{attr_name}",
                    "category": "target_attribute",
                    "label": attr_name,
                    "payload": attr,
                }
            )

    # Also enumerate BRD requirements as a separate category — each requirement
    # should be reflected in at least one mapping or transformation rule.
    requirements = req_layer.get("requirements") or []
    if isinstance(requirements, list):
        for i, r in enumerate(requirements):
            label = (
                (isinstance(r, dict) and (r.get("id") or r.get("title")))
                or (isinstance(r, str) and r[:60])
                or f"req_{i}"
            )
            items.append(
                {
                    "item_id": f"required.brd_requirement.{i}.{label}",
                    "category": "brd_requirement",
                    "label": label,
                    "payload": r,
                }
            )

    return items


def _enumerate_produced_items(mapping_result: Dict[str, Any]) -> List[dict]:
    items: List[dict] = []

    rows: List[Any] = []
    for key in ("mappings", "rows", "mapping_rows", "items", "field_mappings"):
        value = mapping_result.get(key)
        if isinstance(value, list):
            rows = value
            break

    if not rows and isinstance(mapping_result.get("results"), list):
        rows = mapping_result.get("results")  # type: ignore[assignment]

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        target = (
            row.get("target")
            or row.get("target_attribute")
            or row.get("target_column")
            or f"row_{i}"
        )
        items.append(
            {
                "item_id": f"mapping.row.{i}.{target}",
                "category": "mapping_row",
                "label": str(target),
                "payload": row,
            }
        )

    transformations = mapping_result.get("transformations") or []
    if isinstance(transformations, list):
        for i, t in enumerate(transformations):
            label = (
                (isinstance(t, dict) and (t.get("id") or t.get("name")))
                or f"transform_{i}"
            )
            items.append(
                {
                    "item_id": f"mapping.transformation.{i}.{label}",
                    "category": "transformation",
                    "label": str(label),
                    "payload": t,
                }
            )

    business_rules = mapping_result.get("business_rules") or []
    if isinstance(business_rules, list):
        for i, br in enumerate(business_rules):
            label = (
                (isinstance(br, dict) and (br.get("id") or br.get("rule")))
                or f"rule_{i}"
            )
            items.append(
                {
                    "item_id": f"mapping.business_rule.{i}.{label}",
                    "category": "business_rule",
                    "label": str(label)[:80],
                    "payload": br,
                }
            )

    return items


# --------------------------------------------------------------------------- #
# Judge orchestration
# --------------------------------------------------------------------------- #


async def judge_mapping(req: JudgeMappingRequest) -> LayerJudgmentResponse:
    brd_payload = download_json_uri(req.brd_uri)
    driver_payload = download_json_uri(req.driver_uri)
    metadata_payload = download_json_uri(req.metadata_uri)

    req_layer = _normalize_requirement_layer(brd_payload)
    driver_layer = _normalize_driver_layer(driver_payload)
    metadata_layer = _normalize_metadata_layer(metadata_payload)
    mapping_result = _load_mapping_result(req)

    required_items = _enumerate_required_items(req_layer, metadata_layer)
    produced_items = _enumerate_produced_items(mapping_result)

    user_prompt = mapping_prompt.build_user_prompt(
        brd_requirement_layer=req_layer,
        driver_layer=driver_layer,
        metadata_layer=metadata_layer,
        mapping_result=mapping_result,
        required_items=required_items,
        produced_items=produced_items,
    )

    return await run_layer_judge(
        layer="mapping",
        session_id=req.sessionId,
        user_id=req.userId,
        revision_number=req.revision_number,
        system_instruction=mapping_prompt.SYSTEM_INSTRUCTION,
        user_prompt=user_prompt,
        required_items=required_items,
        produced_items=produced_items,
        extra_artifact_context={
            "brd_uri": req.brd_uri,
            "driver_uri": req.driver_uri,
            "metadata_uri": req.metadata_uri,
            "mapping_uri": req.mapping_uri,
        },
    )
