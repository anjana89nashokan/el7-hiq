import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from uuid import uuid4

from google.adk.agents import ParallelAgent, SequentialAgent

from agents.mapping_ingestion.models import SharedState
from agents.mapping_ingestion.sub_agents.data_model_agent import data_model_agent
from agents.mapping_ingestion.sub_agents.instruction_agent import (
    instruction_agent,
    run_instruction_agent,
)
from agents.mapping_ingestion.sub_agents.source_metadata_agent import source_metadata_agent
from agents.mapping_ingestion.sub_agents.target_metadata_agent import target_metadata_agent
from config.settings import config
from utils.mapping_ingestion_tools import (
    build_data_model_graph,
    build_source_schema,
    build_target_schema,
    save_shared_state,
)
from utils.mapping_artifact_store import save_json
from utils.erwin_graph_merge import merge_subject_area_graphs
from utils.graph_artifact_loader import load_latest_graph_artifact
from utils.indemap_target_metadata_utils import (
    build_target_schema_from_indemap_json,
    fetch_indemap_target_metadata,
)

logger = logging.getLogger(__name__)

# ADK main agent definition (structural, used when running via Runner)
# First two sub-agents (source/target) run in parallel, then data model, then instruction
parallel_sources_targets = ParallelAgent(
    name="source_target_parallel",
    sub_agents=[source_metadata_agent, target_metadata_agent],
    description="Runs source and target metadata parsing in parallel.",
)

main_agent = SequentialAgent(
    name="metadata_ingestion_main_agent",
    sub_agents=[
        parallel_sources_targets,
        data_model_agent,
        instruction_agent,
    ],
    description=(
        "Step 1 orchestrator for mapping ingestion. "
        "Parses source/target metadata, builds a data model graph, "
        "and extracts mapping instructions into MappingContext. "
        "Source/Target parsing run in parallel; instruction agent uses LLM."
    ),
)


async def run_ingestion_pipeline(
    interface_code: str,
    source_files: List[str],
    target_files: List[str],
    instructions_text: str | None = None,
    subject_areas: List[str] | None = None,
    target_layout: str = "UPLOAD_FILES",
    target_db_table_pairs: List[dict[str, str]] | None = None,
    output_dir: Path | None = None,
) -> Tuple[str, str]:
    """
    Deterministic helper for FastAPI endpoint.
    Executes the same steps as the agents but directly in Python.
    """
    logger.info("Running ingestion pipeline for interface_code=%s", interface_code)

    layout = str(target_layout or "UPLOAD_FILES").strip().upper()
    if layout == "INDEMAP":
        source_schema = await asyncio.to_thread(build_source_schema, interface_code, [Path(p) for p in source_files])
        raw_payload = await asyncio.to_thread(
            fetch_indemap_target_metadata,
            db_table_pairs=[
                {
                    "database_name": str(x.get("database_name") or "").strip(),
                    "table_name": str(x.get("table_name") or "").strip(),
                }
                for x in (target_db_table_pairs or [])
                if isinstance(x, dict)
            ],
        )
        target_schema = await asyncio.to_thread(
            build_target_schema_from_indemap_json,
            interface_code=interface_code,
            payload=raw_payload,
        )
    else:
        source_task = asyncio.to_thread(build_source_schema, interface_code, [Path(p) for p in source_files])
        target_task = asyncio.to_thread(build_target_schema, interface_code, [Path(p) for p in target_files])
        source_schema, target_schema = await asyncio.gather(source_task, target_task)

    run_id = f"mapping_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:12]}"
    selected_subject_areas = [str(sa).strip() for sa in (subject_areas or []) if str(sa).strip()]
    selected_subject_area = selected_subject_areas[0] if len(selected_subject_areas) == 1 else None
    graph_artifact_path: str | None = None
    if selected_subject_areas and bool(getattr(config, "STEP1_GRAPH_BY_SUBJECT_ENABLED", True)):
        graphs_with_sources = []
        for subject_area in selected_subject_areas:
            graph, graph_path = await asyncio.to_thread(
                load_latest_graph_artifact,
                subject_area=subject_area,
            )
            graphs_with_sources.append((subject_area, graph, graph_path))
        if len(graphs_with_sources) == 1:
            _only_subject, graph, graph_path = graphs_with_sources[0]
            data_model_graph = graph
            graph_artifact_path = graph_path
        else:
            data_model_graph = await asyncio.to_thread(
                merge_subject_area_graphs,
                run_id=run_id,
                subject_areas=selected_subject_areas,
                graphs_with_sources=graphs_with_sources,
            )
            graph_artifact_path = await asyncio.to_thread(
                save_json,
                "STEP1_MERGED_GRAPH",
                run_id,
                data_model_graph,
            )
    else:
        data_model_graph = build_data_model_graph(interface_code, source_schema, target_schema)

    mapping_context = await run_instruction_agent(
        interface_code=interface_code,
        source_schema=source_schema,
        target_schema=target_schema,
        data_model_graph=data_model_graph,
        instructions_text=instructions_text or "",
    )

    shared_state = SharedState(
        run_id=run_id,
        interface_code=interface_code,
        source_schema=source_schema,
        target_schema=target_schema,
        data_model_graph=data_model_graph,
        mapping_context=mapping_context,
        graph_subject_area=selected_subject_area,
        graph_subject_areas=selected_subject_areas,
        graph_artifact_path=graph_artifact_path,
        created_at=datetime.utcnow(),
        created_by="metadata_ingestion_agent",
    )

    output_dir = output_dir or Path(config.RUNS_DIR)
    output_path = await asyncio.to_thread(save_shared_state, shared_state, output_dir)
    logger.info("SharedState saved to %s", output_path)
    return run_id, output_path


__all__ = ["main_agent", "run_ingestion_pipeline"]
