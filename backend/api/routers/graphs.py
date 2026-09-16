from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal, Optional, get_args

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from config.settings import config
from utils.erwin_graph_builder import build_erwin_subject_area_graph
from utils.graph_artifact_loader import list_subject_area_statuses, save_graph_artifact

from google.genai.errors import ServerError
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


class GraphBuildStats(BaseModel):
    tables_count: int
    fk_edges_count: int
    akmap_sk_count: int
    derived_origin_sk_count: int
    incomplete_fk_count: int
    missing_parent_tables_count: int


class BuildErwinSubjectAreaGraphResponse(BaseModel):
    inserted: bool
    run_id: str
    subject_area: str
    graph_artifact_path: str
    stats: GraphBuildStats
    warnings: list[dict] = Field(default_factory=list)


class SubjectAreaStatusItem(BaseModel):
    subject_area: str
    enabled: bool
    last_uploaded_at: str | None = None
    graph_artifact_path: str | None = None


@router.get("/subject-areas/status", response_model=list[SubjectAreaStatusItem])
async def get_subject_areas_status():
    try:
        payload = list_subject_area_statuses(subject_areas=list(SUBJECT_AREAS))
        return [SubjectAreaStatusItem(**item) for item in payload]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/build-erwin-subject-area", response_model=BuildErwinSubjectAreaGraphResponse)
async def build_erwin_subject_area_graph_endpoint(
    subject_area: SubjectArea = Form(..., description="ERwin subject area name."),
    tables_and_columns_file: UploadFile = File(..., description="ERwin 'Tables and Columns' report (.csv/.xlsx)."),
    tables_and_indexes_file: UploadFile = File(..., description="ERwin 'Tables and Indexes' report (.csv/.xlsx)."),
    run_id: Optional[str] = Form(None, description="Optional run id. UUID is generated when omitted."),
):
    try:
        tmp_dir = Path(config.TMP_DIR)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        resolved_run_id = (run_id or "").strip() or str(uuid.uuid4())

        columns_path = tmp_dir / f"{resolved_run_id}_{tables_and_columns_file.filename}"
        indexes_path = tmp_dir / f"{resolved_run_id}_{tables_and_indexes_file.filename}"

        columns_path.write_bytes(await tables_and_columns_file.read())
        indexes_path.write_bytes(await tables_and_indexes_file.read())

        result = build_erwin_subject_area_graph(
            subject_area=subject_area,
            tables_and_columns_path=columns_path,
            tables_and_indexes_path=indexes_path,
            run_id=resolved_run_id,
            output_root=Path(config.DATA_DIR) / "graphs",
        )
        graph_artifact_uri = save_graph_artifact(subject_area=subject_area, graph=result.graph)

        return BuildErwinSubjectAreaGraphResponse(
            inserted=True,
            run_id=result.run_id,
            subject_area=result.subject_area,
            graph_artifact_path=graph_artifact_uri,
            stats=GraphBuildStats(**result.stats),
            warnings=result.warnings_preview,
        )
    except ServerError as e:
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "inserted": False,
                "error": str(exc),
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail={
                "inserted": False,
                "error": str(exc),
            },
        )

