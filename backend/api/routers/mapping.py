import json
import logging
from pathlib import Path
from typing import List, Literal, get_args

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from google.genai.errors import ServerError

from agents.mapping_ingestion.agent import run_ingestion_pipeline
from agents.mapping_generation.agent import run_step2_draft_pipeline
from agents.mapping_review.agent import (
    run_step3_capture_pipeline,
    run_step3_review_package_pipeline,
    run_step3_questions_pipeline,
)
from agents.mapping_apply_review.agent import run_step4_apply_review_pipeline
from config.settings import config
from api.dependencies.auth import CurrentUser, resolve_current_user
from db.engine import app_db_session, is_app_db_enabled
from db.repositories import AppSessionRepository
from utils.run_artifact_loader import (
    load_shared_state,
    load_step2_state,
    load_step3_review_package,
    load_step3_state,
    locate_step3_review_package,
)

logger = logging.getLogger(__name__)

router = APIRouter()

SubjectArea = Literal[
    "Authorizations - Referrals",
    "Behavioral Health",
    "Billing - Premium",
    "Customer Group Product",
    "Medical Claims",
    "Member Enrollment",
    "Pharmacy Claims",
    "Customer Service",
    "Provider",
    "Finance",
]
SUBJECT_AREAS: tuple[str, ...] = get_args(SubjectArea)

TargetLayout = Literal["UPLOAD_FILES", "INDEMAP"]


class Step3SubmitRequest(BaseModel):
    app_session_id: str | None = Field(default=None, description="Owning app session id.")
    run_id: str = Field(..., description="Run id whose Step 2/Step 3 snapshots will be loaded by the backend.")
    changed_rows: list[dict] = Field(
        default_factory=list,
        description="Changed mapping rows only (full row objects), identified by row_id.",
    )
    answers: dict[str, str] = Field(
        default_factory=dict,
        description="Answers keyed by question_id (UI currently provides free-text only).",
    )
    feedbacks: dict[str, str] = Field(
        default_factory=dict,
        description="Feedback keyed by mapping row_id.",
    )
    answered_by: str | None = Field(default=None, description="Optional reviewer identifier.")


class Step4ApplyRequest(BaseModel):
    app_session_id: str | None = Field(default=None, description="Owning app session id.")
    run_id: str = Field(..., description="Run id whose Step 1/2/3 artifacts will be loaded and finalized in Step 4.")


