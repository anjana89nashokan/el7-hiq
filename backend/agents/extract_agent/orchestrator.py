"""
BSA DATAMAP AI Multi-Agent Extract Mapping System — Pipeline Orchestrator
=========================================================================

Central coordinator managing the sequential pipeline with HITL checkpoints:

  Stage 1: Requirement (parse BRD/Layout/Transcript + ambiguity)  → H1 checkpoint
  Stage 2: Driver (generate extract drivers from requirements)     → H2 checkpoint
  Stage 3: Discovery (find source tables/columns)                  → H3 checkpoint
  Stage 4: Metadata (normalize types and names)                    → (auto-approved)
  Stage 5: Mapping (generate final field mappings)                 → H4 checkpoint

The orchestrator does NOT auto-advance through HITL gates.
Each stage is triggered by a separate API call after BSA approval.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from google.adk.agents import SequentialAgent
from google.adk import Runner
from google.adk.apps import App
from utils.adk_runtime import VertexAiSessionService
from google.genai import types

from config.settings import config
from agents.extract_agent.pipeline_models import (
    ApprovedDiscovery,
    ApprovedDrivers,
    ApprovedMetadata,
    ApprovedRequirements,
    FinalMapping,
    MappingEntry,
    PipelineStage,
    PipelineState,
    StageStatus,
)
from judges.h1_requirement.post_judge import PostJudgeH1
from judges.h1_requirement.pre_judge import PreJudgeH1
from judges.h1_requirement.schemas import JudgeInputH1, RequirementModelInput
from models.judge import JudgeVerdict

# Import agent definitions from each layer
from agents.extract_agent.agents import parse_parallel_agent, ambiguity_detector_agent
from agents.extract_agent.driver_agent.agent import (
    standards_search_agent as business_mapping_agent,  # alias — pipeline now uses 2 agents
    logic_builder_agent,
    driver_validator_agent,
)
from agents.extract_agent.discovery_agent.agent import discovery_pipeline_agent
from agents.extract_agent.metadata_agent.agent import metadata_normalizer_agent
from agents.extract_agent.mapping_agent.agent import (
    mapping_row_agent as mapping_generator_agent,
)

logger = logging.getLogger(__name__)

# Pipeline state key in session state
PIPELINE_STATE_KEY = "extract_pipeline_state"
REQUIREMENT_JUDGE_STATE_KEY = "requirement_judge_state"
DRIVER_JUDGE_STATE_KEY = "driver_judge_state"
METADATA_JUDGE_STATE_KEY = "metadata_judge_state"
APP_NAME = config.REASONING_ENGINE_RESOURCE


class ExtractPipelineOrchestrator:
    """
    Manages the 5-stage extract mapping pipeline.

    Each stage is run independently via `run_stage()`. The pipeline does NOT
    auto-advance through HITL checkpoints — the frontend must call
    `approve_stage()` and then `run_stage()` for the next stage.

    State is persisted in Vertex AI session state (MEM1).
    """

    def __init__(
        self,
        project_id: str = config.GOOGLE_CLOUD_PROJECT,
        location: str = config.GOOGLE_CLOUD_LOCATION,
    ):
        self.project_id = project_id
        self.location = location

    # ─── Session Service ────────────────────────────────────────────────

    def _get_session_service(self) -> VertexAiSessionService:
        return VertexAiSessionService(project=self.project_id, location=self.location)

    async def _load_session_state(self, user_id: str, session_id: str) -> dict:
        """Load the full session state."""
        svc = self._get_session_service()
        session = await svc.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        return session.state

    async def _update_session_state(
        self, user_id: str, session_id: str, updates: dict
    ) -> None:
        """Merge updates into session state via append_event."""
        import uuid as _uuid
        from google.adk.events import Event, EventActions

        svc = self._get_session_service()
        session = await svc.get_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )
        event = Event(
            author="system",
            invocation_id=f"sys-{_uuid.uuid4()}",
            actions=EventActions(state_delta=updates),
        )
        await svc.append_event(session=session, event=event)

    # ─── Pipeline State Management ──────────────────────────────────────

    async def get_pipeline_state(self, user_id: str, session_id: str) -> PipelineState:
        """Read the current pipeline state from MEM1."""
        state = await self._load_session_state(user_id, session_id)
        raw = state.get(PIPELINE_STATE_KEY)
        if raw:
            return PipelineState.model_validate(raw)
        # Initialize if not present
        ps = PipelineState(session_id=session_id)
        await self._save_pipeline_state(user_id, session_id, ps)
        return ps

    async def _save_pipeline_state(
        self, user_id: str, session_id: str, ps: PipelineState
    ) -> None:
        """Persist pipeline state to MEM1."""
        ps.updated_at = datetime.utcnow()
        await self._update_session_state(
            user_id, session_id, {PIPELINE_STATE_KEY: ps.model_dump(mode="json")}
        )

    # ─── Stage Execution ────────────────────────────────────────────────

    async def run_stage(
        self,
        stage: PipelineStage,
        user_id: str,
        session_id: str,
    ) -> dict:
        """
        Run a single pipeline stage.

        Validates that prerequisite stages are approved before proceeding.
        Returns a dict with the stage results and status.
        """
        ps = await self.get_pipeline_state(user_id, session_id)

        # Validate prerequisites
        prerequisite = self._get_prerequisite(stage)
        if prerequisite and not ps.is_stage_approved(prerequisite):
            return {
                "status": "error",
                "message": f"Stage '{prerequisite.value}' must be BSA-approved before running '{stage.value}'.",
            }

        # Mark stage as running
        ps.set_stage_status(stage, StageStatus.RUNNING)
        ps.current_stage = stage
        await self._save_pipeline_state(user_id, session_id, ps)

        try:
            result = await self._execute_stage(stage, user_id, session_id, ps)

            # Mark stage as draft_ready (awaiting BSA review)
            # Exception: Metadata stage auto-completes (no HITL)
            if stage == PipelineStage.METADATA:
                ps.set_stage_status(stage, StageStatus.COMPLETED)
                # Auto-build approved metadata from state
                state = await self._load_session_state(user_id, session_id)
                ps.approved_metadata = ApprovedMetadata(
                    normalized_fields=state.get("normalized_metadata", []),
                    normalization_summary=state.get("metadata_summary", {}),
                    completed_at=datetime.utcnow().isoformat(),
                )
            else:
                ps.set_stage_status(stage, StageStatus.DRAFT_READY)

            await self._save_pipeline_state(user_id, session_id, ps)

            return {
                "status": "draft_ready"
                if stage != PipelineStage.METADATA
                else "completed",
                "stage": stage.value,
                "session_id": session_id,
                **result,
            }

        except Exception as e:
            logger.exception(
                "Stage '%s' failed for session '%s'", stage.value, session_id
            )
            ps.set_stage_status(stage, StageStatus.FAILED)
            await self._save_pipeline_state(user_id, session_id, ps)
            return {
                "status": "failed",
                "stage": stage.value,
                "error": str(e),
            }

    async def _execute_stage(
        self,
        stage: PipelineStage,
        user_id: str,
        session_id: str,
        ps: PipelineState,
    ) -> dict:
        """Execute the agent(s) for a specific stage."""
        svc = self._get_session_service()

        if stage == PipelineStage.REQUIREMENT:
            return await self._run_requirement_stage(svc, user_id, session_id)
        elif stage == PipelineStage.DRIVER:
            return await self._run_driver_stage(svc, user_id, session_id)
        elif stage == PipelineStage.DISCOVERY:
            return await self._run_discovery_stage(svc, user_id, session_id)
        elif stage == PipelineStage.METADATA:
            return await self._run_metadata_stage(svc, user_id, session_id)
        elif stage == PipelineStage.MAPPING:
            return await self._run_mapping_stage(svc, user_id, session_id)
        else:
            raise ValueError(f"Unknown stage: {stage}")

    async def _run_requirement_stage(
        self, svc: VertexAiSessionService, user_id: str, session_id: str
    ) -> dict:
        """Run the Requirement Layer (parse + ambiguity detection)."""
        state = await self._load_session_state(user_id, session_id)

        # Build message from stored artifacts
        parts = [
            types.Part.from_text(
                text=(
                    "Parse and analyze all uploaded BRD, layout, and transcript documents. "
                    "Follow your instructions for each document type."
                )
            )
        ]
        message = types.Content(role="user", parts=parts)

        # Run parallel parsing agents
        app = App(name=APP_NAME, root_agent=parse_parallel_agent)
        runner = Runner(app=app, session_service=svc)
        async for _ in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            pass

        # Run ambiguity detection
        state = await self._load_session_state(user_id, session_id)
        summary_text = (
            f"Parsed BRD: {json.dumps(state.get('parsed_brd', {}))}\n"
            f"Parsed Layouts: {json.dumps(state.get('parsed_layouts', []))}\n"
            f"Parsed Transcript: {json.dumps(state.get('parsed_transcript', {}))}"
        )
        summary_msg = types.Content(
            role="user",
            parts=[types.Part.from_text(text=summary_text)],
        )
        amb_app = App(name=APP_NAME, root_agent=ambiguity_detector_agent)
        amb_runner = Runner(app=amb_app, session_service=svc)
        async for _ in amb_runner.run_async(
            user_id=user_id, session_id=session_id, new_message=summary_msg
        ):
            pass

        return {"message": "Requirement parsing and ambiguity detection complete."}

    async def _run_driver_stage(
        self, svc: VertexAiSessionService, user_id: str, session_id: str
    ) -> dict:
        """Run the Driver Layer (Golden Flow)."""
        state = await self._load_session_state(user_id, session_id)

        # Build context message from approved requirements
        approved = state.get("approved_parsed_requirements", {})
        msg_text = (
            f"Generate extract drivers from the approved requirements.\n\n"
            f"Approved BRD: {json.dumps(approved.get('parsed_brd', {}))}\n"
            f"Approved Layouts: {json.dumps(approved.get('parsed_layouts', []))}\n"
            f"Domain Tags: {json.dumps(approved.get('domain_tagged_fields', {}))}\n"
            f"Transcript: {json.dumps(state.get('parsed_transcript', {}))}"
        )
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=msg_text)],
        )

        driver_pipeline = SequentialAgent(
            name="driver_pipeline_agent",
            sub_agents=[
                business_mapping_agent,
                logic_builder_agent,
                driver_validator_agent,
            ],
        )
        app = App(name=APP_NAME, root_agent=driver_pipeline)
        runner = Runner(app=app, session_service=svc)
        async for _ in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            pass

        return {"message": "Driver generation (Golden Flow) complete."}

    async def _run_discovery_stage(
        self, svc: VertexAiSessionService, user_id: str, session_id: str
    ) -> dict:
        """Run the Discovery Layer (Priority Engine)."""
        state = await self._load_session_state(user_id, session_id)

        drivers = state.get("extract_drivers", [])
        # Collect all target fields from drivers
        target_fields = []
        for d in drivers:
            for tf in d.get("target_fields", []):
                fname = tf.get("field_name", "")
                if fname and fname not in target_fields:
                    target_fields.append(fname)

        msg_text = (
            f"Discover source tables and columns for these target fields:\n"
            f"{json.dumps(target_fields)}\n\n"
            f"Extract drivers: {json.dumps(drivers)}"
        )
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=msg_text)],
        )

        app = App(name=APP_NAME, root_agent=discovery_pipeline_agent)
        runner = Runner(app=app, session_service=svc)
        async for _ in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            pass

        return {"message": "Warehouse discovery complete."}

    async def _run_metadata_stage(
        self, svc: VertexAiSessionService, user_id: str, session_id: str
    ) -> dict:
        """Run the Metadata Layer (normalization)."""
        state = await self._load_session_state(user_id, session_id)

        discovery_results = state.get("discovery_results", [])

        msg_text = (
            f"Normalize data types and field names for all discovered fields.\n\n"
            f"Discovery results: {json.dumps(discovery_results)}"
        )
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=msg_text)],
        )

        app = App(name=APP_NAME, root_agent=metadata_normalizer_agent)
        runner = Runner(app=app, session_service=svc)
        async for _ in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            pass

        return {"message": "Metadata normalization complete."}

    async def _run_mapping_stage(
        self, svc: VertexAiSessionService, user_id: str, session_id: str
    ) -> dict:
        """Run the Mapping Layer (IndiMap reuse + generation)."""
        state = await self._load_session_state(user_id, session_id)

        discovery = state.get("discovery_results", [])
        metadata = state.get("normalized_metadata", [])
        drivers = state.get("extract_drivers", [])

        msg_text = (
            f"Generate field-level mappings.\n\n"
            f"Discovery results: {json.dumps(discovery)}\n"
            f"Normalized metadata: {json.dumps(metadata)}\n"
            f"Extract drivers: {json.dumps(drivers)}"
        )
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=msg_text)],
        )

        app = App(name=APP_NAME, root_agent=mapping_generator_agent)
        runner = Runner(app=app, session_service=svc)
        async for _ in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=message
        ):
            pass

        return {"message": "Mapping generation complete."}

    async def invoke_requirement_phase_with_judge(
        self, session_id: str, user_id: str | None = None
    ) -> dict:
        """
        Full requirement phase lifecycle including judge gates.

        This adapts the judge spec onto the existing extract pipeline by storing
        requirement-phase judge data in session state under REQUIREMENT_JUDGE_STATE_KEY.
        """
        resolved_user_id = user_id or getattr(
            config, "APP_SESSION_DEV_USER_ID", "dev-user"
        )
        svc = self._get_session_service()
        ps = await self.get_pipeline_state(resolved_user_id, session_id)
        state = await self._load_session_state(resolved_user_id, session_id)
        judge_state = state.get(REQUIREMENT_JUDGE_STATE_KEY, {}) or {}
        judge_state.setdefault("internal_revision_count", 0)
        judge_state.setdefault("bsa_revision_count", 0)

        post_feedback = state.get(
            f"{PipelineStage.REQUIREMENT.value}_rejection_feedback", ""
        )
        if (
            ps.stage_statuses.get(PipelineStage.REQUIREMENT.value)
            == StageStatus.BSA_REJECTED.value
            and post_feedback
        ):
            requirement_model = self._build_requirement_model_from_state(state)
            post_result = await PostJudgeH1().evaluate(
                JudgeInputH1(
                    session_id=session_id,
                    requirement_model=requirement_model,
                    brd_text=self._extract_brd_text_from_state(state),
                    layout_raw=self._extract_layout_raw_from_state(state),
                    transcript_texts=self._extract_transcript_texts_from_state(state),
                    bsa_rejection_feedback=post_feedback,
                    previous_evaluation=judge_state.get("pre_judge_evaluation"),
                    revision_number=judge_state.get("bsa_revision_count", 0) + 1,
                )
            )
            if post_result.revision_directive is None:
                raise AssertionError("PostJudgeH1 must return a RevisionDirective.")
            judge_state["post_judge_evaluation"] = post_result.evaluation.model_dump(
                mode="json"
            )
            judge_state["revision_directive"] = (
                post_result.revision_directive.model_dump(mode="json")
            )
            judge_state["bsa_revision_count"] = (
                judge_state.get("bsa_revision_count", 0) + 1
            )
            await self._update_session_state(
                resolved_user_id,
                session_id,
                {
                    REQUIREMENT_JUDGE_STATE_KEY: judge_state,
                    "requirement_judge_revision_context": post_result.revision_directive.model_dump(
                        mode="json"
                    ),
                },
            )

        while True:
            await self._run_requirement_stage(svc, resolved_user_id, session_id)
            state = await self._load_session_state(resolved_user_id, session_id)
            requirement_model = self._build_requirement_model_from_state(state)
            pre_result = await PreJudgeH1().evaluate(
                JudgeInputH1(
                    session_id=session_id,
                    requirement_model=requirement_model,
                    brd_text=self._extract_brd_text_from_state(state),
                    layout_raw=self._extract_layout_raw_from_state(state),
                    transcript_texts=self._extract_transcript_texts_from_state(state),
                    previous_evaluation=judge_state.get("pre_judge_evaluation"),
                    revision_number=judge_state.get("internal_revision_count", 0),
                )
            )

            judge_state["requirement_model"] = requirement_model.model_dump(mode="json")
            judge_state["pre_judge_evaluation"] = pre_result.evaluation.model_dump(
                mode="json"
            )
            judge_state["annotated_artifact"] = pre_result.annotated_artifact
            await self._update_session_state(
                resolved_user_id,
                session_id,
                {REQUIREMENT_JUDGE_STATE_KEY: judge_state},
            )

            if (
                pre_result.evaluation.verdict == JudgeVerdict.BLOCK
                and judge_state.get("internal_revision_count", 0) < 2
            ):
                judge_state["internal_revision_count"] = (
                    judge_state.get("internal_revision_count", 0) + 1
                )
                await self._update_session_state(
                    resolved_user_id,
                    session_id,
                    {
                        REQUIREMENT_JUDGE_STATE_KEY: judge_state,
                        "requirement_judge_revision_context": {
                            "source": "pre_judge_block",
                            "rule_scores": [
                                score.model_dump(mode="json")
                                for score in pre_result.evaluation.rule_scores
                            ],
                        },
                    },
                )
                continue

            if pre_result.evaluation.verdict == JudgeVerdict.BLOCK:
                note = "Pre-judge blocked twice; forwarding to BSA anyway because the judge can be wrong and the BSA is the final arbiter."
                pre_result.annotated_artifact["judge_override_note"] = note
                judge_state["annotated_artifact"] = pre_result.annotated_artifact
                judge_state["judge_override_note"] = note
                await self._update_session_state(
                    resolved_user_id,
                    session_id,
                    {REQUIREMENT_JUDGE_STATE_KEY: judge_state},
                )
            else:
                judge_state["internal_revision_count"] = 0

            ps.set_stage_status(PipelineStage.REQUIREMENT, StageStatus.DRAFT_READY)
            ps.current_stage = PipelineStage.REQUIREMENT
            await self._save_pipeline_state(resolved_user_id, session_id, ps)
            return {
                "status": "draft_ready",
                "stage": PipelineStage.REQUIREMENT.value,
                "session_id": session_id,
                "judge_verdict": pre_result.evaluation.verdict.value,
                "internal_revision_count": judge_state.get(
                    "internal_revision_count", 0
                ),
                "annotated_artifact": pre_result.annotated_artifact,
                "evaluation": pre_result.evaluation.model_dump(mode="json"),
            }

    # ─── Stage Approval / Rejection ─────────────────────────────────────

    async def approve_stage(
        self,
        stage: PipelineStage,
        user_id: str,
        session_id: str,
        overrides: Optional[dict] = None,
    ) -> dict:
        """
        Apply BSA overrides and mark a stage as approved.
        This enables the next stage in the pipeline.
        """
        ps = await self.get_pipeline_state(user_id, session_id)

        if ps.stage_statuses.get(stage.value) != StageStatus.DRAFT_READY.value:
            return {
                "status": "error",
                "message": f"Stage '{stage.value}' is not in draft_ready state.",
            }

        state = await self._load_session_state(user_id, session_id)
        now = datetime.utcnow().isoformat()

        # Build approved snapshot based on stage
        if stage == PipelineStage.REQUIREMENT:
            ps.approved_requirements = ApprovedRequirements(
                parsed_brd=state.get("parsed_brd", {}),
                parsed_layouts=state.get("parsed_layouts", []),
                parsed_transcript=state.get("parsed_transcript", {}),
                domain_tagged_fields=state.get("domain_tagged_fields", {}),
                ambiguity_report=state.get("ambiguity_report", {}),
                bsa_overrides=overrides or {},
                approved_at=now,
            )
        elif stage == PipelineStage.DRIVER:
            ps.approved_drivers = ApprovedDrivers(
                drivers=[],  # Will be populated from state
                bsa_overrides=overrides or {},
                approved_at=now,
            )
            # Store raw drivers from state
            raw_drivers = state.get("extract_drivers", [])
            await self._update_session_state(
                user_id,
                session_id,
                {"approved_extract_drivers": raw_drivers},
            )
        elif stage == PipelineStage.DISCOVERY:
            ps.approved_discovery = ApprovedDiscovery(
                discovery_results=state.get("discovery_results", []),
                bsa_overrides=overrides or {},
                approved_at=now,
            )
        elif stage == PipelineStage.MAPPING:
            ps.final_mapping = FinalMapping(
                mappings=state.get("final_mappings", []),
                unmapped_fields=state.get("unmapped_fields", []),
                mapping_summary=state.get("mapping_summary", {}),
                bsa_overrides=overrides or {},
                approved_at=now,
            )

        ps.set_stage_status(stage, StageStatus.BSA_APPROVED)

        # Auto-advance to next stage
        next_stage = ps.get_next_stage()
        if next_stage:
            ps.advance_to(next_stage)

        await self._save_pipeline_state(user_id, session_id, ps)

        return {
            "status": "bsa_approved",
            "stage": stage.value,
            "next_stage": next_stage.value if next_stage else None,
            "message": (
                f"Stage '{stage.value}' approved."
                + (
                    f" Next stage: '{next_stage.value}'."
                    if next_stage
                    else " Pipeline complete!"
                )
            ),
        }

    async def reject_stage(
        self,
        stage: PipelineStage,
        user_id: str,
        session_id: str,
        feedback: str = "",
    ) -> dict:
        """
        Mark a stage as rejected with BSA feedback.
        The stage can be re-run after rejection.
        """
        ps = await self.get_pipeline_state(user_id, session_id)

        if ps.stage_statuses.get(stage.value) != StageStatus.DRAFT_READY.value:
            return {
                "status": "error",
                "message": f"Stage '{stage.value}' is not in draft_ready state.",
            }

        ps.set_stage_status(stage, StageStatus.BSA_REJECTED)
        await self._save_pipeline_state(user_id, session_id, ps)

        # Store rejection feedback in session state
        await self._update_session_state(
            user_id,
            session_id,
            {f"{stage.value}_rejection_feedback": feedback},
        )

        return {
            "status": "bsa_rejected",
            "stage": stage.value,
            "message": f"Stage '{stage.value}' rejected. Re-run the stage after addressing feedback.",
            "feedback": feedback,
        }

    # ─── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _get_prerequisite(stage: PipelineStage) -> Optional[PipelineStage]:
        """Return the prerequisite stage for a given stage, or None for the first stage."""
        prerequisites = {
            PipelineStage.REQUIREMENT: None,
            PipelineStage.DRIVER: PipelineStage.REQUIREMENT,
            PipelineStage.DISCOVERY: PipelineStage.DRIVER,
            PipelineStage.METADATA: PipelineStage.DISCOVERY,
            PipelineStage.MAPPING: PipelineStage.DISCOVERY,  # Metadata auto-completes
        }
        return prerequisites.get(stage)

    @staticmethod
    def _build_requirement_model_from_state(
        state: dict[str, Any],
    ) -> RequirementModelInput:
        parsed_brd = state.get("parsed_brd", {}) or {}
        parsed_layouts = state.get("parsed_layouts", []) or []
        parsed_transcript = state.get("parsed_transcript", {}) or {}
        domain_tagged = state.get("domain_tagged_fields", {}) or {}
        ambiguity_report = state.get("ambiguity_report", {}) or {}

        explicit_filters: list[dict[str, Any]] = []
        for criterion in parsed_brd.get("eligibility_criteria", []) or []:
            explicit_filters.append(
                {
                    "field": "eligibility",
                    "operator": "include",
                    "values": [criterion],
                    "source": "parsed_brd.eligibility_criteria",
                }
            )
        for index, criterion in enumerate(parsed_brd.get("date_criteria", []) or []):
            explicit_filters.append(
                {
                    "field": criterion.get("field_type") or f"date_criteria_{index}",
                    "operator": "date_range",
                    "values": [criterion.get("description")],
                    "source": "parsed_brd.date_criteria",
                }
            )

        output_fields = []
        for layout in parsed_layouts:
            for field in layout.get("fields", []) or []:
                output_fields.append(
                    {
                        "field_name": field.get("attribute_name")
                        or field.get("normalized_name"),
                        "position": field.get("sequence"),
                        "data_type": field.get("data_type"),
                        "source_file_name": layout.get("source_file_name"),
                    }
                )

        transcript_decisions = parsed_transcript.get("decisions", []) or []
        implicit_rules = [
            {
                "rule_description": decision.get("decision_text"),
                "rule_type": decision.get("category"),
                "confidence": 0.8,
                "source": decision.get("source_session"),
            }
            for decision in transcript_decisions
        ]

        ambiguity_items = ambiguity_report.get("ambiguities", []) or []
        conflicts_with_brd = [
            item
            for item in ambiguity_items
            if str(item.get("item_type", "")).lower() == "conflict"
        ]

        scope = {
            "company": (parsed_brd.get("in_scope_items") or [None])[0],
            "LOB": domain_tagged.get("primary_domain"),
            "funding": None,
            "date_range": [
                item.get("description")
                for item in parsed_brd.get("date_criteria", []) or []
            ],
            "constraints": parsed_brd.get("out_of_scope_items", []),
        }
        primary_domain = domain_tagged.get("primary_domain") or "Other"
        return RequirementModelInput(
            extract_purpose=" ".join(parsed_brd.get("in_scope_items", [])[:2]).strip()
            or "Requirement extraction output from BRD, layout, and transcript analysis.",
            scope=scope,
            explicit_filters=explicit_filters,
            compliance_flags=[],
            stakeholder_references=[],
            output_fields=output_fields,
            total_field_count=sum(
                layout.get("field_count", 0) for layout in parsed_layouts
            ),
            implicit_rules=implicit_rules,
            conflicts_with_brd=conflicts_with_brd,
            ambiguities=ambiguity_items,
            blocking_count=sum(
                1
                for item in ambiguity_items
                if str(item.get("severity", "")).upper() == "HIGH"
            ),
            primary_domain=primary_domain,
            sub_domain=primary_domain,
            domain_confidence=0.8,
            complexity_score=max(
                1, (len(output_fields) // 20) + (len(explicit_filters) // 5) + 1
            ),
            recommended_catalogs=[primary_domain]
            if primary_domain and primary_domain != "unknown"
            else [],
            confidence_score=0.75,
            agent_notes=parsed_brd.get("skipped_tbd_items", []) or [],
        )

    @staticmethod
    def _extract_brd_text_from_state(state: dict[str, Any]) -> str:
        return (
            state.get("brd_text")
            or state.get("brd_raw_text")
            or json.dumps(state.get("parsed_brd", {}), indent=2)
        )

    @staticmethod
    def _extract_layout_raw_from_state(state: dict[str, Any]) -> list[dict]:
        return state.get("layout_raw") or state.get("parsed_layouts") or []

    @staticmethod
    def _extract_transcript_texts_from_state(state: dict[str, Any]) -> list[str]:
        if state.get("transcript_texts"):
            return state["transcript_texts"]
        parsed_transcript = state.get("parsed_transcript", {}) or {}
        decisions = parsed_transcript.get("decisions", []) or []
        if decisions:
            return [
                "\n".join(
                    str(decision.get("decision_text") or "") for decision in decisions
                )
            ]
        return []
