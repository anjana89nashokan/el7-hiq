from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agents.extract_agent.orchestrator import (
    REQUIREMENT_JUDGE_STATE_KEY,
    ExtractPipelineOrchestrator,
)
from api.dependencies.auth import resolve_current_user
from judges.base_judge import AuditEventType, audit_trail
from models.judge import JudgeVerdict

router = APIRouter(prefix="/api/v1", tags=["judge"])


async def _load_judge_state(request: Request, session_id: str) -> tuple[ExtractPipelineOrchestrator, str, dict]:
    current_user = resolve_current_user(request)
    orchestrator = ExtractPipelineOrchestrator()
    state = await orchestrator._load_session_state(current_user.user_key, session_id)
    return orchestrator, current_user.user_key, state.get(REQUIREMENT_JUDGE_STATE_KEY, {}) or {}


@router.get("/judge/{session_id}/h1/evaluation")
async def get_h1_judge_evaluation(session_id: str, request: Request) -> dict:
    orchestrator, _, judge_state = await _load_judge_state(request, session_id)
    _ = orchestrator
    evaluation = judge_state.get("post_judge_evaluation") or judge_state.get("pre_judge_evaluation")
    if not evaluation:
        raise HTTPException(status_code=404, detail="No H1 judge evaluation exists for this session.")
    return evaluation


@router.get("/judge/{session_id}/h1/revision-directive")
async def get_h1_revision_directive(session_id: str, request: Request) -> dict:
    orchestrator, _, judge_state = await _load_judge_state(request, session_id)
    _ = orchestrator
    directive = judge_state.get("revision_directive")
    if not directive:
        raise HTTPException(status_code=404, detail="No H1 revision directive exists for this session.")
    return directive


@router.post("/judge/{session_id}/h1/override")
async def override_h1_judge_verdict(
    session_id: str,
    override_verdict: str,
    reason: str,
    reviewer_id: str,
    request: Request,
) -> dict:
    normalized_override = override_verdict.strip().lower()
    if normalized_override not in {JudgeVerdict.WARN.value, JudgeVerdict.PASS.value}:
        raise HTTPException(status_code=422, detail="override_verdict must be 'warn' or 'pass'.")

    orchestrator, user_key, judge_state = await _load_judge_state(request, session_id)
    evaluation = judge_state.get("pre_judge_evaluation")
    if not evaluation:
        raise HTTPException(status_code=404, detail="No pre-judge evaluation exists for this session.")
    if str(evaluation.get("verdict", "")).lower() != JudgeVerdict.BLOCK.value:
        raise HTTPException(status_code=409, detail="Session is not currently blocked by the pre-judge.")

    original_verdict = evaluation["verdict"]
    evaluation["verdict"] = normalized_override
    judge_state["pre_judge_evaluation"] = evaluation
    judge_state["judge_override"] = {
        "reviewer_id": reviewer_id,
        "reason": reason,
        "original_verdict": original_verdict,
        "override_verdict": normalized_override,
    }
    annotated_artifact = judge_state.get("annotated_artifact", {}) or {}
    annotated_artifact["judge_override_note"] = reason
    judge_state["annotated_artifact"] = annotated_artifact

    audit_trail.record(
        AuditEventType.JUDGE_VERDICT_OVERRIDDEN,
        session_id,
        reviewer_id=reviewer_id,
        reason=reason,
        original_verdict=original_verdict,
        override_verdict=normalized_override,
    )
    await orchestrator._update_session_state(
        user_key,
        session_id,
        {REQUIREMENT_JUDGE_STATE_KEY: judge_state},
    )
    return {
        "status": "overridden",
        "session_id": session_id,
        "original_verdict": original_verdict,
        "override_verdict": normalized_override,
        "reason": reason,
        "reviewer_id": reviewer_id,
    }
