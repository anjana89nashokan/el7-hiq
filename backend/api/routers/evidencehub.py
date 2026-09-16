from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from utils.vectorstore_ingestion_pipeline import ingest_evidence_files

from google.genai.errors import ServerError

router = APIRouter()


class EvidenceIngestResponse(BaseModel):
    evidence_type: str = Field(..., description="Evidence type ingested (TRANSCRIPT|PLAYBOOK).")
    files_received: int
    chunks_total: int
    chunks_deduped: int
    chunks_ingested: int


@router.post("/ingest-files", response_model=EvidenceIngestResponse)
async def ingest_files(
    files: List[UploadFile] = File(..., description="Evidence files (pdf/txt/csv/xlsx/docx)."),
    evidence_type: Literal["TRANSCRIPT", "PLAYBOOK"] = Form(..., description="Whether the files are transcripts or playbooks."),
    interface_code: Optional[str] = Form(None, description="Optional interface code tag for filtering."),
    authority_level: Optional[Literal["LOW", "MED", "HIGH"]] = Form(None, description="Optional authority level override."),
    version: Optional[str] = Form(None, description="Optional version tag for audit/repro."),
):
    """
    Ingest evidence files into Vertex Vector Search + BigQuery catalog.

    Notes:
      - This endpoint currently supports PLAYBOOK and TRANSCRIPT ingestion only.
      - Experience ingestion (from Step 3 learning loop) will be added later.
    """
    try:
        res = await ingest_evidence_files(
            files=files,
            evidence_type=evidence_type,
            interface_code=interface_code,
            authority_level=authority_level,
            version=version,
            created_at=datetime.now(timezone.utc),
        )
    except ServerError as e:
        raise HTTPException(status_code=e.code, detail=f"Error sending message: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return EvidenceIngestResponse(
        evidence_type=evidence_type,
        files_received=res.files_received,
        chunks_total=res.chunks_total,
        chunks_deduped=res.chunks_deduped,
        chunks_ingested=res.chunks_ingested,
    )

