from google.adk.agents import LlmAgent, ParallelAgent
from config.settings import config

from .prompts import (
    BRD_PARSER_INSTRUCTION, LAYOUT_PARSER_INSTRUCTION,
    TRANSCRIPT_INSTRUCTION, DOMAIN_CLASSIFIER_INSTRUCTION,
    AMBIGUITY_DETECTOR_INSTRUCTION,
)
from .tools import (
    structure_parsed_brd, structure_layout_fields,
    distill_transcript_decisions, classify_domains, detect_ambiguities,
)

MODEL = "gemini-2.5-flash"

brd_parser_agent = LlmAgent(
    name="brd_parser_agent",
    model=MODEL,
    instruction=BRD_PARSER_INSTRUCTION,
    tools=[structure_parsed_brd],
    output_key="parsed_brd",
)

layout_parser_agent = LlmAgent(
    name="layout_parser_agent",
    model=MODEL,
    instruction=LAYOUT_PARSER_INSTRUCTION,
    tools=[structure_layout_fields],
    output_key="parsed_layouts",
)

transcript_agent = LlmAgent(
    name="transcript_agent",
    model=MODEL,
    instruction=TRANSCRIPT_INSTRUCTION,
    tools=[distill_transcript_decisions],
    output_key="parsed_transcript",
)

domain_classifier_agent = LlmAgent(
    name="domain_classifier_agent",
    model=MODEL,
    instruction=DOMAIN_CLASSIFIER_INSTRUCTION,
    tools=[classify_domains],
    output_key="domain_tagged_fields",
)

ambiguity_detector_agent = LlmAgent(
    name="ambiguity_detector_agent",
    model=MODEL,
    instruction=AMBIGUITY_DETECTOR_INSTRUCTION,
    tools=[detect_ambiguities],
    output_key="ambiguity_report",
)

# ParallelAgent runs all 4 parsing agents simultaneously
parse_parallel_agent = ParallelAgent(
    name="parse_parallel_agent",
    sub_agents=[
        brd_parser_agent,
        layout_parser_agent,
        transcript_agent,
        domain_classifier_agent,
    ],
)
