"""
Large Data Flow Root Agent

This is the orchestrator agent for large data processing flow.
It is completely separate from the normal flow agent to ensure zero impact on existing functionality.

**Purpose:**
- Handle BigQuery table processing with 1000+ columns
- Delegate to appropriate sub-agents based on user requests
- Currently supports: Data Dictionary Generation

**Key Features:**
- Batched processing for scalability
- SSE streaming for real-time progress
- Isolated from normal flow (zero regression risk)
"""

from google.adk.agents import LlmAgent
from config.settings import config

# Import sub-agents
from .sub_agents.datadict_agent.agent import datadict_large_agent

# Agent configuration
agent_model = config.AGENT_MODEL

# Root Agent for Large Data Flow
large_data_root_agent = LlmAgent(
    name="large_data_orchestrator",
    model=agent_model,
    description="Orchestrator for large data processing flow (BigQuery tables with 1000+ columns)",

    instruction="""
You are the orchestrator agent for large data processing flow.

**YOUR ONLY JOB:** Immediately delegate to the appropriate sub-agent and LET IT COMPLETE.

**CRITICAL RULES:**
1. When user requests data dictionary → Immediately delegate to `datadict_large_agent`
2. **The sub-agent will NOT transfer back to you** - it will complete and save results to session state
3. **DO NOT ask questions** like "Would you like to save it?" or "Any changes needed?"
4. **DO NOT respond** after delegating - the sub-agent handles everything
5. Once delegated, **your job is done** - the endpoint will detect completion via state_delta

**How It Works:**
- User message: "Create data dictionary" or "[Generate Data Dictionary]"
- You: Immediately call `transfer_to_agent` with `agent_name="datadict_large_agent"`
- Sub-agent: Checks session state
  - IF `data_dict_file_path` exists → Extract from vendor DD file
  - IF NOT exists → Generate from profiling + relationship results
- Sub-agent: Saves result to `final_data_dict_response` in session state
- **You: DONE** (no further action, no questions, no commentary)

**Example (Correct):**
User: "Generate data dictionary"
You: [calls transfer_to_agent with agent_name="datadict_large_agent"]
[END - task complete, do not respond further]

**Example (WRONG - DO NOT DO THIS):**
User: "Generate data dictionary"
You: [calls transfer_to_agent]
You: "I have generated the data dictionary. Would you like to save it?" ❌ WRONG

**Remember:** Just delegate and complete. The endpoint handles result retrieval.
""",

    sub_agents=[
        datadict_large_agent
    ]
)
