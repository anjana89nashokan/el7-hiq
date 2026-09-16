"""
Discovery Layer — ADK agent definitions.

Two agents in sequence:
  1. discovery_engine_agent — runs the priority engine across all target fields
  2. discovery_review_agent — reviews results and flags low-confidence fields
"""

from google.adk.agents import LlmAgent, SequentialAgent

from .prompts import DISCOVERY_ENGINE_INSTRUCTION, DISCOVERY_REVIEW_INSTRUCTION
from .tools import run_discovery_engine, save_discovery_results, finalize_discovery_results

MODEL = "gemini-2.5-flash"

# ─── Step 1: Run Discovery Engine ──────────────────────────────────────────

discovery_engine_agent = LlmAgent(
    name="discovery_engine_agent",
    model=MODEL,
    instruction=DISCOVERY_ENGINE_INSTRUCTION,
    tools=[run_discovery_engine, save_discovery_results],
    output_key="discovery_results_raw",
)

# ─── Step 2: Review Discovery Results ──────────────────────────────────────

discovery_review_agent = LlmAgent(
    name="discovery_review_agent",
    model=MODEL,
    instruction=DISCOVERY_REVIEW_INSTRUCTION,
    tools=[finalize_discovery_results],
    output_key="discovery_results",
)

# ─── Discovery Pipeline ─────────────────────────────────────────────────────

discovery_pipeline_agent = SequentialAgent(
    name="discovery_pipeline_agent",
    sub_agents=[
        discovery_engine_agent,
        discovery_review_agent,
    ],
    description=(
        "Warehouse Discovery pipeline. Runs the priority engine to find source "
        "tables/columns, then reviews results and flags fields needing BSA attention."
    ),
)
