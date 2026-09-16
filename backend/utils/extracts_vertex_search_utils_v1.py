"""
extracts_vertex_search_utils_v1.py
===================================
Fixed version of extracts_vertex_search_utils.py.
Changes vs v0:
  - search_standards_passages: adds extractive_content_spec to request
    and parses extractive_answers via MessageToDict → returns clean passage text
  - _get_credentials / answer_query_standards: unchanged from working version
"""

import logging
import os
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf.json_format import MessageToDict

from config.settings import config

logger = logging.getLogger(__name__)


def _get_credentials():
    """
    Load ADC from well-known gcloud path, bypassing GOOGLE_APPLICATION_CREDENTIALS.
    Revert to `return None` once the server SA has Discovery Engine IAM on the standards project.
    """
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
            logger.warning("[standards-search] Failed to load ADC: %s — falling back to ambient credentials", exc)

    return None


def search_standards_passages(
    query: str,
    project_id: str,
    location: str,
    engine_id: str,
    page_size: int = 5,
) -> dict:
    """
    Search AIDataDeliveryStandards using extractive answers.
    Returns clean passage text extracted from derived_struct_data.
    """
    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        return {"answer_text": "", "citations": [], "status": "not_configured"}
    client = discoveryengine.SearchServiceClient(
        credentials=_get_credentials(),
        client_options={"api_endpoint": f"{location}-discoveryengine.googleapis.com"},
    )

    serving_config = (
        f"projects/{project_id}/locations/{location}/collections/default_collection"
        f"/engines/{engine_id}/servingConfigs/default_search"
    )

    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=page_size,
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_answer_count=1,
            ),
        ),
    )

    response = client.search(request)

    passages = []
    citations = []
    for r in response.results:
        if not r.document:
            continue

        dsd = r.document.derived_struct_data
        # In newer google-cloud-discoveryengine versions the proto-plus client
        # auto-converts google.protobuf.Struct → a Python dict / MapComposite.
        # MessageToDict only works on raw protobuf Message objects (has DESCRIPTOR).
        # Handle both cases safely.
        if hasattr(dsd, "DESCRIPTOR"):
            doc_dict = MessageToDict(dsd)
        elif isinstance(dsd, dict):
            doc_dict = dsd
        else:
            try:
                doc_dict = dict(dsd)
            except Exception:
                logger.warning("[standards-search] Could not convert derived_struct_data (%s) — skipping result", type(dsd).__name__)
                doc_dict = {}

        for ea in doc_dict.get("extractive_answers", []):
            # proto-plus may return Value wrappers — normalise to plain strings
            content = str(ea.get("content", "") or "").strip()
            if content:
                passages.append(content)
                citations.append({"page": str(ea.get("pageNumber", "") or ""), "snippet": content[:200]})

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
    Query AIDataDeliveryStandards via Conversational Answer API.
    Requires discoveryengine.servingConfigs.answer IAM permission.
    If 403, set STANDARDS_SEARCH_METHOD=search in .env to use search_standards_passages instead.
    """
    if getattr(config, "STANDALONE_MODE", False) or not engine_id:
        return {"answer_text": "", "citations": [], "status": "not_configured"}

    client = discoveryengine.SearchServiceClient(
        credentials=_get_credentials(),
        client_options={"api_endpoint": f"{location}-discoveryengine.googleapis.com"},
    )

    serving_config = (
        f"projects/{project_id}/locations/{location}/collections/default_collection"
        f"/engines/{engine_id}/servingConfigs/default_serving_config"
    )

    standards_preamble = """
You are a data standards lookup assistant for healthcare insurance data extracts.
Answer questions about DART field names, filter rules, and data standards from the AIDataDeliveryStandards document.
Return the exact DART field name(s) that apply to the query, with a brief usage explanation.
Be concise — the answer will be used by an automated mapping agent.
"""

    request = discoveryengine.AnswerQueryRequest(
        serving_config=serving_config,
        query=discoveryengine.Query(text=query),
        session=None,
        query_understanding_spec=discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec(
            query_rephraser_spec=discoveryengine.AnswerQueryRequest.QueryUnderstandingSpec.QueryRephraserSpec(
                disable=False, max_rephrase_steps=1,
            )
        ),
        answer_generation_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec(
            ignore_adversarial_query=False,
            ignore_non_answer_seeking_query=False,
            ignore_low_relevant_content=False,  # v0.3 doc is table-heavy; suppressing low-relevance drops valid tabular results
            model_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.ModelSpec(
                model_version="gemini-2.5-flash/answer_gen/v1",
            ),
            prompt_spec=discoveryengine.AnswerQueryRequest.AnswerGenerationSpec.PromptSpec(
                preamble=standards_preamble,
            ),
            include_citations=True,
            answer_language_code="en",
        ),
        user_pseudo_id="standards-lookup",
    )

    response = client.answer_query(request)

    answer_text = ""
    if response.answer and response.answer.answer_text:
        answer_text = response.answer.answer_text.strip()

    citations = []
    if response.answer and response.answer.citations:
        for c in response.answer.citations:
            citations.append({
                "start_index": getattr(c, "start_index", 0),
                "end_index": getattr(c, "end_index", 0),
                "sources": [str(s) for s in getattr(c, "sources", [])],
            })

    return {"answer_text": answer_text, "citations": citations, "status": "ok"}
