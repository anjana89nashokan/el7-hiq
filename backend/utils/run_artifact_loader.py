"""
Run artifact loader utilities (GCS mapping artifacts only, no LLM).

Purpose:
  - Centralize run_id-based loading for Step 1/2/3/4 artifacts.
  - Keep API routers thin and deterministic.
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import HTTPException

from agents.mapping_generation.models import Step2State
from agents.mapping_ingestion.models import SharedState
from agents.mapping_review.models import Step3ReviewPackage, Step3State
from utils.mapping_artifact_store import artifact_uri, load_json, load_latest_step4


def load_shared_state(run_id: str) -> Tuple[SharedState, str]:
    try:
        payload = load_json("STEP1_SHARED_STATE", run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"SharedState not found for run_id={run_id}")
    return SharedState.model_validate(payload), artifact_uri("STEP1_SHARED_STATE", run_id)


def load_step2_state(run_id: str) -> Tuple[Step2State, str]:
    try:
        payload = load_json("STEP2_STATE", run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Step2State not found for run_id={run_id}")
    return Step2State.model_validate(payload), artifact_uri("STEP2_STATE", run_id)


def load_step3_state(run_id: str) -> Tuple[Step3State, str]:
    try:
        payload = load_json("STEP3_CAPTURE_STATE", run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Step3State not found for run_id={run_id}")
    return Step3State.model_validate(payload), artifact_uri("STEP3_CAPTURE_STATE", run_id)


def load_step3_review_package(run_id: str) -> Tuple[Step3ReviewPackage, str]:
    try:
        payload = load_json("STEP3_REVIEW_PACKAGE", run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Step3ReviewPackage not found for run_id={run_id}")
    return Step3ReviewPackage.model_validate(payload), artifact_uri("STEP3_REVIEW_PACKAGE", run_id)


def locate_step3_review_package(run_id: str) -> Optional[str]:
    try:
        load_json("STEP3_REVIEW_PACKAGE", run_id)
    except FileNotFoundError:
        return None
    return artifact_uri("STEP3_REVIEW_PACKAGE", run_id)


def load_latest_step4_state(run_id: str) -> Tuple[dict, str]:
    try:
        return load_latest_step4(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Step4State not found for run_id={run_id}")
