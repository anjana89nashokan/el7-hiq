"""
Step 3 Main Agent (ADK orchestrator).

Scope for this implementation:
  - Consume Step 2 output (Step2State) and generate a curated list of Step 3 ReviewQuestions.
  - Step 3 does not apply human decisions yet; the UI submission endpoint is a placeholder.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from google.adk.agents import SequentialAgent

from agents.mapping_generation.models import Step2State
from agents.mapping_generation.models import MappingRow
from agents.mapping_review.models import ReviewQuestion, Step3Metadata, Step3ReviewPackage, Step3State
from agents.mapping_review.sub_agents.review_question_builder_agent import (
    review_question_builder_agent,
    run_review_question_builder_agent,
)
from config.settings import config
from utils.mapping_artifact_store import save_json
from utils.step3_capture_utils import build_step3_state_from_ui

logger = logging.getLogger(__name__)

step3_main_agent = SequentialAgent(
    name="step3_main_agent",
    sub_agents=[review_question_builder_agent],
    description="Step 3 orchestrator for HITL review question generation (one sub-agent).",
)


async def run_step3_questions_pipeline(step2_state: Step2State) -> list[ReviewQuestion]:
    """
    Deterministic entrypoint for Step 3 question generation.

    Returns:
      - review_questions: curated, deduped, prioritized list of ReviewQuestion objects.
    """
    return await run_review_question_builder_agent(step2_state=step2_state)


def save_step3_review_package(review_package: Step3ReviewPackage, output_dir: Path) -> str:
    """
    Persist the Step3ReviewPackage as JSON.

    File name convention:
        <run_id>_step3.json
    """
    _ = output_dir
    return save_json("STEP3_REVIEW_PACKAGE", review_package.metadata.run_id, review_package)


def save_step3_state(step3_state: Step3State, output_dir: Path) -> str:
    """
    Persist the Step3State as JSON (post-HITL capture).

    File name convention:
        <run_id>_step3_state.json
    """
    _ = output_dir
    return save_json("STEP3_CAPTURE_STATE", step3_state.metadata.run_id, step3_state)


async def run_step3_review_package_pipeline(
    *,
    step2_state: Step2State,
    output_dir: Path | None = None,
) -> tuple[Step3ReviewPackage, str]:
    """
    Step 3 pipeline (questions + review package snapshot).

    Current behavior:
      - Generate curated questions (deterministic + optional wordsmith)
      - Build a Step3ReviewPackage snapshot for the UI
      - Persist <run_id>_step3.json to RUNS_DIR
    """
    questions = await run_step3_questions_pipeline(step2_state)

    metadata = Step3Metadata(
        run_id=step2_state.metadata.run_id,
        interface_code=step2_state.metadata.interface_code,
        created_at=datetime.utcnow(),
        created_by="Step3MainAgent",
        # This artifact is the pre-HITL snapshot shown to the UI (not the final state after BSA answers).
        schema_version="step3_review_package_v1",
    )

    suggested_order: list[str] = []
    seen_qids: set[str] = set()
    for q in questions:
        if q.question_id in seen_qids:
            continue
        seen_qids.add(q.question_id)
        suggested_order.append(q.question_id)

    review_package = Step3ReviewPackage(
        metadata=metadata,
        step2_metadata=step2_state.metadata,
        step2_snapshot=step2_state,
        review_questions=questions,
        suggested_order=suggested_order,
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = save_step3_review_package(review_package, output_dir)
    return review_package, output_path


async def run_step3_capture_pipeline(
    *,
    step2_state: Step2State,
    review_questions: list[ReviewQuestion],
    changed_rows_payload: list[dict],
    answers_by_question_id: dict[str, str],
    feedbacks_by_row_id: dict[str, str],
    answered_by: str | None = None,
    output_dir: Path | None = None,
) -> tuple[Step3State, str]:
    """
    Step 3.5 pipeline (capture UI answers + edits).

    Behavior:
      - Load baseline from Step2State (provided by caller)
      - Convert UI changes into Step3State (bsa_answers + decisions + outcomes)
      - Persist <run_id>_step3_state.json to RUNS_DIR
    """
    changed_rows: list[MappingRow] = []
    for raw in changed_rows_payload or []:
        try:
            changed_rows.append(MappingRow.model_validate(raw))
        except Exception:
            # Skip malformed rows; backend will still persist answers/other edits.
            continue

    step3_state = build_step3_state_from_ui(
        step2_state=step2_state,
        review_questions=review_questions,
        changed_rows=changed_rows,
        answers_by_question_id=answers_by_question_id or {},
        feedbacks_by_row_id=feedbacks_by_row_id or {},
        answered_by=answered_by,
        created_by="Step3MainAgent",
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = save_step3_state(step3_state, output_dir)

    # Best-effort experience ingestion (BigQuery only) from Step 3.5 capture.
    # Do not block the capture artifact if ingestion fails.
    try:
        from utils.step3_experience_ingestion_utils import ingest_step3_experience_to_bigquery

        ingest_step3_experience_to_bigquery(step2_state=step2_state, step3_state=step3_state, answered_by=answered_by)
    except Exception:
        # Capture should still succeed even if EvidenceHub is not configured yet.
        logger.exception("Step 3.5 experience ingestion failed (best-effort)")
    return step3_state, output_path


__all__ = [
    "step3_main_agent",
    "run_step3_questions_pipeline",
    "run_step3_review_package_pipeline",
    "save_step3_review_package",
    "run_step3_capture_pipeline",
    "save_step3_state",
]