def _load_owned_mapping_run(
    *,
    session_id: str,
    user_key: str,
    mapping_run_id: str,
):
    if not is_app_db_enabled():
        raise HTTPException(status_code=503, detail="App session database is not configured.")
    with app_db_session() as db:
        repo = AppSessionRepository(db)
        session_obj = repo.get_session(session_id=session_id, user_key=user_key)
        if not session_obj:
            raise HTTPException(status_code=404, detail="Session not found.")
        run = repo.get_mapping_run_by_run_id(session_id=session_id, mapping_run_id=mapping_run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Mapping run not found for this session.")
        return session_obj, run


@router.post("/ingest")
async def ingest_mapping_metadata(
    interface_code: str = Form(...),
    app_session_id: str | None = Form(None),
    instructions_text: str = Form(""),
    subject_areas: List[str] | None = Form(None),
    subject_area: str | None = Form(None),
    target_layout: TargetLayout = Form("UPLOAD_FILES"),
    indemap_pairs_json: str | None = Form(None),
    indemap_database_names: List[str] | None = Form(None),
    indemap_table_names: List[str] | None = Form(None),
    source_files: List[UploadFile] = File(...),
    target_files: List[UploadFile] | None = File(None),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Step 1 ingestion endpoint.

    Accepts source/target metadata Excel files and optional instructions text.
    Produces a SharedState JSON on disk and returns run_id + path.
    """
    try:
        selected_subject_areas = [str(sa).strip() for sa in (subject_areas or []) if str(sa).strip()]
        if not selected_subject_areas and subject_area:
            selected_subject_areas = [str(subject_area).strip()]
        if not selected_subject_areas:
            raise HTTPException(status_code=422, detail="At least one subject area is required.")
        invalid_subject_areas = [sa for sa in selected_subject_areas if sa not in SUBJECT_AREAS]
        if invalid_subject_areas:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported subject areas: {', '.join(invalid_subject_areas)}",
            )

        temp_dir = Path(config.TMP_DIR)
        temp_dir.mkdir(parents=True, exist_ok=True)

        async def _save_files(files: List[UploadFile] | None) -> List[str]:
            saved_paths = []
            if not files:
                return saved_paths
            for uf in files:
                dest = temp_dir / uf.filename
                with dest.open("wb") as f:
                    f.write(await uf.read())
                saved_paths.append(str(dest))
            return saved_paths

        layout = str(target_layout or "UPLOAD_FILES").strip().upper()
        indemap_pairs: list[dict[str, str]] = []
        if layout == "UPLOAD_FILES":
            if not target_files or len(target_files) == 0:
                raise HTTPException(status_code=422, detail="Target files are required for UPLOAD_FILES layout.")
        elif layout == "INDEMAP":
            if (indemap_pairs_json or "").strip():
                try:
                    parsed = json.loads(indemap_pairs_json or "[]")
                except Exception:
                    raise HTTPException(status_code=422, detail="indemap_pairs_json must be valid JSON.")
                if not isinstance(parsed, list):
                    raise HTTPException(status_code=422, detail="indemap_pairs_json must be a JSON array.")
                for row in parsed:
                    if not isinstance(row, dict):
                        continue
                    db = str(row.get("database_name") or "").strip()
                    tbl = str(row.get("table_name") or "").strip()
                    if db and tbl:
                        indemap_pairs.append({"database_name": db, "table_name": tbl})
            else:
                # Backward-compatible fallback for older clients:
                # - one DB + many tables => repeat DB for each table
                # - equal length DB/table lists => zip pairs
                dbs = [str(x).strip() for x in (indemap_database_names or []) if str(x).strip()]
                tbls = [str(x).strip() for x in (indemap_table_names or []) if str(x).strip()]
                if len(dbs) == 1 and tbls:
                    indemap_pairs = [{"database_name": dbs[0], "table_name": t} for t in tbls]
                elif len(dbs) == len(tbls) and len(dbs) > 0:
                    indemap_pairs = [{"database_name": dbs[i], "table_name": tbls[i]} for i in range(len(dbs))]
                elif dbs or tbls:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Ambiguous IndeMap inputs. Provide explicit indemap_pairs_json as "
                            "[{\"database_name\":\"...\",\"table_name\":\"...\"}, ...]."
                        ),
                    )

            if not indemap_pairs:
                raise HTTPException(
                    status_code=422,
                    detail="At least one (database_name, table_name) pair is required for INDEMAP layout.",
                )
        else:
            raise HTTPException(status_code=422, detail=f"Unsupported target_layout '{target_layout}'.")

        source_paths = await _save_files(source_files)
        target_paths = await _save_files(target_files)

        mapping_session_run_id = None
        if app_session_id:
            if not is_app_db_enabled():
                raise HTTPException(status_code=503, detail="App session database is not configured.")
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                session_obj = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if not session_obj:
                    raise HTTPException(status_code=404, detail="Session not found.")
                mapping_run = repo.create_mapping_run(session=session_obj)
                mapping_session_run_id = mapping_run.id

        run_id, path = await run_ingestion_pipeline(
            interface_code=interface_code,
            source_files=source_paths,
            target_files=target_paths,
            instructions_text=instructions_text or None,
            subject_areas=selected_subject_areas,
            target_layout=layout,
            target_db_table_pairs=indemap_pairs,
        )

        if app_session_id:
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                session_obj = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if not session_obj:
                    raise HTTPException(status_code=404, detail="Session not found.")
                run = repo.get_current_mapping_run(session=session_obj)
                if not run or run.id != mapping_session_run_id:
                    raise HTTPException(status_code=409, detail="Mapping session changed while ingesting.")
                repo.update_mapping_run(
                    run=run,
                    status="INGESTED",
                    current_step="ingest",
                    mapping_run_id=run_id,
                    step1_uri=str(path),
                        resume_state_json={
                            "currentStep": 1,
                            "interfaceCode": interface_code,
                            "subjectArea": selected_subject_areas[0] if len(selected_subject_areas) == 1 else "",
                            "subjectAreas": selected_subject_areas,
                            "targetLayout": layout,
                        },
                    )

        return {
            "run_id": run_id,
            "shared_state_path": str(path),
            "app_session_id": app_session_id,
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to ingest mapping metadata")
        if isinstance(exc, FileNotFoundError):
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/draft")
async def draft_mapping(
    run_id: str = Form(...),
    app_session_id: str | None = Form(None),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Step 2 draft endpoint (AG1 + AG2 + AG3).

    Takes a Step 1 `run_id`, loads the corresponding SharedState JSON from RUNS_DIR,
    runs Step 2 through MappingPostProcessorAgent (Sub-agent #3), persists the Step2State JSON,
    and returns run_id + output path.
    """
    try:
        shared_state, _shared_state_uri = load_shared_state(run_id)

        out_run_id, step2_path = await run_step2_draft_pipeline(
            shared_state=shared_state,
        )
        step2_state, _step2_uri = load_step2_state(out_run_id)
        if app_session_id:
            _session_obj, _mapping_run = _load_owned_mapping_run(
                session_id=app_session_id,
                user_key=current_user.user_key,
                mapping_run_id=run_id,
            )
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                run = repo.get_mapping_run_by_run_id(session_id=app_session_id, mapping_run_id=run_id)
                if run:
                    repo.update_mapping_run(
                        run=run,
                        status="DRAFT_READY",
                        current_step="draft",
                        step2_uri=str(step2_path),
                        resume_state_json={
                            **(run.resume_state_json or {}),
                            "currentStep": 2,
                            "mappingData": step2_state.model_dump(),
                            "baselineMappingData": step2_state.model_dump(),
                        },
                    )
        return step2_state.model_dump()
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to draft mapping (Step 2)")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/review/questions")
async def build_step3_review_questions(
    run_id: str = Form(...),
    app_session_id: str | None = Form(None),
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Step 3 question generation endpoint (HITL Review - questions only).

    Takes a Step 2 `run_id`, loads the corresponding Step2State JSON from RUNS_DIR,
    generates a curated list of review questions, and returns:
      - run_id
      - step2_state (JSON object)
      - step3_questions (JSON array)

    Notes:
      - This endpoint does NOT accept BSA decisions yet (placeholder below).
      - This endpoint does NOT touch any frontend/UI code.
    """
    try:
        step2_state, _step2_uri = load_step2_state(run_id)
        review_package, step3_path = await run_step3_review_package_pipeline(step2_state=step2_state)
        if app_session_id:
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                session_obj = repo.get_session(session_id=app_session_id, user_key=current_user.user_key)
                if session_obj:
                    run = repo.get_mapping_run_by_run_id(session_id=app_session_id, mapping_run_id=run_id)
                    if run:
                        repo.update_mapping_run(
                            run=run,
                            status="REVIEW_READY",
                            current_step="review",
                            step3_review_package_uri=str(step3_path),
                            resume_state_json={
                                **(run.resume_state_json or {}),
                                "currentStep": 2,
                                "step3Questions": [q.model_dump() for q in review_package.review_questions],
                            },
                        )

        return {
            "run_id": step2_state.metadata.run_id,
            "step2_state": step2_state.model_dump(),
            "step3_questions": [q.model_dump() for q in review_package.review_questions],
            "step3_review_package_path": str(step3_path),
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to generate Step 3 review questions")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/review/submit")
async def submit_step3_review(
    payload: Step3SubmitRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Step 3.5 capture endpoint (UI submission: answers + edited rows + feedback).

    Contract (Option A):
      - UI sends changed rows only (full row objects), plus answers keyed by question_id, plus feedback by row_id.
      - Backend loads baseline Step2State and the pre-HITL Step3ReviewPackage snapshot by run_id,
        converts diffs into normalized Step3State.decisions, and persists <run_id>_step3_state.json.
    """
    try:
        step2_state, _step2_uri = load_step2_state(payload.run_id)

        step3_review_path = locate_step3_review_package(payload.run_id)
        review_questions = []
        if step3_review_path:
            review_package, _review_uri = load_step3_review_package(payload.run_id)
            review_questions = list(review_package.review_questions or [])

        # IMPORTANT: Step 4 answer linking must not depend on <run_id>_step3.json.
        # Ensure Step3State always contains review_questions (question_id -> row_ids/issue_ids links),
        # even if the pre-HITL snapshot file is missing.
        if not review_questions:
            review_questions = await run_step3_questions_pipeline(step2_state=step2_state)

        step3_state, step3_state_path = await run_step3_capture_pipeline(
            step2_state=step2_state,
            review_questions=review_questions,
            changed_rows_payload=payload.changed_rows,
            answers_by_question_id=payload.answers,
            feedbacks_by_row_id=payload.feedbacks,
            answered_by=payload.answered_by,
        )

        if payload.app_session_id:
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                session_obj = repo.get_session(session_id=payload.app_session_id, user_key=current_user.user_key)
                if session_obj:
                    run = repo.get_mapping_run_by_run_id(session_id=payload.app_session_id, mapping_run_id=payload.run_id)
                    if run:
                        selected_row_id = None
                        if payload.changed_rows:
                            selected_row_id = str(payload.changed_rows[0].get("row_id") or "")
                        repo.save_mapping_review_draft(
                            mapping_run=run,
                            answers_json=payload.answers,
                            feedbacks_json=payload.feedbacks,
                            changed_rows_json=payload.changed_rows,
                            active_tab="mappings",
                            selected_row_id=selected_row_id or None,
                        )
                        repo.update_mapping_run(
                            run=run,
                            status="REVIEW_SUBMITTED",
                            current_step="review_submit",
                            step3_capture_uri=str(step3_state_path),
                            resume_state_json={
                                **(run.resume_state_json or {}),
                                "currentStep": 3.5,
                                "answers": payload.answers,
                                "feedbacks": payload.feedbacks,
                            },
                        )

        return {
            "run_id": step3_state.metadata.run_id,
            "step3_state_path": str(step3_state_path),
        }
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to capture Step 3 review submission")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/review/apply")
async def apply_step4_review(
    payload: Step4ApplyRequest,
    current_user: CurrentUser = Depends(resolve_current_user),
):
    """
    Step 4 endpoint: apply Step 3 capture to Step 2 draft and persist a Step4State JSON.

    Input:
      - run_id only (Step 1/2/3 artifacts are loaded by run_id from RUNS_DIR).

    Output:
      - run_id
      - step4_state_path
      - step4_state (full JSON artifact)
    """
    try:
        shared_state, shared_state_path = load_shared_state(payload.run_id)
        step2_state, step2_path = load_step2_state(payload.run_id)
        step3_state, step3_state_path = load_step3_state(payload.run_id)
        step3_review_path = locate_step3_review_package(payload.run_id)

        step4_state, step4_path = await run_step4_apply_review_pipeline(
            shared_state=shared_state,
            shared_state_uri=str(shared_state_path),
            step2_state=step2_state,
            step2_state_uri=str(step2_path),
            step3_state=step3_state,
            step3_state_uri=str(step3_state_path),
            step3_review_package_uri=str(step3_review_path) if step3_review_path else None,
        )
        if payload.app_session_id:
            with app_db_session() as db:
                repo = AppSessionRepository(db)
                session_obj = repo.get_session(session_id=payload.app_session_id, user_key=current_user.user_key)
                if session_obj:
                    run = repo.get_mapping_run_by_run_id(session_id=payload.app_session_id, mapping_run_id=payload.run_id)
                    if run:
                        repo.update_mapping_run(
                            run=run,
                            status="COMPLETED",
                            current_step="apply_review",
                            step4_uri=str(step4_path),
                            resume_state_json={
                                **(run.resume_state_json or {}),
                                "currentStep": 4,
                                "step4Data": step4_state.model_dump(),
                                "step4StatePath": str(step4_path),
                            },
                            completed=True,
                        )
        return {"run_id": payload.run_id, "step4_state_path": str(step4_path), "step4_state": step4_state.model_dump()}
    except ServerError as e:
        logger.error("[VERTEX SERVICE UNAVAILABLE]", exc_info=True)
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to run Step 4 apply review")
        raise HTTPException(status_code=500, detail=str(exc))
