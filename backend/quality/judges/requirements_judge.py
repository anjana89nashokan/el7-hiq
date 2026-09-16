from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from utils.gcs_artifact_utils import download_json_uri

from ..prompts import requirements as requirements_prompt
from ..prompts import requirements_extraction as extraction_prompt
from ..schemas import JudgeRequirementsRequest, LayerJudgmentResponse
from ..llm_client import GeminiJudgeClient
from .base import run_layer_judge


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Source-artifact loading
# --------------------------------------------------------------------------- #


def _safe_download_json(uri: Optional[str]) -> Optional[Dict[str, Any]]:
    if not uri:
        return None
    try:
        return download_json_uri(uri)
    except Exception as exc:
        logger.warning("Could not download %s: %s", uri, exc)
        return None


def _normalize_requirement_layer(payload: Dict[str, Any]) -> Dict[str, Any]:
    return (
        payload.get("validated_requirement_layer")
        or payload.get("requirement_layer")
        or payload
    )


def _load_produced_artifacts(
    req: JudgeRequirementsRequest,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Produced artifacts can come three ways:
      1. inline in the request (`requirement_layer`, `file_layout_tables`)
      2. via a GCS URI in `requirement_layer_uri`
      3. derived from `brd_gcs_uri` / `layout_gcs_uri` (BRD upload + layout often
         carry the produced requirement_layer themselves at the standard prefix).
    """
    requirement_layer: Optional[Dict[str, Any]] = req.requirement_layer
    file_layout_tables: Optional[List[Dict[str, Any]]] = req.file_layout_tables

    if requirement_layer is None and req.requirement_layer_uri:
        payload = _safe_download_json(req.requirement_layer_uri) or {}
        requirement_layer = _normalize_requirement_layer(payload)

    if requirement_layer is None:
        # Fall back to brd_gcs_uri: the extract_brd_information endpoint writes the
        # requirement layer to GCS and returns its URI.  The caller may have passed
        # the URI of that same JSON here.
        payload = _safe_download_json(req.brd_gcs_uri) or {}
        requirement_layer = _normalize_requirement_layer(payload)

    if file_layout_tables is None:
        layout_payload = _safe_download_json(req.layout_gcs_uri) or {}
        for key in ("file_layout_tables", "tables", "layout_tables"):
            value = layout_payload.get(key)
            if isinstance(value, list):
                file_layout_tables = value
                break
        if file_layout_tables is None:
            file_layout_tables = []

    if not requirement_layer:
        raise HTTPException(
            status_code=400,
            detail=(
                "requirement_layer could not be located. Provide it inline, via "
                "requirement_layer_uri, or ensure brd_gcs_uri points at the "
                "validated_requirement_layer JSON."
            ),
        )

    return requirement_layer, file_layout_tables


# --------------------------------------------------------------------------- #
# Required / produced enumeration
# --------------------------------------------------------------------------- #


async def _extract_required_items_via_llm(sources: Dict[str, Any]) -> List[dict]:
    """
    First pass LLM call: ask the model to read the raw BRD / layout and output
    an exhaustive checklist of every requirement, rule, and filter it can find.
    """
    client = GeminiJudgeClient()
    user_prompt = extraction_prompt.build_user_prompt(sources=sources)
    raw = await client.judge_json(
        system=extraction_prompt.SYSTEM_INSTRUCTION,
        user=user_prompt
    )
    return raw.get("extracted_items") or []


def _enumerate_produced_items(
    requirement_layer: Dict[str, Any],
    file_layout_tables: List[Dict[str, Any]],
) -> List[dict]:
    items: List[dict] = []

    for key, value in requirement_layer.items():
        items.append(
            {
                "item_id": f"requirement_layer.{key}",
                "category": "requirement_layer_key",
                "label": key,
                "payload": value,
            }
        )

    for t_idx, table in enumerate(file_layout_tables or []):
        if not isinstance(table, dict):
            continue
        table_name = table.get("name") or table.get("table") or f"table_{t_idx}"
        items.append(
            {
                "item_id": f"file_layout_tables.{t_idx}.{table_name}",
                "category": "layout_table",
                "label": str(table_name),
                "payload": {k: v for k, v in table.items() if k != "columns"},
            }
        )
        columns = table.get("columns") or []
        if isinstance(columns, list):
            for c_idx, col in enumerate(columns):
                col_name = (
                    (isinstance(col, dict) and (col.get("name") or col.get("column")))
                    or (isinstance(col, str) and col)
                    or f"col_{c_idx}"
                )
                items.append(
                    {
                        "item_id": f"file_layout_tables.{t_idx}.{table_name}.column.{col_name}",
                        "category": "layout_column",
                        "label": f"{table_name}.{col_name}",
                        "payload": col,
                    }
                )

    return items


# --------------------------------------------------------------------------- #
# Judge orchestration
# --------------------------------------------------------------------------- #


async def judge_requirements(req: JudgeRequirementsRequest) -> LayerJudgmentResponse:
    sources: Dict[str, Any] = {
        "brd": _safe_download_json(req.brd_gcs_uri),
        "layout": _safe_download_json(req.layout_gcs_uri),
        "transcript": _safe_download_json(req.transcript_gcs_uri),
        "brd_markdown": _safe_download_json(req.brd_markdown_gcs_uri),
        "layout_markdown": _safe_download_json(req.layout_markdown_gcs_uri),
    }
    sources = {k: v for k, v in sources.items() if v is not None}

    requirement_layer, file_layout_tables = _load_produced_artifacts(req)

    required_items = await _extract_required_items_via_llm(sources)
    produced_items = _enumerate_produced_items(requirement_layer, file_layout_tables)

    user_prompt = requirements_prompt.build_user_prompt(
        sources=sources,
        requirement_layer=requirement_layer,
        file_layout_tables=file_layout_tables,
        required_items=required_items,
        produced_items=produced_items,
    )

    return await run_layer_judge(
        layer="requirements",
        session_id=req.session_id,
        user_id=req.user_id,
        revision_number=req.revision_number,
        system_instruction=requirements_prompt.SYSTEM_INSTRUCTION,
        user_prompt=user_prompt,
        required_items=required_items,
        produced_items=produced_items,
        extra_artifact_context={
            "brd_gcs_uri": req.brd_gcs_uri,
            "layout_gcs_uri": req.layout_gcs_uri,
            "transcript_gcs_uri": req.transcript_gcs_uri,
            "brd_markdown_gcs_uri": req.brd_markdown_gcs_uri,
            "layout_markdown_gcs_uri": req.layout_markdown_gcs_uri,
            "requirement_layer_uri": req.requirement_layer_uri,
        },
    )
