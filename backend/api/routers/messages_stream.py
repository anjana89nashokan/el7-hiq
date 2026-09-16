# server/api/routers/messages_stream.py
from pydantic import BaseModel
import requests, os
from pathlib import Path
import json
from utils.bg_query_utils import get_table
import logging
from pydoc import text
import time
import asyncio
from typing import Optional, Dict, Any, List

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from api.models import MessageRequest,QARequest
from agents.data_map_copilot_agent.agent import root_agent

from google.adk import Runner
from google.adk.apps import App
from google.adk.sessions import Session
from utils.adk_runtime import VertexAiSessionService
from google.genai import types
from google.genai.errors import ServerError
from google import genai

from config.settings import config
from utils.streaming_progress import StreamingProgressTracker, FeatureType
from utils.markdown_formatter import generate_error_markdown
from .messages import get_data_ditionary # Import from the original messages router
from google.adk.events import Event, EventActions
from utils.llm_helper import GoogleGeminiClient
import uuid
import re
import json


router = APIRouter()

def extract_json_from_string(text_blob: str):
    """
    Extracts a JSON object from a string that might be embedded in a markdown code block.

    This function looks for a JSON block formatted as ```json ... ```. It handles
    cases where there is text before or after the block, or if the string
    consists only of the block itself.

    Args:
        text_blob: The input string containing the JSON data.

    Returns:
        A Python dictionary or list if a valid JSON object is found, otherwise None.
    """
    # Regex to find the content within ```json ... ```
    # re.DOTALL allows '.' to match newline characters, which is crucial for multi-line JSON
    pattern = r"```json\s*(.*?)\s*```"

    match = re.search(pattern, text_blob, re.DOTALL)

    # If a JSON block is found
    if match:
        # The actual JSON string is in the first captured group
        json_string = match.group(1)


        return json_string
    else:
        # If no ```json ... ``` block is found, return None
        return None

    # Try to parse the extracted string into a Python object

def format_output(stage, data):
    
    gemini_client = GoogleGeminiClient()

    response = gemini_client.generate(stage, data)
    print("+"*200)
    print(response)
    print("+"*200)

    return response

def get_text_between_brackets(s: str) -> str:
    start = s.find('[')
    end = s.find(']', start + 1)
    return s[start + 1:end] if start != -1 and end != -1 else ''

def get_data_ditionary(dd_reference: List['str']):
    results = []
    for dd_ref in dd_reference:
        print("getting table for ", dd_ref)
        table = get_table(dd_ref)
        results.append(table)
    return results


