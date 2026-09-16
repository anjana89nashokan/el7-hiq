from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from pydantic import ValidationError

from ..llm_client import GeminiJudgeClient
from .. import kpi as kpi_mod
from ..persistence import write_judgment
from ..schemas import (
    LayerJudgmentResponse,
    LayerName,
    LlmJudgment,
    PerItemJudgment,
)


logger = logging.getLogger(__name__)


def _coerce_judgments(raw_items: Any) -> List[PerItemJudgment]:
    out: List[PerItemJudgment] = []
    if not isinstance(raw_items, list):
        return out
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(PerItemJudgment.model_validate(entry))
        except ValidationError as exc:
            logger.warning("Skipping malformed per_item_judgment: %s", exc)
    return out


def _build_llm_judgment(raw: Dict[str, Any]) -> Tuple[LlmJudgment, List[PerItemJudgment]]:
    verdict = (raw.get("verdict") or "warn").strip().lower()
    if verdict not in {"pass", "warn", "fail"}:
        verdict = "warn"
    findings_raw = raw.get("findings") or []
    findings = [str(f) for f in findings_raw if f] if isinstance(findings_raw, list) else []
    per_item = _coerce_judgments(raw.get("per_item_judgments"))
    return (
        LlmJudgment(
            verdict=verdict,  # type: ignore[arg-type]
            summary=str(raw.get("summary") or ""),
            findings=findings,
            per_item_judgments=per_item,
        ),
        per_item,
    )


def _fill_missing_judgments(
    per_item: List[PerItemJudgment],
    required_items: List[dict],
    produced_items: List[dict],
) -> List[PerItemJudgment]:
    """
    Ensure every enumerated required/produced item has a corresponding judgment.

    If the LLM skipped an item (or returned fewer judgments than enumerated),
    we synthesize a pessimistic default so the KPI denominators always reflect
    the true enumerated counts.

    • Missing required items → present_in_output=False  (hurts completeness)
    • Missing produced items → supported_by_source=False (hurts groundedness)
    """
    judged_ids = {j.item_id for j in per_item}

    for req in required_items:
        item_id = req.get("item_id", "")
        if item_id and item_id not in judged_ids:
            per_item.append(
                PerItemJudgment(
                    item_id=item_id,
                    item_type="required",
                    present_in_output=False,
                    supported_by_source=None,
                    contradicts_source=None,
                    follows_instructions=False,
                    rationale="Item not judged by LLM; defaulting to absent.",
                )
            )
            judged_ids.add(item_id)

    for prod in produced_items:
        item_id = prod.get("item_id", "")
        if item_id and item_id not in judged_ids:
            per_item.append(
                PerItemJudgment(
                    item_id=item_id,
                    item_type="produced",
                    present_in_output=None,
                    supported_by_source=False,
                    contradicts_source=False,
                    follows_instructions=False,
                    rationale="Item not judged by LLM; defaulting to unsupported.",
                )
            )
            judged_ids.add(item_id)

    return per_item


async def run_layer_judge(
    *,
    layer: LayerName,
    session_id: str,
    user_id: str,
    revision_number: int,
    system_instruction: str,
    user_prompt: str,
    required_items: List[dict],
    produced_items: List[dict],
    extra_artifact_context: Dict[str, Any] | None = None,
) -> LayerJudgmentResponse:
    """
    Shared orchestration: call the LLM, validate per-item judgments, compute the
    4 KPIs deterministically, persist the merged artifact to GCS, and return.
    """
    client = GeminiJudgeClient()
    raw = await client.judge_json(system=system_instruction, user=user_prompt)

    llm_judgment, per_item = _build_llm_judgment(raw)

    # Safety net: fill in pessimistic defaults for any enumerated items the LLM
    # did not return judgments for, so KPI denominators always match the true
    # enumerated counts.
    llm_count = len(per_item)
    per_item = _fill_missing_judgments(per_item, required_items, produced_items)
    filled_count = len(per_item) - llm_count
    if filled_count > 0:
        logger.warning(
            "[%s] LLM skipped %d enumerated items; filled with pessimistic defaults "
            "(required_expected=%d, produced_expected=%d, llm_returned=%d).",
            layer, filled_count, len(required_items), len(produced_items), llm_count,
        )
    # Update the llm_judgment object so the persisted artifact has the full list
    llm_judgment.per_item_judgments = per_item

    kpis = kpi_mod.compute(per_item)
    judged_at = datetime.now(timezone.utc).isoformat()

    artifact: Dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "layer": layer,
        "revision_number": revision_number,
        "judged_at": judged_at,
        "source": {
            "required_item_count": len(required_items),
            "produced_item_count": len(produced_items),
            "llm_judged_count": llm_count,
            "filled_default_count": filled_count,
            **(extra_artifact_context or {}),
        },
        "llm_judgment": llm_judgment.model_dump(),
        "kpis": {k: v.model_dump() for k, v in kpis.items()},
    }
    artifact_uri = write_judgment(
        session_id=session_id,
        layer=layer,
        revision_number=revision_number,
        payload=artifact,
    )

    return LayerJudgmentResponse(
        success=True,
        session_id=session_id,
        layer=layer,
        revision_number=revision_number,
        judged_at=judged_at,
        kpis=kpis,
        llm_judgment=llm_judgment,
        artifact_gcs_uri=artifact_uri,
    )
