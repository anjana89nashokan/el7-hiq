import asyncio
import json

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.apps import App
from utils.adk_runtime import VertexAiSessionService
from google.genai import types

from agents.mapping_ingestion.models import MappingContext, SourceSchema, TargetSchema, DataModelGraph
from config.settings import config
from .prompts import get_instruction_prompt


def _get_agent_engine_id() -> str | None:
    resource = getattr(config, "REASONING_ENGINE_RESOURCE", None)
    if not resource:
        return None
    resource = resource.strip()
    if not resource:
        return None
    # VertexAiSessionService expects just the engine ID, not the full resource path
    return resource.split("/")[-1]

instruction_agent = LlmAgent(
    name="mapping_instruction_agent",
    model=config.AGENT_MODEL,
    description="Parses BRD/prompt text into structured MappingContext with overrides and filters.",
    instruction=get_instruction_prompt(),
    output_schema=MappingContext,
    output_key="mapping_context",
)


async def run_instruction_agent(
    interface_code: str,
    source_schema: SourceSchema,
    target_schema: TargetSchema,
    data_model_graph: DataModelGraph,
    instructions_text: str,
) -> MappingContext:
    """
    Invoke instruction_agent via ADK Runner to get structured MappingContext.
    """
    app = App(name="mapping_instruction_app", root_agent=instruction_agent)
    session_service = VertexAiSessionService(
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.GOOGLE_CLOUD_LOCATION,
        agent_engine_id=_get_agent_engine_id(),
    )
    runner = Runner(app=app, session_service=session_service)

    base_prompt = get_instruction_prompt(interface_code)
    payload = f"""{base_prompt}

Instructions text:
{instructions_text}

Source schema (JSON):
{source_schema.model_dump_json()}

Target schema (JSON):
{target_schema.model_dump_json()}
"""

    msg = types.Content(role="user", parts=[types.Part(text=payload)])
    raw_json = None

    session = await session_service.create_session(
        app_name=app.name,
        user_id="system",
        state={},
    )

    async for event in runner.run_async(
        user_id="system",
        session_id=session.id,
        new_message=msg,
    ):
        if hasattr(event, "actions") and event.actions and getattr(event.actions, "state_delta", None):
            if "mapping_context" in event.actions.state_delta:
                raw_json = json.dumps(event.actions.state_delta["mapping_context"])
                break

    if not raw_json:
        raise ValueError("LLM returned empty response for mapping context.")

    return instruction_agent.output_schema.model_validate_json(raw_json)