@router.post("/send-stream")
async def send_message_stream(request: MessageRequest):
    """
    Streaming version of /send endpoint using Server-Sent Events (SSE).
    Provides real-time progress updates for all DataMap Copilot features.
    """

    async def event_generator():
        try:
            req = request.dict()
            message_text = req["newMessage"]['parts'][0]['text']
            logging.info(f"Stream Message Received: {message_text}")

            tracker: Optional[StreamingProgressTracker] = None

            def ensure_tracker(feature: FeatureType, total_items: int = 0, message: Optional[str] = None):
                nonlocal tracker
                if tracker and tracker.feature_type == feature:
                    return None
                tracker = StreamingProgressTracker(feature, total_items=total_items)
                init_event = tracker.get_init_event(message)
                return {
                    "event": init_event["event"],
                    "data": json.dumps(init_event["data"])
                }

            def emit_complete_events(result: Dict[str, Any], feature_hint: FeatureType) -> List[Dict[str, Any]]:
                events: List[Dict[str, Any]] = []
                init_evt = ensure_tracker(feature_hint)
                if init_evt:
                    events.append(init_evt)
                if tracker:
                    complete_event = tracker.get_complete_event(result=result)
                    events.append({
                        "event": complete_event["event"],
                        "data": json.dumps(complete_event["data"])
                    })
                return events

            def emit_error_events(message: str, feature_hint: FeatureType, details: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
                events: List[Dict[str, Any]] = []
                init_evt = ensure_tracker(feature_hint)
                if init_evt:
                    events.append(init_evt)
                if tracker:
                    error_event = tracker.get_error_event(message, details)
                    events.append({
                        "event": error_event["event"],
                        "data": json.dumps(error_event["data"])
                    })
                return events

            yield {
                "event": "status",
                "data": json.dumps({
                    "phase": "init",
                    "feature": "unknown",
                    "message": "Initializing request...",
                    "progress": 0,
                    "total_items": 0
                })
            }

            data_dictionary_context = ""
            if req.get('additional_data') and req['additional_data'].get('data_dictionary'):
                data_dictionary_reference = req['additional_data']['data_dictionary']
                data_dictionary_content = get_data_ditionary(data_dictionary_reference)
                data_dictionary_context = f"\n\n Data Dictionary Context: {json.dumps(data_dictionary_content)}"

            session_service = VertexAiSessionService(project=config.GOOGLE_CLOUD_PROJECT, location=config.GOOGLE_CLOUD_LOCATION)
            session_id = req.get("sessionId")
            app_name = req["appName"]
            
            try:
                session = await session_service.get_session(
                    app_name=app_name,
                    user_id=req["userId"],
                    session_id=session_id
                )
                
                state_update_event = Event(
                    author="system",
                    invocation_id=f"sys-inv-{uuid.uuid4()}",
                    actions=EventActions(state_delta={"is_stream": True})
                )

                # Append the event to persist the state change
                await session_service.append_event(session=session, event=state_update_event)
                logging.info(f"Appended is_stream=True event to session {session_id}")
                
            except Exception as e:
                logging.error(f"Could not append event to session {session_id}: {e}")
                yield {"event": "error", "data": json.dumps({"message": f"Failed to set session state: {e}"})}
                return
            
            # Handle similarity check data if provided
            # Handle similarity check data if provided
            if req.get('stateDelta', {}).get('similarity_dart_references'):
                logging.info("[send-stream] - Similarity structured data detected in stateDelta")
                dart_refs = req['stateDelta']['similarity_dart_references']
                source_tables = req['stateDelta'].get('similarity_source_tables', [])

                logging.info(f"[send-stream]   - DART refs: {len(dart_refs)} tables")
                logging.info(f"[send-stream]   - Source tables: {len(source_tables)} tables")

                # INJECT INTO SESSION STATE (bypasses LLM parsing entirely)
                try:
                    # Event and EventActions already imported at top of file
                    similarity_state_event = Event(
                        author="system",
                        invocation_id=f"similarity-inject-{uuid.uuid4()}",
                        actions=EventActions(state_delta={
                            "similarity_dart_references": dart_refs,
                            "similarity_source_tables": source_tables
                        })
                    )
                    
                    # Append to session before running agent
                    await session_service.append_event(session=session, event=similarity_state_event)
                    logging.info("[send-stream] ✓ Injected similarity data into session state")
                    logging.info(f"[send-stream]   - dart_references: {dart_refs}")
                    logging.info(f"[send-stream]   - source_tables: {source_tables}")
                    
                except Exception as e:
                    logging.error(f"[send-stream] Failed to inject similarity data into session: {e}")
                    import traceback
                    traceback.print_exc()
                    # Continue anyway - will fall back to LLM parsing

            orchestrator_app = App(name=app_name, root_agent=root_agent)
            runner = Runner(app=orchestrator_app, session_service=session_service)

            msg = types.Content(
                role="user",
                parts=[types.Part(text=f"{message_text} {data_dictionary_context} \
 REGENRATE THE ANSWER EVEN IF ALREADY GENERATED without complaining.")]
            )
            
            should_exit = False #Fkag ti break outer loop after tool completion
            event_count = 0
            async for event in runner.run_async(
                user_id=req["userId"],
                session_id=session_id,
                new_message=msg
            ):

                event_count += 1
                print(f"Received event #{event_count}")
                with open(f"event_{event_count}.txt", "w", encoding="utf-8") as f:
                    f.write(f"{event}")

                if hasattr(event, "actions") and event.actions and hasattr(event.actions, "state_delta"):
                    state_delta = event.actions.state_delta

                    # Profiling complete
                    if 'final_profiling_response' in state_delta:
                        for event_payload in emit_complete_events(state_delta['final_profiling_response'], FeatureType.PROFILING):
                            yield event_payload
                        break

                    # Data Dictionary complete
                    elif 'final_data_dict_response' in state_delta:
                        for event_payload in emit_complete_events(state_delta['final_data_dict_response'], FeatureType.DATA_DICTIONARY):
                            yield event_payload
                        break

                    # Metadata Template complete
                    elif 'metadata_excel_file' in state_delta:
                        for event_payload in emit_complete_events(state_delta['metadata_excel_file'], FeatureType.METADATA_TEMPLATE):
                            yield event_payload
                        break

                    # Similarity check complete (if routed through /send-stream)
                    elif 'final_similarity_response' in state_delta:
                        for event_payload in emit_complete_events(state_delta['final_similarity_response'], FeatureType.SIMILARITY):
                            yield event_payload
                        break

                    # Progress updates - generic phase detection
                    # We can enhance this based on specific state_delta keys your agents emit

                if hasattr(event, "content") and event.content and \
                   hasattr(event.content, "parts") and event.content.parts:

                    for part in event.content.parts:
                        # Check for function_response (tool completed)
                        if hasattr(part, "function_response") and part.function_response:
                            func_response = part.function_response

                            # Handle profiling tool completion - MULTI-PASS PRODUCTION ARCHITECTURE
                            if func_response.name == "intelligent_profiling_tool":
                                logging.info(f"[send-stream] ✓ Profiling tool completed - starting multi-pass analysis")

                                try:
                                    # ==========================================
                                    # ADK-COMPLIANT APPROACH: Retrieve from session state
                                    # ==========================================
                                    # The tool stores full results in ToolContext.state (not in return value)
                                    # This prevents token limit errors while keeping data accessible

                                    # Get current session to access state
                                    session = await session_service.get_session(
                                        app_name=app_name,
                                        user_id=req["userId"],
                                        session_id=session_id
                                    )

                                    # Retrieve full results from session state (stored by tool)
                                    all_results = session.state.get('profiling_full_results', [])

                                    if all_results:
                                        logging.info(
                                            f"[send-stream] ✓ Retrieved full results from session state ({len(all_results)} tables) "
                                            f"- ADK-compliant approach"
                                        )
                                    else:
                                        # Fallback: If session state is empty, use tool response directly
                                        # (This handles backward compatibility with old tool versions)
                                        raw_response = func_response.response
                                        all_results = raw_response if isinstance(raw_response, list) else []
                                        logging.warning(
                                            f"[send-stream] Fallback: Retrieved {len(all_results)} results from tool response "
                                            f"(session state empty)"
                                        )

                                    logging.info(f"[send-stream] Processing {len(all_results)} table results for multi-pass analysis")

                                    tracker_init = ensure_tracker(FeatureType.PROFILING, total_items=len(all_results), message="Profiling analysis in progress...")
                                    if tracker_init:
                                        yield tracker_init

                                    # Send tool_complete event (90%)
                                    tool_complete_event = {
                                        "event": "tool_complete",
                                        "data": json.dumps({
                                            "tool_name": "intelligent_profiling_tool",
                                            "tool_response": {"result": all_results},
                                            "progress": 90,
                                            "message": f"Profiling complete ({len(all_results)} tables). Starting multi-pass LLM analysis..."
                                        })
                                    }
                                    yield tool_complete_event

                                    # MULTI-PASS ARCHITECTURE: Split into batches
                                    from utils.profiling_analysis_batched import (
                                        build_batch_profiling_analysis_prompt,
                                        build_aggregate_profiling_summary_prompt,
                                        build_searchable_index
                                    )
                                    from google import genai
                                    from google.genai import types as genai_types

                                    # Initialize Gemini client
                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION
                                    )
                                    model = config.AGENT_MODEL

                                    # Configuration
                                    # OPTIMIZED: Batch size reduced from 12 to 8 for typical 20-table workload
                                    # - 20 tables ÷ 8 = 3 batches (8, 8, 4) - more balanced than 12+8
                                    # - Each batch: ~8 tables × 16K tokens = ~128K tokens per LLM call
                                    # - Better progress granularity and more even LLM load distribution
                                    BATCH_SIZE = 8

                                    # Create batches
                                    table_batches = [all_results[i:i+BATCH_SIZE] for i in range(0, len(all_results), BATCH_SIZE)]
                                    total_batches = len(table_batches)

                                    logging.info(f"[send-stream] Split {len(all_results)} tables into {total_batches} batches (max {BATCH_SIZE} tables/batch)")

                                    # Process each batch
                                    all_batch_analyses = []
                                    full_markdown_analysis = ""

                                    for batch_idx, batch_tables in enumerate(table_batches):
                                        batch_num = batch_idx + 1
                                        logging.info(f"[send-stream] Processing batch {batch_num}/{total_batches} ({len(batch_tables)} tables)")

                                        # Yield batch start event
                                        yield {
                                            "event": "llm_batch_start",
                                            "data": json.dumps({
                                                "batch_number": batch_num,
                                                "total_batches": total_batches,
                                                "tables_in_batch": len(batch_tables),
                                                "progress": 90 + (batch_idx / total_batches * 8),  # 90-98%
                                                "message": f"Analyzing batch {batch_num}/{total_batches} ({len(batch_tables)} tables)..."
                                            })
                                        }

                                        # Build prompt for this batch
                                        batch_prompt = build_batch_profiling_analysis_prompt(
                                            batch_tables=batch_tables,
                                            batch_index=batch_num,
                                            total_batches=total_batches
                                        )

                                        # Stream LLM analysis for this batch
                                        batch_analysis = ""
                                        token_count = 0

                                        try:
                                            response_stream = client.models.generate_content_stream(
                                                model=model,
                                                contents=batch_prompt,
                                                config=genai_types.GenerateContentConfig(
                                                    temperature=0.3
                                                )
                                            )

                                            # Stream tokens to client
                                            for chunk in response_stream:
                                                token_text = None
                                                if hasattr(chunk, 'text') and chunk.text:
                                                    token_text = chunk.text
                                                elif hasattr(chunk, 'candidates') and chunk.candidates:
                                                    for candidate in chunk.candidates:
                                                        for part in getattr(candidate.content, 'parts', []):
                                                            if hasattr(part, 'text') and part.text:
                                                                token_text = part.text

                                                if token_text:
                                                    batch_analysis += token_text
                                                    full_markdown_analysis += token_text
                                                    token_count += 1

                                                    # Stream every 3 tokens
                                                    if token_count % 3 == 0:
                                                        base_progress = 90 + (batch_idx / total_batches * 8)
                                                        batch_progress = (len(batch_analysis) / 5000) * (8 / total_batches)
                                                        progress = min(base_progress + batch_progress, 98)

                                                        yield {
                                                            "event": "llm_token",
                                                            "data": json.dumps({
                                                                "token": token_text,
                                                                "cumulative": full_markdown_analysis,
                                                                "batch_number": batch_num,
                                                                "total_batches": total_batches,
                                                                "progress": round(progress, 1),
                                                                "message": f"Batch {batch_num}/{total_batches} analysis..."
                                                            })
                                                        }

                                            all_batch_analyses.append(batch_analysis)
                                            logging.info(f"[send-stream] ✓ Batch {batch_num}/{total_batches} complete ({len(batch_analysis)} chars)")

                                        except Exception as batch_error:
                                            logging.error(f"[send-stream] Error in batch {batch_num}: {batch_error}")
                                            batch_analysis = f"\n\n## Batch {batch_num}/{total_batches}: Error\n\n⚠️ Analysis failed for this batch: {str(batch_error)}\n\n"
                                            all_batch_analyses.append(batch_analysis)
                                            full_markdown_analysis += batch_analysis

                                    # FINAL PASS: Aggregate summary (98-100%)
                                    logging.info(f"[send-stream] Generating executive summary across all {len(all_results)} tables")

                                    yield {
                                        "event": "llm_summary_start",
                                        "data": json.dumps({
                                            "progress": 98,
                                            "message": "Generating executive summary across all tables..."
                                        })
                                    }

                                    # Build aggregate summary prompt
                                    summary_prompt = build_aggregate_profiling_summary_prompt(
                                        all_tables=all_results,
                                        batch_analyses=all_batch_analyses
                                    )

                                    # Generate executive summary
                                    executive_summary = ""
                                    token_count = 0

                                    try:
                                        response_stream = client.models.generate_content_stream(
                                            model=model,
                                            contents=summary_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                            )
                                        )

                                        for chunk in response_stream:
                                            token_text = None
                                            if hasattr(chunk, 'text') and chunk.text:
                                                token_text = chunk.text
                                            elif hasattr(chunk, 'candidates') and chunk.candidates:
                                                for candidate in chunk.candidates:
                                                    for part in getattr(candidate.content, 'parts', []):
                                                        if hasattr(part, 'text') and part.text:
                                                            token_text = part.text

                                            if token_text:
                                                executive_summary += token_text
                                                token_count += 1

                                                if token_count % 3 == 0:
                                                    progress = min(98 + (len(executive_summary) / 3000), 99.9)
                                                    yield {
                                                        "event": "llm_token",
                                                        "data": json.dumps({
                                                            "token": token_text,
                                                            "cumulative": full_markdown_analysis + "\n\n" + executive_summary,
                                                            "progress": round(progress, 1),
                                                            "message": "Generating executive summary..."
                                                        })
                                                    }

                                        logging.info(f"[send-stream] ✓ Executive summary complete ({len(executive_summary)} chars)")

                                    except Exception as summary_error:
                                        logging.error(f"[send-stream] Error generating summary: {summary_error}")
                                        executive_summary = f"\n\n## Executive Summary\n\n⚠️ Summary generation failed: {str(summary_error)}\n\n"

                                    # Combine all analyses
                                    final_markdown = executive_summary + "\n\n---\n\n" + full_markdown_analysis

                                    # Build searchable index for chat followup
                                    logging.info(f"[send-stream] Building searchable index for chat support")
                                    searchable_index = build_searchable_index(all_results)

                                    # PHASE 4: Send final complete event (100%) with enhanced tool_response
                                    final_result = {
                                        "text_response": final_markdown,
                                        "tool_response": {
                                            "all_tables": all_results,  # Complete data for all tables
                                            "searchable_index": searchable_index,  # For fast chat lookups
                                            "batch_analyses": all_batch_analyses,  # Individual batch analyses
                                            "summary": {
                                                "total_tables": len(all_results),
                                                "total_batches": total_batches,
                                                "analysis_complete": True
                                            }
                                        },
                                        "should_update": False
                                    }

                                    for event_payload in emit_complete_events(final_result, FeatureType.PROFILING):
                                        yield event_payload

                                    logging.info(f"[send-stream] ✓ Multi-pass profiling complete: {len(all_results)} tables, {total_batches} batches, {len(final_markdown)} chars")
                                    should_exit = True
                                    break  # Break inner for loop

                                except Exception as e:
                                    logging.error(f"[send-stream] Error in multi-pass profiling analysis: {e}")
                                    import traceback
                                    traceback.print_exc()

                                    # Fallback to error message
                                    error_markdown = generate_error_markdown(f"Error in multi-pass profiling analysis: {str(e)}")
                                    error_result = {
                                        "text_response": error_markdown,
                                        "tool_response": {"all_tables": all_results} if 'all_results' in locals() else {},
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(error_result, FeatureType.PROFILING):
                                        yield event_payload
                                    should_exit = True
                                    break  # Break inner for loop

                            # Handle relationship analysis tool completion with LLM streaming
                            elif func_response.name == "relationship_analysis_tool":
                                logging.info(f"[send-stream] ✓ Relationship analysis tool completed")

                                try:
                                    # Extract results
                                    raw_response = func_response.response

                                    num_tables = raw_response.get("tables_analyzed", 0)
                                    num_relationships = len(raw_response.get("cross_table_relationships", []))
                                    logging.info(f"[send-stream] Relationship tool returned {num_relationships} relationships across {num_tables} tables")

                                    tracker_init = ensure_tracker(FeatureType.RELATIONSHIP_ANALYSIS, total_items=num_tables, message="Relationship analysis in progress...")
                                    if tracker_init:
                                        yield tracker_init

                                    # PHASE 1: Send tool_complete event (95%)
                                    tool_complete_event = {
                                        "event": "tool_complete",
                                        "data": json.dumps({
                                            "tool_name": "relationship_analysis_tool",
                                            "tool_response": raw_response,
                                            "progress": 95,
                                            "message": f"Relationship analysis complete. Found {num_relationships} relationships. Generating intelligent insights..."
                                        })
                                    }
                                    yield tool_complete_event

                                    # PHASE 2: Build LLM analysis prompt (relationship-aware compression)
                                    from utils.relationship_analysis import build_relationship_analysis_prompt

                                    logging.info(f"[send-stream] Building intelligent relationship analysis prompt")
                                    analysis_prompt = build_relationship_analysis_prompt(raw_response)

                                    # Send llm_analysis_start event (96%)
                                    llm_start_event = {
                                        "event": "llm_analysis_start",
                                        "data": json.dumps({
                                            "progress": 96,
                                            "message": "Gemini is analyzing relationship patterns and data model architecture..."
                                        })
                                    }
                                    yield llm_start_event

                                    # PHASE 3: Get LLM analysis (streaming tokens)
                                    from google import genai
                                    from google.genai import types as genai_types

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION
                                    )
                                    model = config.AGENT_MODEL

                                    llm_analysis_text = ""
                                    token_count = 0

                                    logging.info(f"[send-stream] Starting LLM streaming analysis for relationships with {model}")
                                    logging.info(f"[send-stream] Prompt size: {len(analysis_prompt)} chars")

                                    try:
                                        # Call LLM with streaming enabled
                                        response_stream = client.models.generate_content_stream(
                                            model=model,
                                            contents=analysis_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3  # Balanced for relationship patterns
                                                # No max_output_tokens limit - relationships need complete analysis
                                            )
                                        )

                                        # Stream tokens to client
                                        for chunk in response_stream:
                                            if hasattr(chunk, 'text') and chunk.text:
                                                llm_analysis_text += chunk.text
                                                token_count += 1

                                                # Stream every 3 chunks
                                                if token_count % 3 == 0:
                                                    progress = min(96 + (len(llm_analysis_text) / 1000), 99.9)
                                                    yield {
                                                        "event": "llm_token",
                                                        "data": json.dumps({
                                                            "token": chunk.text,
                                                            "cumulative": llm_analysis_text,
                                                            "progress": round(progress, 1)
                                                        })
                                                    }
                                            elif hasattr(chunk, 'candidates') and chunk.candidates:
                                                # Fallback for different response format
                                                for candidate in chunk.candidates:
                                                    if hasattr(candidate.content, 'parts'):
                                                        for part in candidate.content.parts:
                                                            if hasattr(part, 'text') and part.text:
                                                                llm_analysis_text += part.text
                                                                token_count += 1

                                                                if token_count % 3 == 0:
                                                                    progress = min(96 + (len(llm_analysis_text) / 1000), 99.9)
                                                                    yield {
                                                                        "event": "llm_token",
                                                                        "data": json.dumps({
                                                                            "token": part.text,
                                                                            "cumulative": llm_analysis_text,
                                                                            "progress": round(progress, 1)
                                                                        })
                                                                    }

                                        logging.info(f"[send-stream] LLM relationship analysis complete ({len(llm_analysis_text)} chars, {token_count} chunks)")

                                    except AttributeError as e:
                                        # Fallback to non-streaming if streaming not supported
                                        logging.warning(f"[send-stream] Streaming not supported, using non-streaming: {e}")

                                        response = client.models.generate_content(
                                            model=model,
                                            contents=analysis_prompt,
                                            config=genai_types.GenerateContentConfig(
                                                temperature=0.3
                                                # No max_output_tokens limit
                                            )
                                        )

                                        # Extract text from response
                                        if hasattr(response, 'text'):
                                            llm_analysis_text = response.text.strip()
                                        elif hasattr(response, 'candidates') and len(response.candidates) > 0:
                                            llm_analysis_text = response.candidates[0].content.parts[0].text.strip()
                                        else:
                                            raise ValueError("Unable to extract text from LLM response")

                                        logging.info(f"[send-stream] LLM relationship analysis complete (non-streaming): {len(llm_analysis_text)} chars")

                                        # Send the full text as a single llm_token event (non-streaming fallback)
                                        yield {
                                            "event": "llm_token",
                                            "data": json.dumps({
                                                "token": llm_analysis_text,
                                                "cumulative": llm_analysis_text,
                                                "progress": 99.9
                                            })
                                        }

                                    # PHASE 4: Send final complete event (100%)
                                    # NOTE: text_response already streamed via llm_token events
                                    # Only send tool_response to avoid huge JSON (65KB+) that causes parsing errors
                                    final_result = {
                                        "text_response": llm_analysis_text,  # Still included for compatibility
                                        "tool_response": raw_response,
                                        "should_update": False
                                    }

                                    for event_payload in emit_complete_events(final_result, FeatureType.RELATIONSHIP_ANALYSIS):
                                        yield event_payload
                                    should_exit = True
                                    break  # Break inner for loop

                                except Exception as e:
                                    logging.error(f"[send-stream] Error in relationship analysis: {e}")
                                    import traceback
                                    traceback.print_exc()

                                    # Fallback to server-side markdown if LLM fails
                                    error_markdown = generate_error_markdown(f"Error generating relationship LLM analysis: {str(e)}")
                                    error_result = {
                                        "text_response": error_markdown,
                                        "tool_response": raw_response,
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(error_result, FeatureType.RELATIONSHIP_ANALYSIS):
                                        yield event_payload
                                    should_exit = True
                                    break  # Break inner for loop

                            # Handle data_dictionary_tool_v2 completion with batched LLM streaming
                            elif func_response.name == "data_dictionary_tool_v2":
                                logging.info(f"[send-stream] ✓ Data dictionary tool v2 completed")

                                try:
                                    # PHASE 1: Extract technical data from tool (95%)
                                    technical_data = func_response.response

                                    # Parse if JSON string
                                    if isinstance(technical_data, str):
                                        technical_data = json.loads(technical_data)

                                    num_columns = len(technical_data)
                                    logging.info(f"[send-stream] Processing {num_columns} columns for data dictionary")

                                    tracker_init = ensure_tracker(FeatureType.DATA_DICTIONARY, total_items=num_columns, message="Preparing data dictionary...")
                                    if tracker_init:
                                        yield tracker_init

                                    # Send tool_complete event
                                    tool_complete_event = {
                                        "event": "tool_complete",
                                        "data": json.dumps({
                                            "tool_name": "data_dictionary_tool_v2",
                                            "progress": 95,
                                            "message": f"Technical data merged. Generating business descriptions for {num_columns} columns...",
                                            "num_columns": num_columns
                                        })
                                    }
                                    yield tool_complete_event

                                    # PHASE 2: Create batches
                                    from utils.datadict_batched import (
                                        create_column_batches,
                                        process_batch_with_streaming,
                                        generate_datadict_markdown,
                                        generate_datadict_summary
                                    )

                                    batch_size = 50
                                    batches = create_column_batches(technical_data, batch_size)
                                    total_batches = len(batches)

                                    logging.info(f"[send-stream] Created {total_batches} batches")

                                    # PHASE 3: Process batches with token streaming (95-100%)
                                    from google import genai

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION
                                    )
                                    model = config.AGENT_MODEL

                                    all_enriched_columns = []
                                    processing_start_time = time.time()

                                    for batch_num, batch in enumerate(batches):
                                        logging.info(f"[send-stream] Processing batch {batch_num + 1}/{total_batches}")

                                        # Process batch with token streaming
                                        async for event in process_batch_with_streaming(
                                            batch, batch_num, total_batches, client, model
                                        ):
                                            # Forward event to client
                                            yield {
                                                "event": event["event"],
                                                "data": json.dumps(event["data"])
                                            }

                                            # Collect result if batch_complete
                                            if event["event"] == "batch_complete":
                                                batch_result = event["data"].get("result", [])
                                                all_enriched_columns.extend(batch_result)

                                    processing_time = time.time() - processing_start_time

                                    # PHASE 4: Generate markdown table
                                    logging.info(f"[send-stream] All batches complete. Generating markdown...")

                                    yield {
                                        "event": "status",
                                        "data": json.dumps({
                                            "progress": 99.5,
                                            "message": "Generating final data dictionary table..."
                                        })
                                    }

                                    markdown_text = generate_datadict_markdown(all_enriched_columns)
                                    summary_text = generate_datadict_summary(all_enriched_columns, total_batches, processing_time)

                                    # Combine summary + table
                                    full_text = summary_text + "\n\n---\n\n" + markdown_text

                                    # PHASE 5: Complete (100%)
                                    final_result = {
                                        "text_response": full_text,
                                        "tool_response": {"result": all_enriched_columns},
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(final_result, FeatureType.DATA_DICTIONARY):
                                        yield event_payload

                                    logging.info(f"[send-stream] Data dictionary complete: {len(all_enriched_columns)} columns in {processing_time:.1f}s")

                                    should_exit = True
                                    break  # Break inner for loop

                                except Exception as e:
                                    logging.error(f"[send-stream] Error in batched data dictionary: {e}")
                                    import traceback
                                    traceback.print_exc()

                                    error_markdown = generate_error_markdown(f"Error generating data dictionary: {str(e)}")
                                    error_result = {
                                        "text_response": error_markdown,
                                        "tool_response": {},
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(error_result, FeatureType.DATA_DICTIONARY):
                                        yield event_payload
                                    should_exit = True
                                    break  
                            elif func_response.name == "data_anomaly_analysis_tool":
                                logging.info("[send-stream] ✓ Data anomaly analysis tool completed")

                                try:
                                    raw_response = func_response.response
                                    if isinstance(raw_response, str):
                                        raw_response = json.loads(raw_response)

                                    tables_analyzed = raw_response.get("tables_analyzed") or raw_response.get("summary_statistics", {}).get("total_tables_analyzed", 0)
                                    tracker_init = ensure_tracker(FeatureType.ANOMALY_DETECTION, total_items=tables_analyzed, message="Anomaly detection in progress...")
                                    if tracker_init:
                                        yield tracker_init

                                    # 95% tool completion
                                    yield {
                                        "event": "tool_complete",
                                        "data": json.dumps({
                                            "tool_name": "data_anomaly_analysis_tool",
                                            "tool_response": raw_response,
                                            "progress": 95,
                                            "message": f"Anomaly detection complete ({tables_analyzed} tables). Generating executive summary..."
                                        })
                                    }

                                    # Build prompt
                                    from utils.anomaly_analysis import build_anomaly_analysis_prompt
                                    analysis_prompt = build_anomaly_analysis_prompt(raw_response)

                                    # 96% LLM analysis start
                                    yield {
                                        "event": "llm_analysis_start",
                                        "data": json.dumps({
                                            "progress": 96,
                                            "message": "Gemini is compiling anomaly insights..."
                                        })
                                    }

                                    # Stream Gemini output
                                    from google import genai
                                    from google.genai import types as genai_types

                                    client = genai.Client(
                                        vertexai=True,
                                        project=config.GOOGLE_CLOUD_PROJECT,
                                        location=config.GOOGLE_CLOUD_LOCATION
                                    )
                                    model = config.AGENT_MODEL

                                    llm_text = ""
                                    token_count = 0

                                    response_stream = client.models.generate_content_stream(
                                        model=model,
                                        contents=analysis_prompt,
                                        config=genai_types.GenerateContentConfig(temperature=0.25)
                                    )

                                    for chunk in response_stream:
                                        token_text = None
                                        if hasattr(chunk, "text") and chunk.text:
                                            token_text = chunk.text
                                        elif hasattr(chunk, "candidates") and chunk.candidates:
                                            for candidate in chunk.candidates:
                                                for part in getattr(candidate.content, "parts", []):
                                                    if hasattr(part, "text") and part.text:
                                                        token_text = part.text

                                        if token_text:
                                            llm_text += token_text
                                            token_count += 1
                                            if token_count % 3 == 0:
                                                progress = min(96 + (len(llm_text) / 1000), 99.9)
                                                yield {
                                                    "event": "llm_token",
                                                    "data": json.dumps({
                                                        "token": token_text,
                                                        "cumulative": llm_text,
                                                        "progress": round(progress, 1),
                                                        "message": "Streaming anomaly insights..."
                                                    })
                                                }

                                    # Completion
                                    final_result = {
                                        "text_response": llm_text.strip(),
                                        "tool_response": raw_response,
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(final_result, FeatureType.ANOMALY_DETECTION):
                                        yield event_payload
                                    should_exit = True
                                    break

                                except Exception as e:
                                    logging.error(f"[send-stream] Error in anomaly analysis: {e}")
                                    import traceback
                                    traceback.print_exc()

                                    error_markdown = generate_error_markdown(f"Error generating anomaly insights: {str(e)}")
                                    error_result = {
                                        "text_response": error_markdown,
                                        "tool_response": raw_response if 'raw_response' in locals() else {},
                                        "should_update": False
                                    }
                                    for event_payload in emit_complete_events(error_result, FeatureType.ANOMALY_DETECTION):
                                        yield event_payload
                                    should_exit = True
                                    break            

                if should_exit:
                    break
                
                if hasattr(event, "content") and event.content and \
                   hasattr(event.content, "parts") and event.content.parts and \
                   len(event.content.parts) > 0 and getattr(event.content.parts[0], "text", None):

                    obj = event.content.parts[0].text

                    # Check if it's a final response (JSON format)
                    if isinstance(obj, dict):
                        feature_hint = tracker.feature_type if tracker else FeatureType.PROFILING
                        for event_payload in emit_complete_events(obj, feature_hint):
                            yield event_payload
                        break
                    elif isinstance(obj, str):
                        new_obj = extract_json_from_string(obj)
                        if new_obj:
                            try:
                                parsed_obj = json.loads(new_obj)
                                feature_hint = tracker.feature_type if tracker else FeatureType.PROFILING
                                for event_payload in emit_complete_events(parsed_obj, feature_hint):
                                    yield event_payload
                                break
                            except json.JSONDecodeError:
                                pass

                # Handle errors
                if hasattr(event, 'error_code') and event.error_code:
                    if event.error_code == 'MALFORMED_FUNCTION_CALL':
                        # Try to extract response from malformed call
                        stage = get_text_between_brackets(req["newMessage"]['parts'][0]['text'])
                        output = format_output(stage, event)

                        feature_hint = tracker.feature_type if tracker else FeatureType.PROFILING
                        for event_payload in emit_complete_events(output, feature_hint):
                            yield event_payload
                        break
                    else:
                        error_message = f"{event.error_code}: {getattr(event, 'error_message', 'Unknown error')}"
                        feature_hint = tracker.feature_type if tracker else FeatureType.PROFILING
                        for error_payload in emit_error_events(error_message, feature_hint):
                            yield error_payload
                        break

                await asyncio.sleep(0.1)

            # If we exited loop without completion event, send generic complete
            feature_label = tracker.feature_type.value if tracker else "unknown"
            logging.info(f"SSE Stream completed for feature: {feature_label}")
        except Exception as e:
            logging.error(f"Error in SSE stream: {e}")
            import traceback
            traceback.print_exc()

            feature_hint = tracker.feature_type if tracker else FeatureType.PROFILING
            for error_payload in emit_error_events(f"Stream error: {str(e)}", feature_hint):
                yield error_payload

    return EventSourceResponse(event_generator())
