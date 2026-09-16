from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.settings import config
from utils.gcs_artifact_utils import upload_json


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_judgment(
    *,
    session_id: str,
    layer: str,
    revision_number: int,
    payload: Any,
) -> str:
    """
    Persist a layer judgment payload to GCS.

    Object name pattern:
        {BSA_EXTRACT_ARTIFACT_PREFIX}/{session_id}/quality/{layer}/rev_{rev}_{ts}.json
    """
    prefix = str(getattr(config, "BSA_EXTRACT_ARTIFACT_PREFIX", "bsa-extract-artifacts")).strip("/")
    object_name = (
        f"{prefix}/{session_id}/quality/{layer}/rev_{revision_number}_{_timestamp()}.json"
    )
    return upload_json(object_name=object_name, payload=payload)
