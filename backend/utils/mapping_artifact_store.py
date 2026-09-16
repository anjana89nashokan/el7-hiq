"""
Mapping artifact storage (GCS-only).

Scope:
  - Step 1/2/3/4 mapping JSON artifacts only.
  - Auth mode is artifact-scoped and does not change other services.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from config.settings import config
from utils.gcs_artifact_utils import (
    artifact_bucket_name,
    artifact_project_id,
    artifact_storage_client,
    gcs_uri,
    payload_to_json_text,
)

ArtifactKind = Literal[
    "STEP1_SHARED_STATE",
    "STEP1_MERGED_GRAPH",
    "STEP2_STATE",
    "STEP3_REVIEW_PACKAGE",
    "STEP3_CAPTURE_STATE",
    "STEP4_STATE",
]


def _bucket_name() -> str:
    return artifact_bucket_name()


def _prefix() -> str:
    p = str(getattr(config, "MAPPING_ARTIFACT_PREFIX", "mapping-artifacts") or "mapping-artifacts").strip().strip("/")
    return p or "mapping-artifacts"


def _project_id() -> str:
    return artifact_project_id()

def _payload_to_json_text(payload: Any) -> str:
    return payload_to_json_text(payload)


def _object_name(artifact_kind: ArtifactKind, run_id: str, *, step4_run_id: str | None = None) -> str:
    rid = str(run_id or "").strip()
    if not rid:
        raise RuntimeError("run_id is required for artifact storage.")

    base = f"{_prefix()}/{rid}"
    if artifact_kind == "STEP1_SHARED_STATE":
        return f"{base}/step1/shared_state.json"
    if artifact_kind == "STEP1_MERGED_GRAPH":
        return f"{base}/step1/merged_graph.json"
    if artifact_kind == "STEP2_STATE":
        return f"{base}/step2/state.json"
    if artifact_kind == "STEP3_REVIEW_PACKAGE":
        return f"{base}/step3/review_package.json"
    if artifact_kind == "STEP3_CAPTURE_STATE":
        return f"{base}/step3/capture_state.json"
    if artifact_kind == "STEP4_STATE":
        sid = str(step4_run_id or "").strip()
        if not sid:
            raise RuntimeError("step4_run_id is required for STEP4_STATE artifact.")
        return f"{base}/step4/{sid}.json"
    raise RuntimeError(f"Unsupported artifact_kind: {artifact_kind}")


def _uri_for_object(object_name: str) -> str:
    return gcs_uri(object_name)


def artifact_uri(
    artifact_kind: ArtifactKind,
    run_id: str,
    *,
    step4_run_id: str | None = None,
) -> str:
    return _uri_for_object(_object_name(artifact_kind, run_id, step4_run_id=step4_run_id))


def save_json(
    artifact_kind: ArtifactKind,
    run_id: str,
    payload: Any,
    *,
    step4_run_id: str | None = None,
) -> str:
    client = artifact_storage_client()
    bucket = client.bucket(_bucket_name())
    object_name = _object_name(artifact_kind, run_id, step4_run_id=step4_run_id)
    blob = bucket.blob(object_name)
    blob.upload_from_string(_payload_to_json_text(payload), content_type="application/json")
    return _uri_for_object(object_name)


def load_json(
    artifact_kind: ArtifactKind,
    run_id: str,
    *,
    step4_run_id: str | None = None,
) -> dict[str, Any]:
    client = artifact_storage_client()
    bucket = client.bucket(_bucket_name())
    object_name = _object_name(artifact_kind, run_id, step4_run_id=step4_run_id)
    blob = bucket.blob(object_name)
    if not blob.exists(client=client):
        raise FileNotFoundError(f"Artifact not found: {_uri_for_object(object_name)}")
    return json.loads(blob.download_as_text(encoding="utf-8"))


def load_latest_step4(run_id: str) -> tuple[dict[str, Any], str]:
    rid = str(run_id or "").strip()
    if not rid:
        raise RuntimeError("run_id is required for STEP4 artifact lookup.")

    client = artifact_storage_client()
    bucket = client.bucket(_bucket_name())
    prefix = f"{_prefix()}/{rid}/step4/"
    blobs = list(client.list_blobs(bucket_or_name=bucket.name, prefix=prefix))
    candidates = [b for b in blobs if b.name.endswith(".json")]
    if not candidates:
        raise FileNotFoundError(f"No STEP4 artifacts found for run_id={rid}.")
    latest = max(candidates, key=lambda b: (b.updated or datetime.min, b.name))
    payload = json.loads(latest.download_as_text(encoding="utf-8"))
    return payload, _uri_for_object(latest.name)
