from google.adk.agents import LlmAgent
from .prompts import QUERY_REVIEW_REWRITE_INSTRUCTION_STR

# LLM Agent for review of the SQL queries and rewriting the sql queries if needed
query_review_rewrite_agent = LlmAgent(
    name = "query_review_agent",
    model = "gemini-2.5-flash",
    description = f"This agent is responsible for reviewing queries in the bigquery",
    instruction = QUERY_REVIEW_REWRITE_INSTRUCTION_STR,
    output_key = "query_review_rewrite_output"
)