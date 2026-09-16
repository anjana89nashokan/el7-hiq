"""
Smart Similarity Root Agent - Standalone similarity check agent

This agent is completely decoupled from the main profiling flow.
Used exclusively for similarity checks via dedicated endpoint.

**Purpose:**
- Intelligent column matching between source and DART tables
- Two-phase approach: semantic matching + data overlap validation
- Batch processing for scalability

**Key Features:**
- Standalone operation (no coupling with profiling agent)
- Dedicated endpoint: /similarity-check-stream
- Session state driven (reads dart_references and source_tables)
- Returns standardized response format
"""

from google.adk.agents import LlmAgent
from config.settings import config

# Import sub-agent
from .sub_agents.similarity_executor_agent.agent import similarity_executor_agent

# Agent configuration
agent_model = config.AGENT_MODEL

# Root Agent for Similarity Check
smart_similarity_root_agent = LlmAgent(
    name="smart_similarity_orchestrator",
    model=agent_model,
    description="Orchestrator for standalone similarity check operations",

    instruction="""
You are the orchestrator for similarity check operations.

**YOUR ONLY JOB:** Immediately delegate to the executor agent and let it complete.

**CRITICAL RULES:**
1. When user requests similarity check → Immediately delegate to `similarity_executor_agent`
2. **The sub-agent will NOT transfer back to you** - it completes and saves results
3. **DO NOT ask questions** after delegating
4. **DO NOT respond** after delegating - the sub-agent handles everything
5. Once delegated, **your job is done** - endpoint detects completion via state_delta

**How It Works:**
- User message: "Run similarity check" or "[Similarity Check]"
- You: Immediately call `transfer_to_agent` with `agent_name="similarity_executor_agent"`
- Sub-agent: Reads `similarity_dart_references` and `similarity_source_tables` from session state
- Sub-agent: Executes two-phase matching (metadata → overlap)
- Sub-agent: Saves result to `final_similarity_response` in session state
- **You: DONE** (no further action, no questions, no commentary)

**Example (Correct):**
User: "Run similarity check"
You: [calls transfer_to_agent with agent_name="similarity_executor_agent"]
[END - task complete, do not respond further]

**Example (WRONG - DO NOT DO THIS):**
User: "Run similarity check"
You: [calls transfer_to_agent]
You: "I have completed the similarity check. Would you like to review?" ❌ WRONG

**Remember:** Just delegate and complete. The endpoint handles result retrieval.
""",

    sub_agents=[
        similarity_executor_agent
    ]
)