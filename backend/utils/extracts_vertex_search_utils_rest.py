"""
extracts_vertex_search_utils_rest.py
=====================================
REST-based replacement for extracts_vertex_search_utils_v1.py.
Uses httpx + ADC Bearer token instead of gRPC SDK to avoid SSL failures
on proxies (Zscaler / CERTIFICATE_VERIFY_FAILED).

Same function signatures as v1 — swap the import in tools.py to use this file.
"""

import logging
import os
from typing import Any

from config.settings import config

logger = logging.getLogger(__name__)

_DISCOVERY_BASE = "https://discoveryengine.googleapis.com"  # overridden per-call with regional endpoint


def _get_access_token() -> str:
    """Get OAuth2 Bearer token from ADC credentials."""
    import google.auth
    from google.auth.transport.requests import Request

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
            creds.refresh(Request())
            logger.info("[standards-search] Using ADC from %s", adc_path)
            return str(creds.token)
        except Exception as exc:
            logger.warning("[standards-search] ADC file load failed: %s — trying ambient", exc)

    # Fallback: ambient ADC
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return str(creds.token)


def search_standards_passages(
    query: str,
    project_id: str,
    location: str,
    engine_id: str,
    page_size: int = 5,
) -> dict:
    """
    Search AIDataDeliveryStandards using extractive answers — REST version.
    Same signature as extracts_vertex_search_utils_v1.search_standards_passages.
    """
    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        # No GCP/Discovery Engine locally — degrade to empty so agents proceed.
        return {"answer_text": "", "citations": [], "status": "not_configured"}
    import httpx

    regional_base = f"https://{location}-discoveryengine.googleapis.com"
    url = (
        f"{regional_base}/v1/projects/{project_id}/locations/{location}"
        f"/collections/default_collection/engines/{engine_id}"
        f"/servingConfigs/default_search:search"
    )
    payload: dict[str, Any] = {
        "query": query,
        "pageSize": page_size,
        "contentSearchSpec": {
            "extractiveContentSpec": {"maxExtractiveAnswerCount": 1}
        },
    }

    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with httpx.Client(timeout=httpx.Timeout(30)) as client:
        r = client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            logger.error("[standards-search] search failed status=%s body=%s", r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()

    passages, citations = [], []
    for result in data.get("results", []):
        dsd = result.get("document", {}).get("derivedStructData", {})
        for ea in (dsd.get("extractive_answers") or dsd.get("extractiveAnswers") or []):
            content = str(ea.get("content", "") or "").strip()
            if content:
                passages.append(content)
                citations.append({"page": str(ea.get("pageNumber", "") or ""), "snippet": content[:200]})

    logger.info("[standards-search] search_passages query=%r passages=%d", query, len(passages))
    return {
        "answer_text": "\n\n".join(passages),
        "citations": citations,
        "status": "ok" if passages else "no_results",
    }


def answer_query_standards(
    query: str,
    project_id: str = config.STANDARDS_PROJECT_ID,
    location: str = config.DATASTORE_LOCATION,
    engine_id: str = config.STANDARDS_APP_ID,
) -> dict:
    """
    Query AIDataDeliveryStandards via AnswerQuery REST API.
    Same signature as extracts_vertex_search_utils_v1.answer_query_standards.
    """
    import httpx

    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        return {"answer_text": "", "citations": [], "status": "not_configured"}

    regional_base = f"https://{location}-discoveryengine.googleapis.com"
    url = (
        f"{regional_base}/v1beta/projects/{project_id}/locations/{location}"
        f"/collections/default_collection/engines/{engine_id}"
        f"/servingConfigs/default_serving_config:answer"
    )

    preamble = (
        "You are a data standards lookup assistant for healthcare insurance data extracts. "
        "Answer questions about DART field names, filter rules, and data standards from the AIDataDeliveryStandards document. "
        "Return the exact DART field name(s) that apply to the query, with a brief usage explanation. "
        "Be concise — the answer will be used by an automated mapping agent."
    )

    payload: dict[str, Any] = {
        "query": {"text": query},
        "queryUnderstandingSpec": {
            "queryRephraserSpec": {"disable": False, "maxRephraseSteps": 1}
        },
        "answerGenerationSpec": {
            "ignoreAdversarialQuery": False,
            "ignoreNonAnswerSeekingQuery": False,
            "ignoreLowRelevantContent": False,
            "modelSpec": {"modelVersion": "gemini-2.5-flash/answer_gen/v1"},
            "promptSpec": {"preamble": preamble},
            "includeCitations": True,
            "answerLanguageCode": "en",
        },
        "userPseudoId": "standards-lookup",
    }

    token = _get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    with httpx.Client(timeout=httpx.Timeout(30)) as client:
        r = client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            logger.error("[standards-search] answer failed status=%s body=%s", r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()

    answer = data.get("answer", {})
    answer_text = (answer.get("answerText") or "").strip()
    citations = [
        {"start_index": c.get("startIndex", 0), "end_index": c.get("endIndex", 0), "sources": [str(s) for s in c.get("sources", [])]}
        for c in answer.get("citations", [])
    ]

    logger.info("[standards-search] answer_query query=%r answer_len=%d", query, len(answer_text))
    return {
        "answer_text": answer_text,
        "citations": citations,
        "status": "ok" if answer_text else "no_results",
    }
