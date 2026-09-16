"""
Vertex AI Search utility for AI Data Delivery Standards.
FIXED VERSION (aligned with working extractive + structured behavior)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf.json_format import MessageToDict

from config.settings import config

logger = logging.getLogger(__name__)


# =========================================================
# AUTH (unchanged)
# =========================================================
def _get_credentials():
    import google.auth

    adc_path = (
        os.path.join(os.environ.get("APPDATA", ""), "gcloud", "application_default_credentials.json")
        if os.name == "nt"
        else os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
    )

    if os.path.exists(adc_path):
        try:
            creds, _ = google.auth.load_credentials_from_file(
                adc_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            logger.info("[standards-search] Using ADC from %s", adc_path)
            return creds
        except Exception as exc:
            logger.warning("[standards-search] Failed to load ADC: %s", exc)

    return None


# =========================================================
# SAFE DICT HANDLER
# =========================================================
def _safe_dict(obj):
    if hasattr(obj, "DESCRIPTOR"):
        return MessageToDict(obj)
    elif isinstance(obj, dict):
        return obj
    else:
        try:
            return dict(obj)
        except Exception:
            return {}

# =========================================================
# FIXED RESULT EXTRACTOR
# =========================================================
def _extract_results(response) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for r in response.results:
        if not r.document:
            continue

        doc = r.document
        struct_dict = _safe_dict(getattr(doc, "struct_data", {}))
        dsd_dict = _safe_dict(getattr(doc, "derived_struct_data", {}))

        if not struct_dict:
            json_data = getattr(doc, "json_data", None)
            if json_data:
                import json as _json
                try:
                    struct_dict = _json.loads(json_data)
                except Exception:
                    pass

        snippets = [
            str(s.get("snippet", "")).strip()
            for s in dsd_dict.get("snippets", [])
            if s.get("snippet")
        ]
        extractive_answers = [
            str(ea.get("content", "")).strip()
            for ea in dsd_dict.get("extractive_answers", [])
            if ea.get("content")
        ]

        source_text = " ".join(extractive_answers + snippets)
        title = (
            struct_dict.get("title")
            or dsd_dict.get("title")
            or dsd_dict.get("link", "")
            or doc.id
        )

        out.append({
            "document_id": doc.id or "",
            "title": title,
            "source_table": struct_dict.get("source_table", struct_dict.get("table_name", "")),
            "source_column": struct_dict.get("source_column", struct_dict.get("column_name", "")),
            "company": struct_dict.get("company", struct_dict.get("company_name", "")),
            "description": struct_dict.get("description", source_text[:300] if source_text else ""),
            "snippets": snippets,
            "extractive_answers": extractive_answers,
            "raw": struct_dict,
        })

    return out


# =========================================================
# REUSABLE CLIENT (prevents gRPC channel leaks)
# =========================================================
_client_cache: dict[str, discoveryengine.SearchServiceClient] = {}


def _get_client(location: str) -> discoveryengine.SearchServiceClient:
    if location not in _client_cache:
        _client_cache[location] = discoveryengine.SearchServiceClient(
            credentials=_get_credentials(),
            client_options={"api_endpoint": f"{location}-discoveryengine.googleapis.com"},
        )
    return _client_cache[location]


# =========================================================
# FIXED SEARCH FUNCTION
# =========================================================
def search_standards(
    query: str,
    *,
    top_k: int = 5,
    project_id: str = config.STANDARDS_PROJECT_ID,
    location: str = config.DATASTORE_LOCATION,
    engine_id: str = config.STANDARDS_APP_ID,
) -> list[dict[str, Any]]:

    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        return []  # No Discovery Engine locally — agents proceed without standards grounding.
    client = _get_client(location)

    # ❗ FIX: use correct serving config
    serving_config = (
        f"projects/{project_id}/locations/{location}/collections/default_collection"
        f"/engines/{engine_id}/servingConfigs/default_search"
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=top_k,

        # ✅ CRITICAL FIX: enables extractive answers (this is why your old code felt weak)
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_answer_count=1,
            )
        ),

        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(condition="AUTO"),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(mode="AUTO"),
    )

    try:
        response = client.search(request)
        results = _extract_results(response)

        logger.debug(
            "[standards_search] query=%r top_k=%d results=%d",
            query, top_k, len(results)
        )

        return results

    except Exception as exc:
        logger.warning("[standards_search] search failed: %s", exc)
        return []


# =========================================================
# TARGET SEARCH (unchanged logic, now benefits from fixes)
# =========================================================
def search_standards_for_target(
    target_attribute: str,
    logical_name: str | None = None,
    description: str | None = None,
    company: str | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:

    name = (logical_name or "").strip()
    desc = (description or "").strip()

    if name and desc:
        query = f"Attribute: {name}\nDescription: {desc}"
    elif name:
        query = f"Attribute: {name}"
    else:
        query = f"Target Column: {target_attribute}"

    return search_standards(
        query=query,
        top_k=top_k,
    )