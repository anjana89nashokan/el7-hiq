"""
Driver Layer — API endpoints.

  POST /extract/driver/standards-search        — single ad-hoc standards query
  POST /extract/driver/standards-search-from-brd — batch search from parsed BRD
  POST /extract/driver/business-mapping        — Step 1: business_mapping_agent
  POST /extract/driver/logic                   — Step 2: logic_builder_agent
  POST /extract/driver/validate                — Step 3: driver_validator_agent
  POST /extract/driver/run                     — Full pipeline (all 3 agents sequential)
"""

import ast as _ast
import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from google.adk import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.apps.app import App
from google.adk.events import Event, EventActions
from utils.adk_runtime import VertexAiSessionService
from google.genai import types
from pydantic import BaseModel, ValidationError as PydanticValidationError

from agents.extract_agent.driver_agent.agent import (
    standards_search_agent,
    mapping_builder_agent,
    logic_builder_agent,
    driver_validator_agent,
)
from agents.extract_agent.driver_agent.tools import (
    SearchStandardsInput,
    search_standards_tool,
    BuildDriverMappingInput,
    build_driver_mapping_tool,
)
from config.settings import config
from utils.llm_rate_utils import (
    record_llm_usage_and_get_wait,
    wait_for_llm_request_slot,
    is_resource_exhausted_error,
    is_transient_llm_transport_error,
    calculate_retry_delay,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_DUMMY_BRD_PATH = Path(__file__).parent.parent.parent / "dummy_json_extracts" / "req_output_new.json"
_DRIVER_EVENTS_DIR = Path(__file__).parent.parent.parent / "driver_events"  # TODO-VDI: comment this line out

def _build_driver_run_config() -> RunConfig:
    return RunConfig(
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=500_000,
            sliding_window=types.SlidingWindow(target_tokens=250_000),
        )
    )


def _write_event(event_dir: Path, event_count: int, event: Any) -> None:
    """Writes ADK events to disk for local debugging — TODO-VDI: comment the call site, not this function."""
    try:
        event_dir.mkdir(parents=True, exist_ok=True)
        event_file = event_dir / f"event{event_count}.txt"
        with open(event_file, "w", encoding="utf-8") as f:
            f.write(f"EVENT {event_count}:\n\n{event}\n")
    except Exception as exc:
        logger.warning("[_write_event] Could not write event file: %s", exc)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SingleQueryRequest(BaseModel):
    query: str


class StandardsSearchResult(BaseModel):
    query: str
    status: str
    answer_text: str
    note: str = ""


class BrdFilterConcept(BaseModel):
    source: str
    source_id: str
    query: str


