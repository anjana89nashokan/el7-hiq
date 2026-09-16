from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from quality.judges.driver_judge import judge_driver
from quality.judges.mapping_judge import judge_mapping
from quality.judges.metadata_judge import judge_metadata
from quality.judges.requirements_judge import judge_requirements
from quality.schemas import (
    JudgeDriverRequest,
    JudgeMappingRequest,
    JudgeMetadataRequest,
    JudgeRequirementsRequest,
    LayerJudgmentResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter()


def _raise_http(exc: Exception, label: str) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    logger.exception("[%s] failed: %s", label, exc)
    raise HTTPException(status_code=500, detail=f"{label} failed: {exc}") from exc


@router.post(
    "/requirements/judge",
    response_model=LayerJudgmentResponse,
    summary="Judge the requirements layer output and compute the 4 quality KPIs",
)
async def requirements_judge_endpoint(
    req: JudgeRequirementsRequest,
) -> LayerJudgmentResponse:
    logger.info(
        "[quality/requirements/judge] user=%s session=%s rev=%s brd=%s layout=%s",
        req.user_id, req.session_id, req.revision_number,
        req.brd_gcs_uri, req.layout_gcs_uri,
    )
    try:
        return await judge_requirements(req)
    except Exception as exc:
        _raise_http(exc, "quality/requirements/judge")
        raise  # unreachable, satisfies type checker


@router.post(
    "/metadata/judge",
    response_model=LayerJudgmentResponse,
    summary="Judge the metadata extractor output and compute the 4 quality KPIs",
)
async def metadata_judge_endpoint(req: JudgeMetadataRequest) -> LayerJudgmentResponse:
    logger.info(
        "[quality/metadata/judge] user=%s session=%s rev=%s brd=%s layout=%s",
        req.userId, req.sessionId, req.revision_number, req.brd_uri, req.layout_uri,
    )
    try:
        return await judge_metadata(req)
    except Exception as exc:
        _raise_http(exc, "quality/metadata/judge")
        raise


@router.post(
    "/mapping/judge",
    response_model=LayerJudgmentResponse,
    summary="Judge the mapping_result output and compute the 4 quality KPIs",
)
async def mapping_judge_endpoint(req: JudgeMappingRequest) -> LayerJudgmentResponse:
    logger.info(
        "[quality/mapping/judge] user=%s session=%s rev=%s brd=%s driver=%s metadata=%s",
        req.userId, req.sessionId, req.revision_number,
        req.brd_uri, req.driver_uri, req.metadata_uri,
    )
    try:
        return await judge_mapping(req)
    except Exception as exc:
        _raise_http(exc, "quality/mapping/judge")
        raise


@router.post(
    "/driver/judge",
    response_model=LayerJudgmentResponse,
    summary="Judge the driver pipeline outputs and compute the 4 quality KPIs",
)
async def driver_judge_endpoint(req: JudgeDriverRequest) -> LayerJudgmentResponse:
    logger.info(
        "[quality/driver/judge] user=%s session=%s rev=%s brd_uri=%s",
        req.userId, req.sessionId, req.revision_number, req.brd_uri,
    )
    try:
        return await judge_driver(req)
    except Exception as exc:
        _raise_http(exc, "quality/driver/judge")
        raise
