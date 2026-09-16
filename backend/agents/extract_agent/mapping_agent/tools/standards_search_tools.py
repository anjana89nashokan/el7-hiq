"""
REST-backed Vertex AI Search tool for the mapping agent.

Searches the AI Data Delivery Standards datastore to find source tables,
source columns, and transformation rules for a target attribute question.
"""

import logging
from typing import Optional

from google.adk.tools import ToolContext

from config.settings import config
from utils.extracts_vertex_search_utils_rest import (
    answer_query_standards,
    search_standards_passages,
)

logger = logging.getLogger(__name__)


def search_standards_for_mapping(
    tool_context: ToolContext,
    question: str,
    extract_scope: Optional[str] = None,
) -> str:
    """
    Search the AI Data Delivery Standards datastore for source table, source column,
    and transformation rules matching the given target attribute question.
    """
    padded_query = question
    if extract_scope:
        padded_query += f" (Extract scope: {extract_scope})"

    project_id = config.STANDARDS_PROJECT_ID
    location = config.DATASTORE_LOCATION
    engine_id = config.STANDARDS_APP_ID
    method = "answer"

    try:
        if method == "answer":
            result = answer_query_standards(
                query=padded_query,
                project_id=project_id,
                location=location,
                engine_id=engine_id,
            )
        else:
            result = search_standards_passages(
                query=padded_query,
                project_id=project_id,
                location=location,
                engine_id=engine_id,
            )
    except Exception as exc:
        logger.warning("[standards-search] REST lookup failed for %r: %s", question, exc)
        result = {"answer_text": "", "citations": [], "status": "unavailable"}

    existing: dict = tool_context.state.get("standards_search_results", {})
    existing[question] = result
    tool_context.state["standards_search_results"] = existing

    status = result.get("status")
    if status == "no_results":
        return f"Standards search returned no results for: {question}"
    if status == "not_configured":
        return "Standards search is not configured."
    if status == "unavailable":
        return f"Standards search is unavailable for: {question}"

    lines = [f"Standards search results (scope={extract_scope or 'all'}):"]
    lines.append(result.get("answer_text", ""))
    if result.get("citations"):
        lines.append("\nCitations:")
        for citation in result["citations"]:
            page = citation.get("page", "")
            snippet = citation.get("snippet", "") or citation.get("sources", "")
            if page:
                lines.append(f"  - Page {page}: {str(snippet)[:200]}")
            else:
                lines.append(f"  - {str(snippet)[:200]}")

    return "\n".join(lines)