class BrdSearchResponse(BaseModel):
    total_concepts: int
    results: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_requirement_layer(brd_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Single key resolver — returns the requirement layer dict from BRD JSON.
    Priority: validated_requirement_layer (BSA-approved) → requirement_layer (draft).
    """
    return (
        brd_data.get("validated_requirement_layer")
        or brd_data.get("requirement_layer")
        or {}
    )


def _normalize_brd_input(brd_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize BRD JSON to a unified shape used by agents and _build_extract_context.
    Resolves the requirement layer key once via _get_requirement_layer so callers
    never need to know whether the source was validated_requirement_layer or requirement_layer.
    Returns ALL relevant sub-dicts so downstream helpers read from normalized, not raw BRD.
    """
    rl    = _get_requirement_layer(brd_data)
    scope = rl.get("scope", {})
    fmt   = "validated" if "validated_requirement_layer" in brd_data else "new"
    normalized = {
        # agent payload fields
        "in_scope":                scope.get("in_scope", ""),
        "out_of_scope":            scope.get("out_of_scope", ""),
        "requirements":            rl.get("requirements", ""),
        "generic_tables":          [],
        "filters_and_parameters":  rl.get("filters_and_parameters", {}),
        # context fields used by _build_extract_context
        "file_specs":              rl.get("file_specs", {}),
        "common_rules":            rl.get("common_rules", {}),
        "file_attributes_mapping": rl.get("file_attributes_mapping", {}),
        "format_version":          fmt,
    }
    fp = normalized["filters_and_parameters"]
    logger.info(
        "[_normalize_brd_input] format=%s in_scope_len=%d out_of_scope_len=%d "
        "requirements_len=%d filters_keys=%s file_specs_keys=%d common_rules_keys=%d",
        fmt,
        len(normalized["in_scope"]),
        len(normalized["out_of_scope"]),
        len(str(normalized["requirements"])),
        list(k for k, v in fp.items() if v and k != "date_parameters"),
        len(normalized["file_specs"]),
        len(normalized["common_rules"]),
    )
    return normalized


def _build_extract_context(normalized: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact extract_context block for fyi_lookup_tool ranking.
    Takes the normalized BRD dict (output of _normalize_brd_input), NOT raw brd_data.
    Deterministic — no LLM. Stored in session state after /driver/business-mapping
    so /driver/logic can reuse it without re-downloading the BRD.

    subject_areas lives in file_attributes_mapping (not file_specs) — fixed here.
    """
    file_specs    = normalized.get("file_specs", {})
    common_rules  = normalized.get("common_rules", {})
    file_attrs    = normalized.get("file_attributes_mapping", {})
    date_params   = normalized.get("filters_and_parameters", {}).get("date_parameters", {})

    # file_population_type is often blank in the extracted JSON.
    # Fall back to in_scope (first 200 chars) so FYI Signal 1 (entity-level match)
    # has meaningful context — e.g. "IBC and TPA members enrolled in medical plans".
    file_population_type = file_specs.get("file_population_type", "").strip()
    if not file_population_type:
        in_scope = normalized.get("in_scope", "")
        if in_scope:
            file_population_type = str(in_scope).strip()[:200]

    return {
        "file_population_type": file_population_type,
        "subject_areas":        file_attrs.get("subject_areas", ""),      # lives in file_attributes_mapping
        "vendor_name":          file_specs.get("vendor_name", ""),
        "interface_code":       common_rules.get("interface_code", ""),
        "effective_dates_from": common_rules.get("effective_dates_from", ""),
        "effective_dates_to":   common_rules.get("effective_dates_to", ""),
        "date_parameters":      {k: v for k, v in date_params.items() if v},
    }


def _load_brd_from_gcs(gcs_uri: str, label: str, req_id: str) -> Dict[str, Any]:
    """
    Download and parse a BRD JSON file from a GCS URI (gs://bucket/blob-path).
    Uses service account credentials from config.CREDENTIALS_PATH; falls back to ADC.
    Raises ValueError for a malformed URI; re-raises GCS / parse errors to the caller.
    """
    import re
    from utils.gcs_artifact_utils import artifact_storage_client
    import time as _time

    match = re.match(r"gs://([^/]+)/(.+)", gcs_uri.strip())
    if not match:
        raise ValueError(
            f"Invalid GCS URI '{gcs_uri}'. Expected format: gs://bucket/path/to/file.json"
        )
    bucket_name = match.group(1)
    blob_path   = match.group(2)

    creds_path = getattr(config, "CREDENTIALS_PATH", "")
    gcs_client = artifact_storage_client()  # local filesystem-backed storage (no GCP)

    logger.info(
        "[%s] [%s] GCS downloading — bucket=%s blob=%s",
        label, req_id, bucket_name, blob_path,
    )
    t0  = _time.monotonic()
    raw = gcs_client.bucket(bucket_name).blob(blob_path).download_as_text(encoding="utf-8")
    elapsed = round(_time.monotonic() - t0, 2)

    data   = json.loads(raw)
    rl_key = (
        "validated_requirement_layer" if "validated_requirement_layer" in data
        else "requirement_layer"      if "requirement_layer"           in data
        else "unknown"
    )
    logger.info(
        "[%s] [%s] GCS BRD loaded — bucket=%s blob=%s bytes=%d elapsed=%.2fs "
        "session_id=%s rl_key=%s gcs_output_uri=%s",
        label, req_id, bucket_name, blob_path, len(raw), elapsed,
        data.get("session_id", ""), rl_key,
        data.get("gcs_output_uri", ""),
    )
    return data


def _resolve_brd_data(request, label: str, req_id: str) -> Dict[str, Any]:
    """
    Resolve BRD data from one of three sources (checked in priority order):
      1. request.brd_uri — download JSON from GCS (UI sends gcs_output_uri as brd_uri)
      2. request.brd     — inline BRD dict in request body (dev / direct API calls)
      3. fallback        — local dummy BRD file (local dev / test only)
    Raises HTTPException on GCS download or parse failure.
    """
    if getattr(request, "brd_uri", None):
        logger.info("[%s] [%s] BRD source: GCS uri=%s", label, req_id, request.brd_uri)
        try:
            return _load_brd_from_gcs(request.brd_uri, label, req_id)
        except Exception as exc:
            logger.exception(
                "[%s] [%s] Failed to load BRD from GCS uri=%s: %s",
                label, req_id, request.brd_uri, exc,
            )
            raise HTTPException(
                status_code=500,
                detail=f"[{req_id}] Failed to load BRD from GCS ({request.brd_uri}): {exc}",
            )
    if getattr(request, "brd", None):
        logger.info("[%s] [%s] BRD source: inline request body", label, req_id)
        return request.brd
    logger.info("[%s] [%s] BRD source: dummy file %s", label, req_id, _DUMMY_BRD_PATH)
    return json.loads(_DUMMY_BRD_PATH.read_text(encoding="utf-8"))


def _extract_filter_concepts(brd: Dict[str, Any]) -> List[BrdFilterConcept]:
    normalized = _normalize_brd_input(brd)
    concepts: List[BrdFilterConcept] = []
    in_scope = normalized["in_scope"]
    out_of_scope = normalized["out_of_scope"]
    requirements = normalized["requirements"]

    if isinstance(in_scope, str):
        if in_scope.strip():
            concepts.append(BrdFilterConcept(
                source="in_scope", source_id="in_scope",
                query=f"DART fields for: {in_scope.strip()}",
            ))
    else:
        for i, item in enumerate(in_scope):
            desc = item.get("description", "")
            notes = item.get("notes", "") or ""
            query = f"DART field for {desc} filter"
            if notes:
                query += f" ({notes.replace(chr(10), ', ')})"
            concepts.append(BrdFilterConcept(source="in_scope", source_id=f"in_scope[{i}]", query=query))

    if isinstance(out_of_scope, str):
        if out_of_scope.strip():
            concepts.append(BrdFilterConcept(
                source="out_of_scope", source_id="out_of_scope",
                query=f"DART fields to exclude: {out_of_scope.strip()}",
            ))
    else:
        for i, item in enumerate(out_of_scope):
            concepts.append(BrdFilterConcept(
                source="out_of_scope", source_id=f"out_of_scope[{i}]",
                query=f"DART field to exclude {item.get('description', '')}",
            ))

    if isinstance(requirements, list):
        for req in requirements:
            if req.get("category", "").lower() != "business":
                continue
            concepts.append(BrdFilterConcept(
                source="requirement", source_id=req.get("id", "?"),
                query=req.get("description", ""),
            ))

    return concepts


async def _run_agent_collect_state_key(
    runner: Runner,
    user_id: str,
    session_id: str,
    user_text: str,
    state_key: str,
    agent_label: str,
    req_id: str,
    event_dir: Path = None,
    event_offset: int = 0,
) -> tuple:
    """
    Run an agent, collect events until state_key appears in state_delta.
    Returns (state_value_or_None, event_count).
    Includes rate limiting and 429 retry logic.
    """
    result_state = None
    event_count = 0
    _malformed_call_text: Optional[str] = None
    msg = types.Content(role="user", parts=[types.Part(text=user_text)])

    logger.info(
        "[%s] [%s] run_async start — user_id=%s session_id=%s payload_chars=%d",
        agent_label, req_id, user_id, session_id, len(user_text),
    )

    max_retries = 3
    for attempt in range(max_retries):
        _malformed_call_text = None
        try:
            await wait_for_llm_request_slot(f"driver:{session_id}")
            recommended_wait_sec = 0.0
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=msg,
                run_config=_build_driver_run_config(),
            ):
                recommended_wait_sec = max(
                    recommended_wait_sec,
                    await record_llm_usage_and_get_wait(
                        event,
                        session_id=f"driver:{session_id}",
                        buffer_tokens=300,
                    ),
                )

                if event_dir:  # TODO-VDI: comment this block out (2 lines)
                    _write_event(event_dir, event_offset + event_count, event)

                event_type = type(event).__name__
                agent_name = getattr(event, "agent_name", None)
                is_final = getattr(event, "is_final_response", lambda: False)
                is_final = is_final() if callable(is_final) else is_final

                logger.debug(
                    "[%s] [%s] event#%d type=%s agent=%s is_final=%s",
                    agent_label, req_id, event_count, event_type, agent_name, is_final,
                )

                # Log any state_delta keys so we can trace what the agent writes each turn
                if (
                    hasattr(event, "actions")
                    and event.actions
                    and hasattr(event.actions, "state_delta")
                    and event.actions.state_delta
                ):
                    logger.info(
                        "[%s] [%s] event#%d state_delta_keys=%s",
                        agent_label, req_id, event_count, list(event.actions.state_delta.keys()),
                    )

                event_count += 1
                if event_count > 500:
                    logger.error(
                        "[%s] [%s] Safety limit reached at event#%d — aborting run",
                        agent_label, req_id, event_count,
                    )
                    break

                # Capture malformed build_driver_mapping_tool call emitted as model text.
                # Event 18 pattern: content=None, payload in event.error_message.
                # Gate: only relevant when we're waiting for driver_mapping.
                if result_state is None and state_key == "driver_mapping":
                    _evt_txt = _extract_event_text_for_recovery(event)
                    if (
                        _evt_txt
                        and "build_driver_mapping_tool" in _evt_txt
                        and any(cls in _evt_txt for cls in (
                            "BuildDriverMappingToolInput",
                            "BuildDriverMappingInput",
                            "BuildDriverMappingToolInputFilterCandidates",
                            "FilterCandidateInput",
                            "filter_candidates",
                        ))
                    ):
                        _malformed_call_text = _evt_txt
                        logger.warning(
                            "[%s] [%s] event#%d: malformed build_driver_mapping_tool text captured (%d chars)",
                            agent_label, req_id, event_count - 1, len(_evt_txt),
                        )

                if (
                    hasattr(event, "actions")
                    and event.actions
                    and hasattr(event.actions, "state_delta")
                    and event.actions.state_delta
                    and state_key in event.actions.state_delta
                ):
                    result_state = event.actions.state_delta[state_key]
                    logger.info(
                        "[%s] [%s] state_key='%s' captured at event#%d result_type=%s",
                        agent_label, req_id, state_key, event_count - 1, type(result_state).__name__,
                    )
                    break

            # Malformed-call recovery: model emitted the tool call as Python text.
            # Only fires when driver_mapping is still missing after the full event loop.
            if result_state is None and _malformed_call_text and state_key == "driver_mapping":
                logger.warning(
                    "[%s] [%s] driver_mapping not in state — attempting malformed-call recovery",
                    agent_label, req_id,
                )
                result_state = await _recover_malformed_driver_mapping(
                    malformed_text=_malformed_call_text,
                    runner=runner,
                    user_id=user_id,
                    session_id=session_id,
                    agent_label=agent_label,
                    req_id=req_id,
                )
                if result_state is not None:
                    logger.warning(
                        "[%s] [%s] malformed-call recovery succeeded — candidates=%d unmapped=%d",
                        agent_label, req_id,
                        len(result_state.get("filter_candidates", [])),
                        len(result_state.get("unmapped_concepts", [])),
                    )

            # If we reached here without exception, break the retry loop
            if recommended_wait_sec > 0:
                logger.info(
                    "[%s] [%s] post-run delay %.2fs",
                    agent_label, req_id, recommended_wait_sec,
                )
                await asyncio.sleep(recommended_wait_sec)
            break

        except Exception as exc:
            if (
                is_resource_exhausted_error(exc)
                or is_transient_llm_transport_error(exc)
            ) and attempt < max_retries - 1:
                delay = calculate_retry_delay(attempt)
                logger.warning(
                    "[%s] [%s] retryable LLM error (attempt %d/%d). Retrying in %.2fs... error=%s",
                    agent_label, req_id, attempt + 1, max_retries, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            else:
                logger.error("[%s] [%s] Agent loop failed: %s", agent_label, req_id, exc)
                raise

    logger.info(
        "[%s] [%s] run_async done — total_events=%d state_captured=%s",
        agent_label, req_id, event_count, result_state is not None,
    )
    if result_state is None:
        session_service = getattr(runner, "session_service", None) or getattr(runner, "_session_service", None)
        app = getattr(runner, "app", None) or getattr(runner, "_app", None)
        app_name = getattr(app, "name", None)
        if session_service is not None and app_name:
            try:
                session = await session_service.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                state = getattr(session, "state", None) or {}
                result_state = state.get(state_key)
                if result_state is not None:
                    logger.info(
                        "[%s] [%s] state_key='%s' recovered from session state",
                        agent_label, req_id, state_key,
                    )
            except Exception as exc:
                logger.warning(
                    "[%s] [%s] session-state fallback failed for state_key='%s': %s",
                    agent_label, req_id, state_key, exc,
                )
    return result_state, event_count


def _build_app(app_name: str, agent, label: str) -> App:
    try:
        return App(name=app_name, root_agent=agent)
    except PydanticValidationError as exc:
        logger.warning("[%s] App name validation failed (%s); using model_construct.", label, exc)
        return App.model_construct(name=app_name, root_agent=agent)


async def _get_session(session_service: VertexAiSessionService, app_name: str, user_id: str, session_id: str, label: str):
    try:
        session = await session_service.get_session(
            app_name=app_name, user_id=user_id, session_id=session_id,
        )
        if session is None:
            # Standalone: the ADK session is just a keyed state container. The
            # driver pipeline reads the BRD from its artifact (brd_uri), not from
            # session state, so creating the session on demand is safe and keeps
            # business-mapping → logic → validate → approve working without an
            # explicit "start" step having pre-created it.
            logger.info(
                "[%s] session not found — creating (app=%s user=%s sid=%s)",
                label, app_name, user_id, session_id,
            )
            session = await session_service.create_session(
                app_name=app_name, user_id=user_id, session_id=session_id,
            )
        return session
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] Failed to get/create session: %s", label, exc)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve Vertex session: {exc}")


async def _write_session_state(
    session_service: VertexAiSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
    state_delta: Dict[str, Any],
    label: str,
) -> None:
    """Write key/value pairs to Vertex AI session state via append_event."""
    session = await _get_session(session_service, app_name, user_id, session_id, label)
    event = Event(
        author="system",
        invocation_id=f"sys-{uuid.uuid4()}",
        actions=EventActions(state_delta=state_delta),
    )
    await session_service.append_event(session=session, event=event)
    logger.info("[%s] session state written — keys=%s", label, list(state_delta.keys()))


# ---------------------------------------------------------------------------
# Malformed function-call recovery helpers
# Used when gemini-2.5-pro emits build_driver_mapping_tool as Python text
# instead of a structured function call (MALFORMED_FUNCTION_CALL finish_reason).
# ---------------------------------------------------------------------------

def _extract_event_text_for_recovery(event) -> str:
    """
    Extract raw text from an ADK event for malformed-call detection.
    Checks event.error_message first (Event 18: content=None, payload only here),
    then falls back to event.content.parts[].text. Never raises.
    """
    parts = []
    try:
        err = getattr(event, "error_message", None)
        if err:
            parts.append(str(err))
    except Exception:
        pass
    try:
        content = getattr(event, "content", None)
        if content:
            for p in (getattr(content, "parts", None) or []):
                txt = getattr(p, "text", None)
                if txt:
                    parts.append(str(txt))
    except Exception:
        pass
    return "\n".join(parts).strip()


def _extract_balanced_call(text: str, start_pattern: str) -> Optional[str]:
    """
    Find the first regex match of start_pattern in text, then walk forward tracking
    parenthesis depth to return the full balanced call expression.
    Returns None if pattern not found or parens are unbalanced.
    """
    import re as _re
    match = _re.search(start_pattern, text)
    if not match:
        return None
    start = match.start()
    depth = 0
    for i, ch in enumerate(text[start:]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start: start + i + 1]
    return None


_KNOWN_MAPPING_INPUT_CLASSES = frozenset({
    "BuildDriverMappingToolInput",          # ADK-generated schema name (seen in Event 18)
    "BuildDriverMappingInput",              # our Pydantic class name
})


def _ast_node_to_python(node):
    """
    Recursively convert an ast node to a plain Python value.
    Call nodes (constructor calls like FilterCandidateInput(...)) → dict of their kwargs.
    Safe: no eval, no builtins, only handles literals + known structural nodes.
    """
    if isinstance(node, _ast.Constant):
        return node.value
    if isinstance(node, _ast.List):
        return [_ast_node_to_python(e) for e in node.elts]
    if isinstance(node, _ast.Tuple):
        return tuple(_ast_node_to_python(e) for e in node.elts)
    if isinstance(node, _ast.Dict):
        return {
            _ast_node_to_python(k): _ast_node_to_python(v)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }
    if isinstance(node, _ast.Name):
        return {"None": None, "True": True, "False": False}.get(node.id)
    if isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.USub):
        val = _ast_node_to_python(node.operand)
        return -val if val is not None else None
    if isinstance(node, _ast.Attribute):
        # Return the attribute name string (e.g. "BuildDriverMappingToolInput").
        # Recursing into node.value would hit the default_api Name → None.
        return node.attr
    if isinstance(node, _ast.Call):
        # Any constructor call → collect keyword args as a plain dict
        return {
            kw.arg: _ast_node_to_python(kw.value)
            for kw in node.keywords
            if kw.arg is not None
        }
    return None


def _find_mapping_input_call(node) -> Optional[_ast.Call]:
    """Walk AST depth-first to find the first Call whose func name is a known input class."""
    if isinstance(node, _ast.Call):
        func = node.func
        name = (
            func.id if isinstance(func, _ast.Name)
            else func.attr if isinstance(func, _ast.Attribute)
            else ""
        )
        if name in _KNOWN_MAPPING_INPUT_CLASSES:
            return node
    for child in _ast.iter_child_nodes(node):
        result = _find_mapping_input_call(child)
        if result is not None:
            return result
    return None


def _try_parse_malformed_mapping_call(
    raw_text: str, agent_label: str, req_id: str
) -> Optional[dict]:
    """
    Parse a Python-syntax build_driver_mapping_tool call from model-emitted text.
    Handles the "Malformed function call: print(...)" prefix from Vertex AI.
    Returns a kwargs dict suitable for constructing BuildDriverMappingInput, or None.
    Uses ast.parse only — no eval, no code execution.
    """
    import re as _re

    if "build_driver_mapping_tool" not in raw_text:
        return None
    if not any(cls in raw_text for cls in (
        "BuildDriverMappingToolInput", "BuildDriverMappingInput",
        "FilterCandidateInput", "BuildDriverMappingToolInputFilterCandidates",
        "filter_candidates",
    )):
        return None

    try:
        # Strip markdown fences and default_api. prefix
        clean = _re.sub(r"```(?:python)?\s*\n?", "", raw_text).strip()
        clean = clean.replace("default_api.", "")

        # Extract the balanced call expression — skips any "Malformed function call: " prefix
        call_expr = _extract_balanced_call(clean, r"print\s*\(")
        if call_expr is None or "build_driver_mapping_tool" not in call_expr:
            call_expr = _extract_balanced_call(clean, r"build_driver_mapping_tool\s*\(")
        if call_expr is None:
            call_expr = clean  # last resort

        tree = _ast.parse(call_expr, mode="eval")

        input_call = _find_mapping_input_call(tree.body)
        if input_call is None:
            logger.warning(
                "[%s] [%s] malformed-call: no BuildDriverMappingToolInput found in AST",
                agent_label, req_id,
            )
            return None

        result = {
            kw.arg: _ast_node_to_python(kw.value)
            for kw in input_call.keywords
            if kw.arg is not None
        }

        if "filter_candidates" not in result:
            logger.warning(
                "[%s] [%s] malformed-call: no filter_candidates in parsed dict",
                agent_label, req_id,
            )
            return None

        logger.info(
            "[%s] [%s] malformed-call AST parse OK — filter_candidates=%d unmapped=%d",
            agent_label, req_id,
            len(result.get("filter_candidates") or []),
            len(result.get("unmapped_concepts") or []),
        )
        return result

    except SyntaxError as exc:
        logger.warning("[%s] [%s] malformed-call SyntaxError: %s", agent_label, req_id, exc)
    except Exception as exc:
        logger.warning(
            "[%s] [%s] malformed-call parse failed — %s: %s",
            agent_label, req_id, type(exc).__name__, exc,
        )
    return None


async def _recover_malformed_driver_mapping(
    malformed_text: str,
    runner,
    user_id: str,
    session_id: str,
    agent_label: str,
    req_id: str,
) -> Optional[dict]:
    """
    Parse the malformed call, invoke build_driver_mapping_tool server-side with a
    MockToolContext, write the recovered state to Vertex AI session, and return
    the driver_mapping dict. Returns None if any step fails.
    """
    parsed = _try_parse_malformed_mapping_call(malformed_text, agent_label, req_id)
    if parsed is None:
        return None

    _KNOWN_INPUT_FIELDS = {
        "in_scope_items", "out_of_scope_items", "requirements",
        "generic_tables", "standards_results",
        "filter_candidates", "unmapped_concepts", "extract_context",
    }
    try:
        mapping_input = BuildDriverMappingInput(
            **{k: v for k, v in parsed.items() if k in _KNOWN_INPUT_FIELDS}
        )
    except Exception as exc:
        logger.warning(
            "[%s] [%s] recovery: BuildDriverMappingInput construction failed — %s: %s",
            agent_label, req_id, type(exc).__name__, exc,
        )
        return None

    try:
        class _MockCtx:
            def __init__(self):
                self.state = {}

        mock_ctx = _MockCtx()
        build_driver_mapping_tool(input=mapping_input, tool_context=mock_ctx)
        driver_mapping = mock_ctx.state.get("driver_mapping")
        if driver_mapping is None:
            logger.warning(
                "[%s] [%s] recovery: build_driver_mapping_tool wrote no driver_mapping",
                agent_label, req_id,
            )
            return None
    except Exception as exc:
        logger.warning(
            "[%s] [%s] recovery: build_driver_mapping_tool failed — %s: %s",
            agent_label, req_id, type(exc).__name__, exc,
        )
        return None

    # Write to Vertex AI session state via the existing append_event path
    # so downstream /driver/logic can read driver_mapping from session state.
    try:
        svc = (
            getattr(runner, "session_service", None)
            or getattr(runner, "_session_service", None)
        )
        app = getattr(runner, "app", None) or getattr(runner, "_app", None)
        app_name = getattr(app, "name", None)
        if svc and app_name:
            await _write_session_state(
                session_service=svc,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state_delta={
                    "driver_mapping":  driver_mapping,
                    "ibc_aha_context": mock_ctx.state.get("ibc_aha_context"),
                    "extract_context": mock_ctx.state.get("extract_context", {}),
                },
                label=agent_label,
            )
    except Exception as exc:
        logger.warning(
            "[%s] [%s] recovery: session state write failed (non-fatal) — %s",
            agent_label, req_id, exc,
        )

    logger.warning(
        "[%s] [%s] MALFORMED-CALL RECOVERY SUCCESS — ibc_aha=%s candidates=%d unmapped=%d",
        agent_label, req_id,
        driver_mapping.get("ibc_aha_context"),
        len(driver_mapping.get("filter_candidates", [])),
        len(driver_mapping.get("unmapped_concepts", [])),
    )
    return driver_mapping


async def _run_business_mapping_pipeline(
    session_service: VertexAiSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
    mapping_payload: dict,
    req_id: str,
    event_dir=None,
) -> tuple:
    """
    Run the two-agent business mapping pipeline:
      Agent 1a: standards_search_agent  — searches standards, saves results to state
      Agent 1b: mapping_builder_agent   — reads results, builds candidates, writes driver_mapping

    Returns (driver_mapping, total_events).
    """
    total_events = 0

    # --- Agent 1a: standards_search_agent ---
    search_app = _build_app(app_name, standards_search_agent, "standards_search_agent")
    search_runner = Runner(app=search_app, session_service=session_service)

    logger.info("[business-mapping] [%s] standards_search_agent starting", req_id)
    _, n_search = await _run_agent_collect_state_key(
        runner=search_runner,
        user_id=user_id,
        session_id=session_id,
        user_text=json.dumps(mapping_payload, indent=2),
        state_key="standards_results",
        agent_label="standards_search_agent",
        req_id=req_id,
        event_dir=event_dir,
    )
    total_events += n_search
    logger.info("[business-mapping] [%s] standards_search_agent done — events=%d", req_id, n_search)

    driver_step_delay_sec = float(os.getenv("DRIVER_STEP_DELAY_SEC", "2.0"))
    if driver_step_delay_sec > 0:
        logger.info(
            "[business-mapping] [%s] driver step delay %.2fs before mapping_builder_agent",
            req_id,
            driver_step_delay_sec,
        )
        await asyncio.sleep(driver_step_delay_sec)

    # Read standards_results written by agent 1a
    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id,
    )
    state = getattr(session, "state", None) or {}
    standards_results = state.get("standards_results") or []
    logger.info("[business-mapping] [%s] standards_results=%d entries", req_id, len(standards_results))

    # --- Agent 1b: mapping_builder_agent ---
    builder_payload = {**mapping_payload, "standards_results": standards_results}
    builder_app = _build_app(app_name, mapping_builder_agent, "mapping_builder_agent")
    builder_runner = Runner(app=builder_app, session_service=session_service)

    logger.info("[business-mapping] [%s] mapping_builder_agent starting", req_id)
    driver_mapping, n_builder = await _run_agent_collect_state_key(
        runner=builder_runner,
        user_id=user_id,
        session_id=session_id,
        user_text=json.dumps(builder_payload, indent=2),
        state_key="driver_mapping",
        agent_label="mapping_builder_agent",
        req_id=req_id,
        event_dir=event_dir,
        event_offset=total_events,
    )
    total_events += n_builder
    logger.info("[business-mapping] [%s] mapping_builder_agent done — events=%d", req_id, n_builder)

    return driver_mapping, total_events


def _derive_driver_output_uri(brd_uri: str) -> str:
    """
    Derive the GCS output path for approved_driver_layer_output.json from the brd_uri.
    Strips the last two path segments (subfolder + filename) and appends
    driver_data/approved_driver_layer_output.json.

    Example:
      in:  gs://bsa-data-map-artifacts/bsa-extract-artifacts/sess_xxx/extracted_data/validated_requirement_layer.json
      out: gs://bsa-data-map-artifacts/bsa-extract-artifacts/sess_xxx/driver_data/approved_driver_layer_output.json
    """
    import re as _re
    match = _re.match(r"(gs://[^/]+)/(.+)", brd_uri.strip())
    if not match:
        raise ValueError(f"Invalid GCS URI '{brd_uri}'")
    bucket_prefix = match.group(1)
    blob_path = match.group(2)
    parts = blob_path.rstrip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"GCS URI path too short to derive driver output path: '{brd_uri}'")
    base_parts = parts[:-2]
    output_blob = "/".join(base_parts) + "/driver_data/approved_driver_layer_output.json"
    return f"{bucket_prefix}/{output_blob}"


def _save_json_to_gcs(gcs_uri: str, data: dict, label: str, req_id: str) -> None:
    """Upload a dict as JSON to a GCS URI. Overwrites if the blob already exists."""
    import re as _re
    from utils.gcs_artifact_utils import artifact_storage_client

    match = _re.match(r"gs://([^/]+)/(.+)", gcs_uri.strip())
    if not match:
        raise ValueError(f"Invalid GCS URI '{gcs_uri}'")
    bucket_name = match.group(1)
    blob_path = match.group(2)

    gcs_client = artifact_storage_client()  # local filesystem-backed storage (no GCP)

    payload = json.dumps(data, indent=2, default=str).encode("utf-8")
    gcs_client.bucket(bucket_name).blob(blob_path).upload_from_string(
        payload, content_type="application/json"
    )
    logger.info(
        "[%s] [%s] GCS save — bucket=%s blob=%s bytes=%d",
        label, req_id, bucket_name, blob_path, len(payload),
    )


# ---------------------------------------------------------------------------
# Endpoint 1 — single standards search
# ---------------------------------------------------------------------------

@router.post("/driver/standards-search", summary="Test standards search with a single query")
def standards_search(request: SingleQueryRequest):
    """Send a single natural-language query to search_standards_tool."""
    logger.info("[driver/standards-search] query: %s", request.query)
    result = search_standards_tool(SearchStandardsInput(query=request.query))
    return StandardsSearchResult(
        query=result["query"],
        status=result["status"],
        answer_text=result.get("answer_text", ""),
        note=result.get("note", ""),
    )


# ---------------------------------------------------------------------------
# Endpoint 2 — batch standards search from BRD
# ---------------------------------------------------------------------------

@router.post("/driver/standards-search-from-brd", summary="Run standards search for all filter concepts in a parsed BRD")
def standards_search_from_brd(brd: Dict[str, Any]):
    """Accepts a parsed BRD JSON and runs search_standards_tool for each filter concept."""
    concepts = _extract_filter_concepts(brd)
    logger.info("[driver/standards-search-from-brd] %d filter concepts extracted", len(concepts))
    results = []
    for concept in concepts:
        raw = search_standards_tool(SearchStandardsInput(query=concept.query))
        results.append({
            "source": concept.source,
            "source_id": concept.source_id,
            "query": concept.query,
            "status": raw["status"],
            "answer_text": raw.get("answer_text", ""),
            "note": raw.get("note", ""),
        })
    return BrdSearchResponse(total_concepts=len(concepts), results=results)


# ---------------------------------------------------------------------------
# Endpoint 3 — Step 1: business_mapping_agent
# ---------------------------------------------------------------------------

class DriverStepRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    brd_uri: Optional[str] = None        # GCS URI (gs://bucket/path) sent by UI from gcs_output_uri
    brd: Optional[Dict[str, Any]] = None # inline BRD dict — for direct API calls / dev only


@router.post("/driver/business-mapping", summary="Step 1 — Run business_mapping_agent: BRD concepts → DART filter candidates")
async def driver_business_mapping(request: DriverStepRequest):
    """
    Step 1 of the driver pipeline.
    Maps BRD filter concepts to standard DART fields using AIDataDeliveryStandards RAG.
    Writes driver_mapping and extract_context to session state.

    BRD input (priority order):
      1. brd_uri — GCS URI of the validated_requirement_layer JSON produced by the
                   requirement layer (e.g. gs://bucket/.../validated_requirement_layer.json).
                   This is the standard production path — UI passes gcs_output_uri here.
      2. brd     — inline BRD dict in the request body (direct API calls / dev only).
      3. omit both — falls back to the local dummy BRD file (local testing only).
    """
    req_id = str(uuid.uuid4())[:8]
    start = time.time()
    # event_dir = _DRIVER_EVENTS_DIR / req_id  # local dev debug events
    event_dir = None
    label = "driver/business-mapping"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s brd_uri=%s brd_inline=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
        request.brd_uri or "", request.brd is not None,
    )

    try:
        brd_data        = _resolve_brd_data(request, label, req_id)
        normalized      = _normalize_brd_input(brd_data)
        extract_context = _build_extract_context(normalized)

        logger.info(
            "[%s] [%s] BRD normalised format=%s — population_type='%s' subject_areas='%s' interface=%s",
            label, req_id, normalized.get("format_version", ""),
            extract_context.get("file_population_type", ""),
            extract_context.get("subject_areas", ""),
            extract_context.get("interface_code", ""),
        )

        payload = {
            "in_scope": normalized["in_scope"],
            "out_of_scope": normalized["out_of_scope"],
            "requirements": normalized["requirements"],
            "generic_tables": normalized["generic_tables"],
            "filters_and_parameters": normalized["filters_and_parameters"],
            "eligibility_criteria": [],
            "date_criteria": [],
            "parsed_transcript": [],
            "extract_context": extract_context,
        }

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        await _get_session(session_service, request.appName, request.userId, request.sessionId, label)

        # Clear stale state before pipeline starts
        await _write_session_state(
            session_service=session_service, app_name=request.appName,
            user_id=request.userId, session_id=request.sessionId,
            state_delta={"driver_mapping": None, "standards_results": None}, label=label,
        )

        driver_mapping, event_count = await _run_business_mapping_pipeline(
            session_service=session_service,
            app_name=request.appName,
            user_id=request.userId,
            session_id=request.sessionId,
            mapping_payload=payload,
            req_id=req_id,
            event_dir=event_dir,
        )

        # Auto-retry once on no_output
        if not driver_mapping:
            logger.warning("[%s] [%s] no_output after %d events — retrying once", label, req_id, event_count)
            driver_mapping, n_retry = await _run_business_mapping_pipeline(
                session_service=session_service,
                app_name=request.appName,
                user_id=request.userId,
                session_id=request.sessionId,
                mapping_payload=payload,
                req_id=req_id,
                event_dir=event_dir,
            )
            event_count += n_retry

        elapsed = round(time.time() - start, 2)

        if not driver_mapping:
            logger.warning("[%s] [%s] no driver_mapping in state after %d events", label, req_id, event_count)
            return {
                "status": "no_output", "req_id": req_id, "events": event_count,
                "elapsed_sec": elapsed,
                "message": "business mapping pipeline did not write driver_mapping to session state.",
            }

        logger.info(
            "[%s] [%s] done — elapsed=%.2fs events=%d candidates=%d unmapped=%d ibc_aha=%s",
            label, req_id, elapsed, event_count,
            len(driver_mapping.get("filter_candidates", [])),
            len(driver_mapping.get("unmapped_concepts", [])),
            driver_mapping.get("ibc_aha_context"),
        )
        return {
            "status": "ok", "req_id": req_id, "elapsed_sec": elapsed,
            "events": event_count,
            "summary": {
                "candidate_count": len(driver_mapping.get("filter_candidates", [])),
                "unmapped_count": len(driver_mapping.get("unmapped_concepts", [])),
                "ibc_aha_context": driver_mapping.get("ibc_aha_context"),
            },
            "driver_mapping": driver_mapping,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")


# ---------------------------------------------------------------------------
# Endpoint 4 — Step 2: logic_builder_agent
# ---------------------------------------------------------------------------

@router.post("/driver/logic", summary="Step 2 — Run logic_builder_agent: filter candidates → SQL predicates")
async def driver_logic(request: DriverStepRequest):
    """
    Step 2 of the driver pipeline.
    Runs fyi_lookup_tool for any candidates with needs_fyi_lookup=True, then
    converts filter candidates into SQL CommonFilter predicates.
    Reads driver_mapping from session state (produced by /driver/business-mapping).
    Writes driver_logic to session state.

    extract_context (used by fyi_lookup_tool for table ranking) priority:
      1. brd_uri / brd in request — downloads/uses BRD directly for freshest context.
      2. Neither provided        — reads extract_context stored in session state by Step 1.
    Omit brd_uri if Step 1 was called in the same session — context is already stored.
    """
    req_id = str(uuid.uuid4())[:8]
    start = time.time()
    # event_dir = _DRIVER_EVENTS_DIR / req_id  # local dev debug events
    event_dir = None
    label = "driver/logic"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
    )

    try:
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        session = await _get_session(session_service, request.appName, request.userId, request.sessionId, label)

        state = getattr(session, "state", None) or {}
        driver_mapping = state.get("driver_mapping")
        if not driver_mapping:
            raise HTTPException(
                status_code=400,
                detail="driver_mapping not found in session state. Run /driver/business-mapping first.",
            )

        # extract_context priority: fresh from request BRD/URI > stored from Step 1 > empty dict
        if request.brd_uri or request.brd:
            brd_data   = _resolve_brd_data(request, label, req_id)
            normalized = _normalize_brd_input(brd_data)
            extract_context = _build_extract_context(normalized)
            logger.info(
                "[%s] [%s] extract_context built fresh — format=%s population_type='%s' subject_areas='%s'",
                label, req_id, normalized.get("format_version", ""),
                extract_context.get("file_population_type", ""),
                extract_context.get("subject_areas", ""),
            )
        else:
            extract_context = state.get("extract_context", {})
            logger.info(
                "[%s] [%s] extract_context from session state — population_type='%s' subject_areas='%s'",
                label, req_id,
                extract_context.get("file_population_type", ""),
                extract_context.get("subject_areas", ""),
            )

        payload = {
            "filter_candidates": driver_mapping.get("filter_candidates", []),
            "unmapped_concepts": driver_mapping.get("unmapped_concepts", []),
            "ibc_aha_context": driver_mapping.get("ibc_aha_context", "IBC"),
            "extract_context": extract_context,
        }

        # Clear stale state so the agent doesn't skip tool calls on retry
        await _write_session_state(
            session_service=session_service, app_name=request.appName,
            user_id=request.userId, session_id=request.sessionId,
            state_delta={"driver_logic": None}, label=label,
        )

        app = _build_app(request.appName, logic_builder_agent, label)
        runner = Runner(app=app, session_service=session_service)

        driver_logic_result, event_count = await _run_agent_collect_state_key(
            runner=runner,
            user_id=request.userId,
            session_id=request.sessionId,
            user_text=json.dumps(payload, indent=2),
            state_key="driver_logic",
            agent_label=label,
            req_id=req_id,
            event_dir=event_dir,
        )

        # Auto-retry once on no_output
        if not driver_logic_result:
            retry_payload = {
                **payload,
                "retry_instruction": (
                    "Previous logic_builder_agent attempt ended without writing driver_logic. "
                    "Use compact FYI candidates, avoid long reasoning, and call "
                    "build_driver_logic_tool exactly once."
                ),
            }
            logger.warning("[%s] [%s] no_output after %d events — retrying once", label, req_id, event_count)
            driver_logic_result, n_retry = await _run_agent_collect_state_key(
                runner=runner, user_id=request.userId, session_id=request.sessionId,
                user_text=json.dumps(retry_payload, indent=2), state_key="driver_logic",
                agent_label=f"{label}[retry]", req_id=req_id, event_dir=event_dir,
            )
            event_count += n_retry

        elapsed = round(time.time() - start, 2)

        if not driver_logic_result:
            logger.warning("[%s] [%s] no driver_logic in state after %d events", label, req_id, event_count)
            return {
                "status": "no_output", "req_id": req_id, "events": event_count,
                "elapsed_sec": elapsed,
                "message": "logic_builder_agent did not write driver_logic to session state.",
            }

        common_filters = driver_logic_result.get("common_filters", [])
        bsa_questions = [
            {"filter_id": f.get("filter_id"), "dart_field": f.get("dart_field"), "bsa_question": f.get("bsa_question")}
            for f in common_filters if f.get("open_item") and f.get("bsa_question")
        ]

        logger.info(
            "[%s] [%s] done — elapsed=%.2fs events=%d filters=%d open_items=%d bsa_q=%d sql_len=%d",
            label, req_id, elapsed, event_count,
            driver_logic_result.get("global_filter_count", 0),
            driver_logic_result.get("open_item_count", 0),
            len(bsa_questions),
            len(str(driver_logic_result.get("sql_where_clause", ""))),
        )
        return {
            "status": "ok", "req_id": req_id, "elapsed_sec": elapsed,
            "events": event_count,
            "summary": {
                "filter_count": driver_logic_result.get("global_filter_count", 0),
                "open_item_count": driver_logic_result.get("open_item_count", 0),
                "bsa_question_count": len(bsa_questions),
                "ibc_aha_context": driver_logic_result.get("ibc_aha_context"),
            },
            "bsa_questions": bsa_questions,
            "sql_where_clause": driver_logic_result.get("sql_where_clause"),
            "driver_logic": driver_logic_result,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")


# ---------------------------------------------------------------------------
# Endpoint 5 — Step 3: driver_validator_agent
# ---------------------------------------------------------------------------

@router.post("/driver/validate", summary="Step 3 — Run driver_validator_agent: validate SQL predicates")
async def driver_validate(request: DriverStepRequest):
    """
    Step 3 of the driver pipeline.
    Validates driver_logic against 4 checks:
      transformation logic, standards compliance, conflict detection, BRD traceability.
    Reads driver_logic entirely from session state (produced by /driver/logic).
    Writes driver_validation to session state.

    No BRD input needed — brd_uri and brd are ignored for this step.
    All data is read from session state.
    """
    req_id = str(uuid.uuid4())[:8]
    start = time.time()
    # event_dir = _DRIVER_EVENTS_DIR / req_id  # local dev debug events
    event_dir = None
    label = "driver/validate"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
    )

    try:
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        session = await _get_session(session_service, request.appName, request.userId, request.sessionId, label)

        state = getattr(session, "state", None) or {}
        driver_logic_state = state.get("driver_logic")
        if not driver_logic_state:
            raise HTTPException(
                status_code=400,
                detail="driver_logic not found in session state. Run /driver/logic first.",
            )

        payload = {
            "common_filters": driver_logic_state.get("common_filters", []),
            "sql_where_clause": driver_logic_state.get("sql_where_clause", ""),
            "requirements": state.get("requirements", ""),
            "ibc_aha_context": driver_logic_state.get("ibc_aha_context", "IBC"),
        }

        # Clear stale state so the agent doesn't skip tool calls on retry
        await _write_session_state(
            session_service=session_service, app_name=request.appName,
            user_id=request.userId, session_id=request.sessionId,
            state_delta={"driver_validation": None}, label=label,
        )

        app = _build_app(request.appName, driver_validator_agent, label)
        runner = Runner(app=app, session_service=session_service)

        driver_validation, event_count = await _run_agent_collect_state_key(
            runner=runner,
            user_id=request.userId,
            session_id=request.sessionId,
            user_text=json.dumps(payload, indent=2),
            state_key="driver_validation",
            agent_label=label,
            req_id=req_id,
            event_dir=event_dir,
        )

        # Auto-retry once on no_output
        if not driver_validation:
            logger.warning("[%s] [%s] no_output after %d events — retrying once", label, req_id, event_count)
            driver_validation, n_retry = await _run_agent_collect_state_key(
                runner=runner, user_id=request.userId, session_id=request.sessionId,
                user_text=json.dumps(payload, indent=2), state_key="driver_validation",
                agent_label=f"{label}[retry]", req_id=req_id, event_dir=event_dir,
            )
            event_count += n_retry

        elapsed = round(time.time() - start, 2)

        if not driver_validation:
            logger.warning("[%s] [%s] no driver_validation in state after %d events", label, req_id, event_count)
            return {
                "status": "no_output", "req_id": req_id, "events": event_count,
                "elapsed_sec": elapsed,
                "message": "driver_validator_agent did not write driver_validation to session state.",
            }

        logger.info(
            "[%s] [%s] done — elapsed=%.2fs events=%d can_proceed=%s high=%d medium=%d standards_ok=%s",
            label, req_id, elapsed, event_count,
            driver_validation.get("can_proceed"),
            driver_validation.get("total_high", 0),
            driver_validation.get("total_medium", 0),
            driver_validation.get("standards_compliant"),
        )
        return {
            "status": "ok", "req_id": req_id, "elapsed_sec": elapsed,
            "events": event_count,
            "summary": {
                "can_proceed": driver_validation.get("can_proceed"),
                "total_high": driver_validation.get("total_high", 0),
                "total_medium": driver_validation.get("total_medium", 0),
                "standards_compliant": driver_validation.get("standards_compliant"),
                "no_transformation_logic": driver_validation.get("no_transformation_logic"),
                "all_brd_requirements_traced": driver_validation.get("all_brd_requirements_traced"),
            },
            "issues": driver_validation.get("issues", []),
            "driver_validation": driver_validation,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")


# ---------------------------------------------------------------------------
# Endpoint 6 — Full pipeline: all 3 agents sequential
# ---------------------------------------------------------------------------

@router.post("/driver/run", summary="Full pipeline — business_mapping → logic → validate (sequential)")
async def driver_run(request: DriverStepRequest):
    """
    Runs all 3 driver agents sequentially in the same Vertex AI session.

    Step 1: business mapping pipeline → driver_mapping  (writes extract_context to session state)
    Step 2: logic_builder_agent    → driver_logic     (calls fyi_lookup_tool for ambiguous columns)
    Step 3: driver_validator_agent → driver_validation

    BRD input (priority order):
      1. brd_uri — GCS URI of the validated_requirement_layer JSON (standard production path).
                   UI passes gcs_output_uri from the requirement layer output here.
      2. brd     — inline BRD dict in the request body (direct API calls / dev only).
      3. omit both — falls back to the local dummy BRD file (local testing only).

    Returns: bsa_questions, sql_where_clause, driver_mapping, driver_logic,
             driver_validation, can_proceed, and per-step event counts.
    """
    req_id = str(uuid.uuid4())[:8]
    start = time.time()
    # event_dir = _DRIVER_EVENTS_DIR / req_id  # local dev debug events
    event_dir = None
    label = "driver/run"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s brd_uri=%s brd_inline=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
        request.brd_uri or "", request.brd is not None,
    )

    try:
        # ------------------------------------------------------------------
        # 0. Load + normalize BRD
        # ------------------------------------------------------------------
        brd_data        = _resolve_brd_data(request, label, req_id)
        normalized      = _normalize_brd_input(brd_data)
        extract_context = _build_extract_context(normalized)
        logger.info(
            "[%s] [%s] BRD normalised format=%s — population_type='%s' subject_areas='%s' interface=%s",
            label, req_id, normalized.get("format_version", ""),
            extract_context.get("file_population_type", ""),
            extract_context.get("subject_areas", ""),
            extract_context.get("interface_code", ""),
        )

        # ------------------------------------------------------------------
        # 1. Shared Vertex AI session
        # ------------------------------------------------------------------
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        await _get_session(session_service, request.appName, request.userId, request.sessionId, label)

        total_events = 0

        # ------------------------------------------------------------------
        # 2. Step 1 — business mapping pipeline (standards_search + mapping_builder)
        # ------------------------------------------------------------------
        mapping_payload = {
            "in_scope": normalized["in_scope"],
            "out_of_scope": normalized["out_of_scope"],
            "requirements": normalized["requirements"],
            "generic_tables": normalized["generic_tables"],
            "filters_and_parameters": normalized["filters_and_parameters"],
            "eligibility_criteria": [],
            "date_criteria": [],
            "parsed_transcript": [],
            "extract_context": extract_context,
        }

        logger.info("[%s] [%s] Step 1: business mapping pipeline starting", label, req_id)
        driver_mapping, n1 = await _run_business_mapping_pipeline(
            session_service=session_service,
            app_name=request.appName,
            user_id=request.userId,
            session_id=request.sessionId,
            mapping_payload=mapping_payload,
            req_id=req_id,
            event_dir=event_dir,
        )
        total_events += n1

        if not driver_mapping:
            logger.error("[%s] [%s] Step 1 failed — no driver_mapping after %d events", label, req_id, total_events)
            return {
                "status": "error", "step": "business_mapping", "req_id": req_id,
                "message": "business_mapping_agent did not produce driver_mapping.",
                "events": total_events,
                "elapsed_sec": round(time.time() - start, 2),
            }

        logger.info(
            "[%s] [%s] Step 1 done — candidates=%d ibc_aha_context=%s",
            label, req_id, len(driver_mapping.get("filter_candidates", [])), driver_mapping.get("ibc_aha_context"),
        )

        # ------------------------------------------------------------------
        # 3. Step 2 — logic_builder_agent
        # ------------------------------------------------------------------
        logic_payload = {
            "filter_candidates": driver_mapping.get("filter_candidates", []),
            "unmapped_concepts": driver_mapping.get("unmapped_concepts", []),
            "ibc_aha_context": driver_mapping.get("ibc_aha_context", "IBC"),
            "extract_context": extract_context,
        }
        logic_app = _build_app(request.appName, logic_builder_agent, "logic_builder_agent")
        logic_runner = Runner(app=logic_app, session_service=session_service)

        logger.info("[%s] [%s] Step 2: logic_builder_agent starting", label, req_id)
        driver_logic_result, n2 = await _run_agent_collect_state_key(
            runner=logic_runner,
            user_id=request.userId,
            session_id=request.sessionId,
            user_text=json.dumps(logic_payload, indent=2),
            state_key="driver_logic",
            agent_label="logic_builder_agent",
            req_id=req_id,
            event_dir=event_dir,
            event_offset=total_events,
        )
        total_events += n2

        if not driver_logic_result:
            logger.error("[%s] [%s] Step 2 failed — no driver_logic after %d events", label, req_id, total_events)
            return {
                "status": "error", "step": "logic_builder", "req_id": req_id,
                "message": "logic_builder_agent did not produce driver_logic.",
                "events": total_events,
                "elapsed_sec": round(time.time() - start, 2),
                "driver_mapping": driver_mapping,
            }

        logger.info(
            "[%s] [%s] Step 2 done — filters=%d open_items=%d",
            label, req_id, driver_logic_result.get("global_filter_count", 0), driver_logic_result.get("open_item_count", 0),
        )

        # ------------------------------------------------------------------
        # 4. Step 3 — driver_validator_agent
        # ------------------------------------------------------------------
        validator_payload = {
            "common_filters": driver_logic_result.get("common_filters", []),
            "sql_where_clause": driver_logic_result.get("sql_where_clause", ""),
            "requirements": normalized["requirements"],
            "ibc_aha_context": driver_logic_result.get("ibc_aha_context", "IBC"),
        }
        validator_app = _build_app(request.appName, driver_validator_agent, "driver_validator_agent")
        validator_runner = Runner(app=validator_app, session_service=session_service)

        logger.info("[%s] [%s] Step 3: driver_validator_agent starting", label, req_id)
        driver_validation, n3 = await _run_agent_collect_state_key(
            runner=validator_runner,
            user_id=request.userId,
            session_id=request.sessionId,
            user_text=json.dumps(validator_payload, indent=2),
            state_key="driver_validation",
            agent_label="driver_validator_agent",
            req_id=req_id,
            event_dir=event_dir,
            event_offset=total_events,
        )
        total_events += n3

        if not driver_validation:
            logger.warning("[%s] [%s] driver_validator_agent did not produce driver_validation", label, req_id)
            driver_validation = {"can_proceed": None, "issues": [], "note": "Validator did not write state"}

        logger.info(
            "[%s] [%s] Step 3 done — can_proceed=%s issues=%d",
            label, req_id, driver_validation.get("can_proceed"), len(driver_validation.get("issues", [])),
        )

        # ------------------------------------------------------------------
        # 5. Assemble response
        # ------------------------------------------------------------------
        elapsed = round(time.time() - start, 2)
        common_filters = driver_logic_result.get("common_filters", [])
        bsa_questions = [
            {"filter_id": f.get("filter_id"), "dart_field": f.get("dart_field"), "bsa_question": f.get("bsa_question")}
            for f in common_filters if f.get("open_item") and f.get("bsa_question")
        ]

        return {
            "status": "ok",
            "req_id": req_id,
            "elapsed_sec": elapsed,
            "total_events": total_events,
            "can_proceed": driver_validation.get("can_proceed"),
            "summary": {
                "ibc_aha_context": driver_mapping.get("ibc_aha_context"),
                "filter_count": driver_logic_result.get("global_filter_count", 0),
                "open_item_count": driver_logic_result.get("open_item_count", 0),
                "bsa_question_count": len(bsa_questions),
                "validation_high_issues": driver_validation.get("total_high", 0),
                "validation_medium_issues": driver_validation.get("total_medium", 0),
                "standards_compliant": driver_validation.get("standards_compliant"),
                "no_transformation_logic": driver_validation.get("no_transformation_logic"),
                "all_brd_requirements_traced": driver_validation.get("all_brd_requirements_traced"),
            },
            "bsa_questions": bsa_questions,
            "sql_where_clause": driver_logic_result.get("sql_where_clause"),
            "driver_mapping": driver_mapping,
            "driver_logic": driver_logic_result,
            "driver_validation": driver_validation,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] Internal error in driver pipeline: {exc}")


# ---------------------------------------------------------------------------
# Endpoint 7 — BSA Direct Edit: Save driver_logic to session state
# ---------------------------------------------------------------------------

class DriverSaveRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    driver_logic: Dict[str, Any]


@router.post("/driver/save", summary="BSA direct edit — save edited driver_logic to session state")
async def driver_save(request: DriverSaveRequest):
    """
    BSA has directly edited the driver output in the UI and clicks Save.
    Accepts the full driver_logic object, rebuilds sql_where_clause and
    open_item_count server-side (deterministic, no LLM), then writes to
    session state. No validation — BSA is the final authority on their edits.
    GCS save happens only on /driver/approve.
    """
    req_id = str(uuid.uuid4())[:8]
    start  = time.time()
    label  = "driver/save"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
    )

    try:
        common_filters = request.driver_logic.get("common_filters") or []

        # Rebuild sql_where_clause from global filters — server-side, deterministic
        global_clauses = [
            f["sql_clause"] for f in common_filters
            if f.get("filter_scope", "global") == "global"
            and f.get("sql_clause", "").strip()
            and not f.get("sql_clause", "").strip().startswith("-- OPEN ITEM")
        ]
        sql_where = "\n  AND ".join(global_clauses)

        open_item_count = sum(1 for f in common_filters if f.get("open_item"))

        updated_driver_logic = {
            **request.driver_logic,
            "sql_where_clause": sql_where,
            "open_item_count":  open_item_count,
        }

        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        await _write_session_state(
            session_service = session_service,
            app_name        = request.appName,
            user_id         = request.userId,
            session_id      = request.sessionId,
            state_delta     = {"driver_logic": updated_driver_logic},
            label           = label,
        )

        bsa_questions = [
            {"filter_id": f.get("filter_id"), "dart_field": f.get("dart_field"), "bsa_question": f.get("bsa_question")}
            for f in common_filters if f.get("open_item") and f.get("bsa_question")
        ]

        elapsed = round(time.time() - start, 2)
        logger.info(
            "[%s] [%s] saved — filters=%d open_items=%d elapsed=%.2fs",
            label, req_id, len(common_filters), open_item_count, elapsed,
        )
        return {
            "status":      "saved",
            "req_id":      req_id,
            "elapsed_sec": elapsed,
            "summary": {
                "filter_count":       updated_driver_logic.get("global_filter_count", len(common_filters)),
                "open_item_count":    open_item_count,
                "bsa_question_count": len(bsa_questions),
                "ibc_aha_context":    updated_driver_logic.get("ibc_aha_context"),
            },
            "bsa_questions":    bsa_questions,
            "sql_where_clause": sql_where,
            "driver_logic":     updated_driver_logic,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")


# ---------------------------------------------------------------------------
# Endpoint 8 — Human Checkpoint: Approve
# ---------------------------------------------------------------------------

class DriverApproveRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    brd_uri: Optional[str] = None   # GCS URI of the BRD — used to derive the driver output save path
    bsa_notes: Optional[str] = None
    bsa_edits: Optional[Dict[str, Any]] = None
    user_session_id : str


@router.post("/driver/approve", summary="Human Checkpoint — BSA approves driver output as-is")
async def driver_approve(request: DriverApproveRequest):
    """
    BSA approves the current driver_logic without changes.
    Reads driver_logic from session state, wraps it into ApprovedDriverLogic,
    and writes approved_driver_logic back to session state.
    This unlocks the next pipeline stage.
    """
    from datetime import datetime as _dt
    from agents.extract_agent.driver_agent.models import ApprovedDriverLogic

    req_id = str(uuid.uuid4())[:8]
    start  = time.time()
    label  = "driver/approve"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s user_session_id=%s",
        label, req_id, request.appName, request.sessionId, request.userId, request.user_session_id
    )

    try:
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        session = await _get_session(session_service, request.appName, request.userId, request.sessionId, label)
        state   = getattr(session, "state", None) or {}

        driver_logic = state.get("driver_logic")
        if not driver_logic:
            raise HTTPException(
                status_code=400,
                detail="driver_logic not found in session state. Run /driver/validate first.",
            )

        approved = ApprovedDriverLogic(
            common_filters    = driver_logic.get("common_filters", []),
            sql_where_clause  = driver_logic.get("sql_where_clause", ""),
            ibc_aha_context   = driver_logic.get("ibc_aha_context", "IBC"),
            bsa_edits         = request.bsa_edits or {},
            bsa_notes         = request.bsa_notes or "",
            approved_at       = _dt.utcnow().isoformat(),
        )

        await _write_session_state(
            session_service = session_service,
            app_name        = request.appName,
            user_id         = request.userId,
            session_id      = request.sessionId,
            state_delta     = {"approved_driver_logic": approved.model_dump()},
            label           = label,
        )

        # ------------------------------------------------------------------
        # GCS save — write approved_driver_layer_output.json using user_session_id
        # ------------------------------------------------------------------
        gcs_output_uri = (
            f"gs://{config.MAPPING_ARTIFACT_BUCKET}"
            f"/{config.BSA_EXTRACT_ARTIFACT_PREFIX}"
            f"/{request.user_session_id}/driver_data/approved_driver_layer_output.json"
        )
        try:
            _save_json_to_gcs(
                gcs_uri = gcs_output_uri,
                data    = approved.model_dump(),
                label   = label,
                req_id  = req_id,
            )
            logger.info(
                "[%s] [%s] driver output saved to GCS — uri=%s",
                label, req_id, gcs_output_uri,
            )
        except Exception as gcs_exc:
            logger.exception(
                "[%s] [%s] GCS save failed (session state already written) — %s",
                label, req_id, gcs_exc,
            )
            gcs_output_uri = None

        elapsed = round(time.time() - start, 2)
        logger.info(
            "[%s] [%s] approved — filters=%d open_items=%d gcs=%s elapsed=%.2fs",
            label, req_id,
            len(approved.common_filters),
            sum(1 for f in approved.common_filters if getattr(f, "open_item", False)
                or (isinstance(f, dict) and f.get("open_item"))),
            gcs_output_uri or "skipped",
            elapsed,
        )
        return {
            "status": "approved",
            "req_id": req_id,
            "elapsed_sec": elapsed,
            "gcs_output_uri": gcs_output_uri,
            "approved_driver_logic": approved.model_dump(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")


# ---------------------------------------------------------------------------
# Endpoint 8 — Human Checkpoint: Checkpoint (BSA freeform correction + re-run)
# ---------------------------------------------------------------------------

class DriverCheckpointRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    instruction: str               # BSA freeform correction text
    brd_uri: Optional[str] = None  # GCS URI — same as other endpoints


@router.post("/driver/checkpoint", summary="Human Checkpoint — BSA provides instruction, re-runs full pipeline")
async def driver_checkpoint(request: DriverCheckpointRequest):
    """
    BSA provides a freeform correction instruction. The full 3-step driver pipeline
    is re-run with the instruction appended to the business mapping payload as
    bsa_instruction. The agent applies the correction before finalising filter_candidates.

    BRD input: brd_uri (GCS URI) if the session does not already have extract_context
    stored from a prior /driver/business-mapping call. If extract_context is in session
    state it is reused automatically when brd_uri is omitted.

    Overwrites driver_mapping, driver_logic, driver_validation in session state.
    Returns the same response shape as /driver/run.
    """
    req_id = str(uuid.uuid4())[:8]
    start  = time.time()
    label  = "driver/checkpoint"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s brd_uri=%s instruction='%.80s'",
        label, req_id, request.appName, request.sessionId, request.userId,
        request.brd_uri or "", request.instruction,
    )

    try:
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        await _get_session(session_service, request.appName, request.userId, request.sessionId, label)

        # ------------------------------------------------------------------
        # 0. BRD + extract_context
        # ------------------------------------------------------------------
        if request.brd_uri:
            brd_data        = _resolve_brd_data(request, label, req_id)
            normalized      = _normalize_brd_input(brd_data)
            extract_context = _build_extract_context(normalized)
            logger.info(
                "[%s] [%s] BRD normalised format=%s population_type='%s'",
                label, req_id, normalized.get("format_version", ""),
                extract_context.get("file_population_type", ""),
            )
        else:
            # Reuse extract_context stored by the prior business-mapping call
            session = await _get_session(session_service, request.appName, request.userId, request.sessionId, label)
            state   = getattr(session, "state", None) or {}
            normalized      = {}
            extract_context = state.get("extract_context", {})
            logger.info(
                "[%s] [%s] extract_context from session state — population_type='%s'",
                label, req_id, extract_context.get("file_population_type", ""),
            )
            if not extract_context:
                logger.warning(
                    "[%s] [%s] extract_context empty — provide brd_uri for accurate FYI ranking",
                    label, req_id,
                )

        total_events = 0

        # ------------------------------------------------------------------
        # 1. Step 1 — business mapping pipeline (with bsa_instruction in payload)
        # ------------------------------------------------------------------
        mapping_payload = {
            "in_scope":               normalized.get("in_scope", ""),
            "out_of_scope":           normalized.get("out_of_scope", ""),
            "requirements":           normalized.get("requirements", ""),
            "generic_tables":         normalized.get("generic_tables", []),
            "filters_and_parameters": normalized.get("filters_and_parameters", {}),
            "eligibility_criteria":   [],
            "date_criteria":          [],
            "parsed_transcript":      [],
            "extract_context":        extract_context,
            "bsa_instruction":        request.instruction,  # passed to mapping_builder_agent
        }

        logger.info("[%s] [%s] Step 1: business mapping pipeline starting (with bsa_instruction)", label, req_id)
        driver_mapping, n1 = await _run_business_mapping_pipeline(
            session_service=session_service,
            app_name=request.appName,
            user_id=request.userId,
            session_id=request.sessionId,
            mapping_payload=mapping_payload,
            req_id=req_id,
        )
        total_events += n1

        if not driver_mapping:
            logger.error("[%s] [%s] Step 1 failed — no driver_mapping after %d events", label, req_id, total_events)
            return {
                "status": "error", "step": "business_mapping", "req_id": req_id,
                "message": "business_mapping_agent did not produce driver_mapping.",
                "events": total_events, "elapsed_sec": round(time.time() - start, 2),
            }

        logger.info(
            "[%s] [%s] Step 1 done — candidates=%d ibc_aha=%s",
            label, req_id,
            len(driver_mapping.get("filter_candidates", [])),
            driver_mapping.get("ibc_aha_context"),
        )

        # ------------------------------------------------------------------
        # 2. Step 2 — logic_builder_agent
        # ------------------------------------------------------------------
        logic_payload = {
            "filter_candidates": driver_mapping.get("filter_candidates", []),
            "unmapped_concepts": driver_mapping.get("unmapped_concepts", []),
            "ibc_aha_context":   driver_mapping.get("ibc_aha_context", "IBC"),
            "extract_context":   extract_context,
        }
        logic_app    = _build_app(request.appName, logic_builder_agent, "logic_builder_agent")
        logic_runner = Runner(app=logic_app, session_service=session_service)

        logger.info("[%s] [%s] Step 2: logic_builder_agent starting", label, req_id)
        driver_logic_result, n2 = await _run_agent_collect_state_key(
            runner      = logic_runner,
            user_id     = request.userId,
            session_id  = request.sessionId,
            user_text   = json.dumps(logic_payload, indent=2),
            state_key   = "driver_logic",
            agent_label = "logic_builder_agent",
            req_id      = req_id,
        )
        total_events += n2

        if not driver_logic_result:
            logger.error("[%s] [%s] Step 2 failed — no driver_logic after %d events", label, req_id, total_events)
            return {
                "status": "error", "step": "logic_builder", "req_id": req_id,
                "message": "logic_builder_agent did not produce driver_logic.",
                "events": total_events, "elapsed_sec": round(time.time() - start, 2),
                "driver_mapping": driver_mapping,
            }

        logger.info(
            "[%s] [%s] Step 2 done — filters=%d open_items=%d",
            label, req_id,
            driver_logic_result.get("global_filter_count", 0),
            driver_logic_result.get("open_item_count", 0),
        )

        # ------------------------------------------------------------------
        # 3. Step 3 — driver_validator_agent
        # ------------------------------------------------------------------
        validator_payload = {
            "common_filters":    driver_logic_result.get("common_filters", []),
            "sql_where_clause":  driver_logic_result.get("sql_where_clause", ""),
            "requirements":      normalized.get("requirements", ""),
            "ibc_aha_context":   driver_logic_result.get("ibc_aha_context", "IBC"),
        }
        validator_app    = _build_app(request.appName, driver_validator_agent, "driver_validator_agent")
        validator_runner = Runner(app=validator_app, session_service=session_service)

        logger.info("[%s] [%s] Step 3: driver_validator_agent starting", label, req_id)
        driver_validation, n3 = await _run_agent_collect_state_key(
            runner      = validator_runner,
            user_id     = request.userId,
            session_id  = request.sessionId,
            user_text   = json.dumps(validator_payload, indent=2),
            state_key   = "driver_validation",
            agent_label = "driver_validator_agent",
            req_id      = req_id,
        )
        total_events += n3

        if not driver_validation:
            logger.warning("[%s] [%s] driver_validator_agent did not produce driver_validation", label, req_id)
            driver_validation = {"can_proceed": None, "issues": [], "note": "Validator did not write state"}

        logger.info(
            "[%s] [%s] Step 3 done — can_proceed=%s issues=%d",
            label, req_id,
            driver_validation.get("can_proceed"),
            len(driver_validation.get("issues", [])),
        )

        # ------------------------------------------------------------------
        # 4. Assemble response
        # ------------------------------------------------------------------
        elapsed      = round(time.time() - start, 2)
        common_filters = driver_logic_result.get("common_filters", [])
        bsa_questions  = [
            {"filter_id": f.get("filter_id"), "dart_field": f.get("dart_field"), "bsa_question": f.get("bsa_question")}
            for f in common_filters if f.get("open_item") and f.get("bsa_question")
        ]

        return {
            "status": "ok",
            "req_id": req_id,
            "elapsed_sec": elapsed,
            "total_events": total_events,
            "can_proceed": driver_validation.get("can_proceed"),
            "summary": {
                "ibc_aha_context":            driver_mapping.get("ibc_aha_context"),
                "filter_count":               driver_logic_result.get("global_filter_count", 0),
                "open_item_count":            driver_logic_result.get("open_item_count", 0),
                "bsa_question_count":         len(bsa_questions),
                "validation_high_issues":     driver_validation.get("total_high", 0),
                "validation_medium_issues":   driver_validation.get("total_medium", 0),
                "standards_compliant":        driver_validation.get("standards_compliant"),
                "no_transformation_logic":    driver_validation.get("no_transformation_logic"),
                "all_brd_requirements_traced": driver_validation.get("all_brd_requirements_traced"),
            },
            "bsa_questions":   bsa_questions,
            "sql_where_clause": driver_logic_result.get("sql_where_clause"),
            "driver_mapping":   driver_mapping,
            "driver_logic":     driver_logic_result,
            "driver_validation": driver_validation,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] Internal error in checkpoint: {exc}")


# ---------------------------------------------------------------------------
# Endpoint 9 — Human Checkpoint: Patch Filter (surgical single-filter edit)
# ---------------------------------------------------------------------------

class DriverPatchFilterRequest(BaseModel):
    appName: str
    sessionId: str
    userId: str
    filter_id: str              # e.g. "F002"
    edits: Dict[str, Any]       # partial — only fields to change


@router.post("/driver/patch-filter", summary="Human Checkpoint — Patch a single filter then re-validate")
async def driver_patch_filter(request: DriverPatchFilterRequest):
    """
    BSA edits one specific filter by filter_id (e.g. wrong values, wrong sql_clause,
    resolve an open item). Only fields present in edits are updated — all other fields
    on the filter are preserved.

    After patching:
      - sql_where_clause is rebuilt from all global filters.
      - open_item_count is recounted.
      - driver_validator_agent is re-run on the patched driver_logic.
      - driver_logic and driver_validation are overwritten in session state.

    Returns: { status, filter_id, driver_logic, driver_validation }
    """
    req_id = str(uuid.uuid4())[:8]
    start  = time.time()
    label  = "driver/patch-filter"
    logger.info(
        "[%s] [%s] appName=%s sessionId=%s userId=%s filter_id=%s edits_keys=%s",
        label, req_id, request.appName, request.sessionId, request.userId,
        request.filter_id, list(request.edits.keys()),
    )

    try:
        session_service = VertexAiSessionService(
            project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION,
        )
        session = await _get_session(session_service, request.appName, request.userId, request.sessionId, label)
        state   = getattr(session, "state", None) or {}

        driver_logic = state.get("driver_logic")
        if not driver_logic:
            raise HTTPException(
                status_code=400,
                detail="driver_logic not found in session state. Run /driver/validate first.",
            )

        # ------------------------------------------------------------------
        # 1. Find and patch the filter
        # ------------------------------------------------------------------
        common_filters = driver_logic.get("common_filters", [])
        target_idx = next(
            (i for i, f in enumerate(common_filters) if f.get("filter_id") == request.filter_id),
            None,
        )
        if target_idx is None:
            raise HTTPException(
                status_code=404,
                detail=f"filter_id '{request.filter_id}' not found in driver_logic.common_filters.",
            )

        before = common_filters[target_idx].copy()
        common_filters[target_idx].update(request.edits)
        after  = common_filters[target_idx]

        logger.info(
            "[%s] [%s] filter_id=%s patched — open_item %s→%s sql_clause_changed=%s",
            label, req_id, request.filter_id,
            before.get("open_item"), after.get("open_item"),
            before.get("sql_clause") != after.get("sql_clause"),
        )

        # ------------------------------------------------------------------
        # 2. Rebuild sql_where_clause from all global filters
        # ------------------------------------------------------------------
        global_clauses = [
            f["sql_clause"] for f in common_filters
            if f.get("filter_scope", "global") == "global"
            and f.get("sql_clause", "").strip()
            and not f.get("sql_clause", "").strip().startswith("-- OPEN ITEM")
        ]
        new_sql_where = "\n  AND ".join(global_clauses)

        open_item_count = sum(1 for f in common_filters if f.get("open_item"))

        patched_driver_logic = {
            **driver_logic,
            "common_filters":    common_filters,
            "sql_where_clause":  new_sql_where,
            "open_item_count":   open_item_count,
        }

        logger.info(
            "[%s] [%s] sql_where rebuilt — global_clauses=%d open_items=%d",
            label, req_id, len(global_clauses), open_item_count,
        )

        # ------------------------------------------------------------------
        # 3. Re-run driver_validator_agent on patched logic
        # ------------------------------------------------------------------
        validator_payload = {
            "common_filters":   common_filters,
            "sql_where_clause": new_sql_where,
            "requirements":     state.get("requirements", ""),
            "ibc_aha_context":  patched_driver_logic.get("ibc_aha_context", "IBC"),
        }
        validator_app    = _build_app(request.appName, driver_validator_agent, "driver_validator_agent")
        validator_runner = Runner(app=validator_app, session_service=session_service)

        logger.info("[%s] [%s] re-running driver_validator_agent", label, req_id)
        driver_validation, event_count = await _run_agent_collect_state_key(
            runner      = validator_runner,
            user_id     = request.userId,
            session_id  = request.sessionId,
            user_text   = json.dumps(validator_payload, indent=2),
            state_key   = "driver_validation",
            agent_label = "driver_validator_agent",
            req_id      = req_id,
        )

        if not driver_validation:
            logger.warning("[%s] [%s] driver_validator_agent did not produce driver_validation", label, req_id)
            driver_validation = {"can_proceed": None, "issues": [], "note": "Validator did not write state"}

        # ------------------------------------------------------------------
        # 4. Write patched driver_logic to session state
        #    (validator already wrote driver_validation via tool_context.state)
        # ------------------------------------------------------------------
        await _write_session_state(
            session_service = session_service,
            app_name        = request.appName,
            user_id         = request.userId,
            session_id      = request.sessionId,
            state_delta     = {"driver_logic": patched_driver_logic},
            label           = label,
        )

        elapsed = round(time.time() - start, 2)
        logger.info(
            "[%s] [%s] done — can_proceed=%s high=%d elapsed=%.2fs events=%d",
            label, req_id,
            driver_validation.get("can_proceed"),
            driver_validation.get("total_high", 0),
            elapsed, event_count,
        )

        return {
            "status":           "patched",
            "req_id":           req_id,
            "filter_id":        request.filter_id,
            "elapsed_sec":      elapsed,
            "events":           event_count,
            "can_proceed":      driver_validation.get("can_proceed"),
            "open_item_count":  open_item_count,
            "sql_where_clause": new_sql_where,
            "driver_logic":     patched_driver_logic,
            "driver_validation": driver_validation,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[%s] [%s] Unexpected error: %s", label, req_id, exc)
        raise HTTPException(status_code=500, detail=f"[{req_id}] {exc}")
