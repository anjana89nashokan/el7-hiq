"""
Vertex AI Vector Search + Embeddings helpers for EvidenceHub ingestion.

Notes:
  - We call Vertex AI REST APIs directly (httpx) to avoid dependency drift.
  - Credentials are resolved via GOOGLE_APPLICATION_CREDENTIALS / ADC.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from config.settings import config

logger = logging.getLogger(__name__)

def _get_access_token() -> str:
    """
    Returns an OAuth2 access token using Application Default Credentials.
    """
    from google.auth.transport.requests import Request  # type: ignore
    import google.auth  # type: ignore

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    return str(credentials.token)


def _aiplatform_base_url(location: str) -> str:
    return f"https://{location}-aiplatform.googleapis.com"


def _vector_search_project_number() -> str:
    """
    Vector Search indexEndpoints resources in this project are referenced by PROJECT NUMBER (not project id)
    in the public endpoint URLs (as shown in the Vertex console examples).

    We derive it from REASONING_ENGINE_RESOURCE (which already contains projects/<number>/...),
    and allow overriding via env if needed.
    """
    pn = str(getattr(config, "VECTOR_SEARCH_PROJECT_NUMBER", "") or "").strip()
    if pn:
        return pn
    resource = str(getattr(config, "REASONING_ENGINE_RESOURCE", "") or "")
    # Expected: projects/677861082546/locations/...
    if "projects/" in resource:
        try:
            tail = resource.split("projects/", 1)[1]
            pn = tail.split("/", 1)[0].strip()
            if pn.isdigit():
                return pn
        except Exception:
            pass
    # As a last resort, fall back to the project id (may work in some environments).
    return str(getattr(config, "GOOGLE_CLOUD_PROJECT", "") or "")


async def embed_texts_gemini_embedding(
    *,
    texts: list[str],
    model: Optional[str] = None,
    output_dimensions: Optional[int] = None,
    max_concurrency: Optional[int] = None,
    timeout_sec: int = 180,
) -> list[list[float]]:
    """
    Generate embeddings for a list of texts using Vertex AI embedding model.

    IMPORTANT:
      - For gemini-embedding-001, Vertex supports predict() with instances shaped like {"content": "..."}.
      - We set outputDimensionality explicitly to match the Vector Search index dimension (3072).
    """
    model = model or config.EVIDENCE_EMBEDDING_MODEL
    output_dimensions = int(output_dimensions or config.EVIDENCE_EMBEDDING_DIMENSIONS)
    max_concurrency = int(max_concurrency or config.EVIDENCE_EMBED_MAX_CONCURRENCY)

    # The embedding model is a publisher model; use the standard predict endpoint.
    url = (
        f"{_aiplatform_base_url(config.VECTOR_SEARCH_LOCATION)}/v1/projects/{config.GOOGLE_CLOUD_PROJECT}"
        f"/locations/{config.VECTOR_SEARCH_LOCATION}/publishers/google/models/{model}:predict"
    )

    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    sem = asyncio.Semaphore(max(1, max_concurrency))
    out: list[list[float]] = [None] * len(texts)  # type: ignore

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec)) as client:

        async def _one(i: int, t: str) -> None:
            payload = {
                "instances": [{"content": t}],
                "parameters": {"outputDimensionality": output_dimensions},
            }
            async with sem:
                r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            preds = (data or {}).get("predictions") or []
            if not preds:
                raise RuntimeError(f"Empty embedding predictions for item {i}")
            # Most embedding responses include: {"embeddings":{"values":[...]}}.
            emb = preds[0].get("embeddings") if isinstance(preds[0], dict) else None
            values = None
            if isinstance(emb, dict):
                values = emb.get("values")
            if values is None:
                # Fallback for alternate response shapes.
                values = preds[0].get("values") if isinstance(preds[0], dict) else None
            if not isinstance(values, list):
                raise RuntimeError(f"Unexpected embedding response shape for item {i}: {preds[0]}")
            vec = [float(x) for x in values]
            if len(vec) != output_dimensions:
                raise RuntimeError(f"Embedding dims mismatch: got {len(vec)} expected {output_dimensions}")
            out[i] = vec

        await asyncio.gather(*[_one(i, t) for i, t in enumerate(texts)])

    return out  # type: ignore


async def upsert_datapoints_to_index(
    *,
    index_id: str,
    datapoints: list[dict[str, Any]],
    timeout_sec: int = 180,
) -> None:
    """
    Upsert datapoints to a Vertex AI Vector Search index.

    Each datapoint must include:
      - datapoint_id
      - feature_vector
      - restricts (optional but recommended)
      - numeric_restricts (optional)
    """
    if not datapoints:
        return

    if not index_id:
        raise RuntimeError("VECTOR_SEARCH_INDEX_ID is required to upsert datapoints.")

    url = (
        f"{_aiplatform_base_url(config.VECTOR_SEARCH_LOCATION)}/v1/projects/{config.GOOGLE_CLOUD_PROJECT}"
        f"/locations/{config.VECTOR_SEARCH_LOCATION}/indexes/{index_id}:upsertDatapoints"
    )

    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec)) as client:
        # Upsert in batches to avoid payload limits.
        bs = max(1, int(getattr(config, "EVIDENCE_UPSERT_BATCH_SIZE", 50)))
        for i in range(0, len(datapoints), bs):
            batch = datapoints[i : i + bs]
            payload = {"datapoints": batch}
            r = await client.post(url, headers=headers, json=payload)
            if r.status_code >= 400:
                sample = batch[0] if batch else None
                logger.error(
                    "[vectorstore] upsertDatapoints failed: status=%s url=%s batch_size=%s sample_keys=%s sample_datapointId=%s sample_vector_len=%s sample_restrict_namespaces=%s response=%s",
                    r.status_code,
                    url,
                    len(batch),
                    sorted(list(sample.keys())) if isinstance(sample, dict) else None,
                    (sample.get("datapointId") if isinstance(sample, dict) else None),
                    (len(sample.get("featureVector") or []) if isinstance(sample, dict) and isinstance(sample.get("featureVector"), list) else None),
                    (
                        [rr.get("namespace") for rr in (sample.get("restricts") or [])]
                        if isinstance(sample, dict) and isinstance(sample.get("restricts"), list)
                        else None
                    ),
                    (r.text[:4000] if isinstance(r.text, str) else str(r.text)),
                )
            r.raise_for_status()


def epoch_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


async def find_neighbors(
    *,
    feature_vector: list[float],
    neighbor_count: int = 10,
    restricts: Optional[list[dict[str, Any]]] = None,
    timeout_sec: int = 180,
) -> list[dict[str, Any]]:
    """
    Query a deployed Vector Search index endpoint (public domain) for nearest neighbors.

    Returns a list of neighbor dicts (datapoint_id + distance + optional metadata fields).

    Notes:
      - This uses the public domain endpoint URL shown in the Vertex console examples.
      - The response shape can vary; we normalize to a flat list.
    """
    if not feature_vector:
        return []

    public_domain = str(getattr(config, "VECTOR_SEARCH_PUBLIC_DOMAIN", "") or "").strip()
    index_endpoint_id = str(getattr(config, "VECTOR_SEARCH_INDEX_ENDPOINT_ID", "") or "").strip()
    deployed_index_id = str(getattr(config, "VECTOR_SEARCH_DEPLOYED_INDEX_ID", "") or "").strip()
    location = str(getattr(config, "VECTOR_SEARCH_LOCATION", "") or "").strip()

    if not (public_domain and index_endpoint_id and deployed_index_id and location):
        raise RuntimeError("Vector Search endpoint settings are missing (domain/endpoint_id/deployed_index_id/location).")

    project_number = _vector_search_project_number()
    url = (
        f"https://{public_domain}/v1/projects/{project_number}/locations/{location}"
        f"/indexEndpoints/{index_endpoint_id}:findNeighbors"
    )

    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    dp: dict[str, Any] = {"featureVector": feature_vector}
    if restricts:
        dp["restricts"] = restricts

    payload = {
        "deployedIndexId": deployed_index_id,
        "queries": [{"datapoint": dp, "neighborCount": int(neighbor_count)}],
        "returnFullDatapoint": False,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec)) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            logger.error(
                "[vectorstore] findNeighbors failed: status=%s url=%s neighbor_count=%s restrict_namespaces=%s response=%s",
                r.status_code,
                url,
                int(neighbor_count),
                [rr.get("namespace") for rr in (restricts or []) if isinstance(rr, dict)],
                (r.text[:4000] if isinstance(r.text, str) else str(r.text)),
            )
        r.raise_for_status()
        data = r.json() or {}

    # Expected: {"nearestNeighbors":[{"neighbors":[{"datapoint":{"datapointId":"..."},"distance":...}, ...]}]}
    out: list[dict[str, Any]] = []
    for nn in (data.get("nearestNeighbors") or []):
        for n in (nn.get("neighbors") or []):
            dp = n.get("datapoint") or {}
            dp_id = dp.get("datapointId") or dp.get("datapoint_id") or n.get("datapointId")
            if not dp_id:
                continue
            out.append(
                {
                    "datapoint_id": str(dp_id),
                    "distance": n.get("distance"),
                }
            )
    return out
