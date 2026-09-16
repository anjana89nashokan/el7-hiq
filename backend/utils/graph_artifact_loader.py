from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents.mapping_ingestion.models import DataModelGraph
from config.settings import config
from utils.gcs_artifact_utils import artifact_bucket_name, artifact_project_id, artifact_storage_client, gcs_uri


def _slugify_subject_area(subject_area: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (subject_area or "").strip().lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unknown"


def _bucket_name() -> str:
    return artifact_bucket_name()


def _prefix() -> str:
    p = str(getattr(config, "ERWIN_DIAGRAM_ARTIFACT_PREFIX", "erwin-diagram-artifacts") or "erwin-diagram-artifacts")
    p = p.strip().strip("/")
    return p or "erwin-diagram-artifacts"


def _project_id() -> str:
    return artifact_project_id()


def _to_uri(object_name: str) -> str:
    return gcs_uri(object_name)


def _graph_prefix_for_subject(subject_area: str) -> str:
    slug = _slugify_subject_area(subject_area)
    return f"{_prefix()}/{slug}/"


def _graph_name_prefix(subject_area: str) -> str:
    slug = _slugify_subject_area(subject_area)
    return f"data_model_graph_{slug}_v1_"


def _list_subject_graph_blobs(*, subject_area: str):
    client = artifact_storage_client()
    bucket_name = _bucket_name()
    prefix = _graph_prefix_for_subject(subject_area)
    candidates = list(client.list_blobs(bucket_or_name=bucket_name, prefix=prefix))
    name_prefix = _graph_name_prefix(subject_area)
    return [
        b for b in candidates
        if b.name.endswith(".json") and Path(b.name).name.startswith(name_prefix)
    ]


def _latest_subject_blob(*, subject_area: str):
    blobs = _list_subject_graph_blobs(subject_area=subject_area)
    if not blobs:
        return None
    return max(blobs, key=lambda b: ((b.updated or datetime.min.replace(tzinfo=timezone.utc)), b.name))


def locate_latest_graph_artifact(*, subject_area: str) -> tuple[str, datetime] | None:
    """
    Locate the latest subject-area graph artifact in GCS.
    Returns (gs://uri, updated_at_utc) when found.
    """
    latest = _latest_subject_blob(subject_area=subject_area)
    if latest is None:
        return None
    updated = latest.updated or datetime.now(timezone.utc)
    return _to_uri(latest.name), updated


def load_latest_graph_artifact(*, subject_area: str) -> tuple[DataModelGraph, str]:
    """
    Load latest subject-area graph artifact from GCS as a validated DataModelGraph.
    """
    latest = _latest_subject_blob(subject_area=subject_area)
    if latest is None:
        raise FileNotFoundError(f"No graph artifact found for subject_area='{subject_area}'.")
    payload = latest.download_as_text(encoding="utf-8")
    graph = DataModelGraph.model_validate_json(payload)
    return graph, _to_uri(latest.name)


def save_graph_artifact(*, subject_area: str, graph: DataModelGraph) -> str:
    """
    Persist a canonical graph JSON artifact to GCS and return gs:// URI.
    """
    now = datetime.now(timezone.utc)
    slug = _slugify_subject_area(subject_area)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    short_id = uuid4().hex[:8]
    filename = f"data_model_graph_{slug}_v1_{timestamp}_{short_id}.json"
    object_name = f"{_graph_prefix_for_subject(subject_area)}{filename}"

    client = artifact_storage_client()
    bucket = client.bucket(_bucket_name())
    bucket.blob(object_name).upload_from_string(graph.model_dump_json(indent=2), content_type="application/json")
    return _to_uri(object_name)


def list_subject_area_statuses(*, subject_areas: list[str]) -> list[dict[str, Any]]:
    # Single GCS list pass for all subjects, then reduce in-memory.
    slug_to_subject: dict[str, str] = {_slugify_subject_area(sa): sa for sa in subject_areas}
    prefix_parts = tuple(_prefix().split("/"))

    client = artifact_storage_client()
    blobs = list(client.list_blobs(bucket_or_name=_bucket_name(), prefix=f"{_prefix()}/"))

    latest_by_slug: dict[str, storage.Blob] = {}
    for blob in blobs:
        name = blob.name or ""
        if not name.endswith(".json"):
            continue

        parts = Path(name).parts
        if len(parts) < len(prefix_parts) + 2:
            continue
        if tuple(parts[: len(prefix_parts)]) != prefix_parts:
            continue

        slug = parts[len(prefix_parts)]
        if slug not in slug_to_subject:
            continue

        filename = parts[-1]
        if not filename.startswith(f"data_model_graph_{slug}_v1_"):
            continue

        current = latest_by_slug.get(slug)
        if current is None:
            latest_by_slug[slug] = blob
            continue

        current_key = (current.updated or datetime.min.replace(tzinfo=timezone.utc), current.name)
        next_key = (blob.updated or datetime.min.replace(tzinfo=timezone.utc), blob.name)
        if next_key > current_key:
            latest_by_slug[slug] = blob

    out: list[dict[str, Any]] = []
    for subject_area in subject_areas:
        slug = _slugify_subject_area(subject_area)
        latest = latest_by_slug.get(slug)
        if latest is None:
            out.append(
                {
                    "subject_area": subject_area,
                    "enabled": False,
                    "last_uploaded_at": None,
                    "graph_artifact_path": None,
                }
            )
            continue

        updated = latest.updated or datetime.now(timezone.utc)
        out.append(
            {
                "subject_area": subject_area,
                "enabled": True,
                "last_uploaded_at": updated.isoformat(),
                "graph_artifact_path": _to_uri(latest.name),
            }
        )
    return out

