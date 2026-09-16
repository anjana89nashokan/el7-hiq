"""
Metadata Layer — ADK agent definitions.

Single LlmAgent that calls the three normalization tools in sequence.
"""

from google.adk.agents import LlmAgent
from google.genai import types as _genai_types

from .prompts import METADATA_NORMALIZER_INSTRUCTION, METADATA_EXTRACTOR_INSTRUCTION
from .tools import normalize_data_types, standardize_field_names, validate_metadata, extract_metadata_template_values

MODEL = "gemini-2.5-flash"

metadata_normalizer_agent = LlmAgent(
    name="metadata_normalizer_agent",
    model=MODEL,
    instruction=METADATA_NORMALIZER_INSTRUCTION,
    tools=[normalize_data_types, standardize_field_names, validate_metadata],
    output_key="normalized_metadata",
    description=(
        "Normalizes data types and field names to enterprise standards. "
        "Validates consistency and flags issues."
    ),
)

# NOTE: This agent emits the metadata as a plain-text JSON object (NOT a tool call).
# Forcing the whole template into a single function-call argument triggers Gemini's
# MALFORMED_FUNCTION_CALL once the file has many attributes (the large argument is
# truncated by the output-token limit). Plain-text JSON uses the full output budget;
# the /extract-metadata handler parses output_key="extracted_metadata" robustly.
metadata_extractor_agent = LlmAgent(
    name="metadata_extractor_agent",
    model=MODEL,
    instruction=METADATA_EXTRACTOR_INSTRUCTION,
    output_key="extracted_metadata",
    generate_content_config=_genai_types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=32768,  # room for many-attribute layouts
        response_mime_type="application/json",
    ),
    description=(
        "Extracts metadata values from BRD and Layout sources "
        "to populate a standardized template."
    ),
)
