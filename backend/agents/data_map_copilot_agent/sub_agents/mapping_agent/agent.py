from datetime import datetime
from google.adk.agents import Agent


def do_mapping(ticker: str) -> dict:
    """Retrieves current stock price and saves to session state."""
    
    pass


# Create the root agent
mapping_agent = Agent(
    name="mapping_agent",
    model="gemini-2.5-flash",
    description="An agent that can perform data mapping tasks.",
    instruction="""
    You are a helpful data mapping assistant that helps users map their data to the appropriate formats.
    
    When asked about data mapping:
    1. Use the do_mapping function to perform the mapping for the requested data
    """,
    tools=[do_mapping],
)




