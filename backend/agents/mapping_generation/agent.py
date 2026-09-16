"""
Step 2 Main Agent (ADK orchestrator).

Responsibilities:
    - Build Step 2 work context from Step 1 SharedState (scope + overrides + filters)
    - Invoke MappingLogic sub-agent (rule typing + candidate pick)
    - (Later) Invoke Join/Filter and Post-Processor sub-agents
    - Persist Step2State JSON (<run_id>_step2.json)

LLM:
    - Step 2 is heuristic-first, but MappingLogicAgent does use structured LLM calls for safe sub-tasks:
        - semantic similarity scoring for ambiguous candidate sets
        - constrained rule-type tie-breaks for ambiguous cases
        - candidate re-ranking when top candidates are very close
        - (when STEP2_RAG_ENABLED=true) evidence interpretation + self-check + CASE/IF_ELSE instance drafting
    - All LLM calls must return structured output and must not introduce new entities/columns.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Tuple

from google.adk.agents import SequentialAgent

from agents.mapping_generation.models import Step2Metadata, Step2State
from agents.mapping_ingestion.models import SharedState  # Step 1 output
from agents.mapping_generation.sub_agents.mapping_logic_agent import (
    mapping_logic_agent,
    run_mapping_logic_agent,
)
from agents.mapping_generation.sub_agents.join_filter_agent import (
    join_and_filter_agent,
    run_join_and_filter_agent,
)
from agents.mapping_generation.sub_agents.post_processor_agent import (
    post_processor_agent,
    run_post_processor_agent,
)
from config.settings import config
from utils.mapping_artifact_store import save_json
from utils.step2_shared_tools import build_work_context

# ADK main agent definition (structural wiring only)
step2_main_agent = SequentialAgent(
    name="step2_main_agent",
    sub_agents=[
        mapping_logic_agent,
        join_and_filter_agent,
        post_processor_agent,
    ],
    description=(
        "Step 2 orchestrator for draft mapping generation. "
        "Runs mapping logic, join/filter enrichment, and post-processing in order."
    ),
)


def save_step2_state(step2_state: Step2State, output_dir: Path) -> str:
    """
    Persist the Step2State as JSON.

    File name convention:
        <run_id>_step2.json
    """
    _ = output_dir
    return save_json("STEP2_STATE", step2_state.metadata.run_id, step2_state)


async def run_step2_draft_pipeline(
    shared_state: SharedState,
    output_dir: Path | None = None,
) -> Tuple[str, str]:
    """
    Step 2 pipeline (AG1 + AG2 + AG3).

    Current behavior:
      - AG1: rule typing + candidate selection + self-check scaffolding
      - AG2: join + common-filter enrichment
      - AG3: transformation text + validation + question_candidates generation
    """
    run_id = shared_state.run_id

    ctx = build_work_context(shared_state)
    mapping_rows, issues = await run_mapping_logic_agent(ctx)
    mapping_rows, issues, table_common_filters = await run_join_and_filter_agent(ctx, mapping_rows, issues)
    mapping_rows, issues, question_candidates = await run_post_processor_agent(
        ctx, mapping_rows, issues, table_common_filters
    )

    step2_state = Step2State(
        metadata=Step2Metadata(
            run_id=run_id,
            interface_code=shared_state.interface_code,
            created_at=datetime.utcnow(),
            created_by="step2_main_agent",
            rag_enabled=bool(ctx.rag_enabled),
        ),
        column_mappings=mapping_rows,
        table_common_filters=table_common_filters,
        open_issues=issues,
        question_candidates=question_candidates,
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = save_step2_state(step2_state, output_dir)
    return run_id, output_path


async def run_step2_pipeline(
    shared_state: SharedState,
    output_dir: Path | None = None,
) -> Tuple[str, str]:
    """
    Deterministic entrypoint for Step 2.

    Current behavior:
        - Build work context
        - Run AG1 mapping logic
        - Run AG2 join/filter enrichment
        - Run AG3 post-processing
        - Persist Step2State
    """
    run_id = shared_state.run_id

    ctx = build_work_context(shared_state)
    mapping_rows, issues = await run_mapping_logic_agent(ctx)
    mapping_rows, issues, table_common_filters = await run_join_and_filter_agent(ctx, mapping_rows, issues)
    mapping_rows, issues, question_candidates = await run_post_processor_agent(
        ctx, mapping_rows, issues, table_common_filters
    )

    step2_state = Step2State(
        metadata=Step2Metadata(
            run_id=run_id,
            interface_code=shared_state.interface_code,
            created_at=datetime.utcnow(),
            created_by="step2_main_agent",
            rag_enabled=bool(ctx.rag_enabled),
        ),
        column_mappings=mapping_rows,
        table_common_filters=table_common_filters,
        open_issues=issues,
        question_candidates=question_candidates,
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = save_step2_state(step2_state, output_dir)
    return run_id, output_path


__all__ = ["step2_main_agent", "run_step2_pipeline", "run_step2_draft_pipeline"]
