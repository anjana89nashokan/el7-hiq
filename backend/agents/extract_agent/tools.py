from google.adk.tools import ToolContext
from .models import (
    ParsedBrd,
    FieldInstruction,
    DateCriteria,
    ParsedLayout,
    LayoutField,
    ParsedTranscript,
    TranscriptDecision,
    DomainTaggedFields,
    TaggedField,
    AmbiguityReport,
    AmbiguityItem,
)
from typing import List, Dict, Any


def structure_parsed_brd(
    tool_context: ToolContext,
    in_scope_items: List[str],
    out_of_scope_items: List[str],
    date_criteria: List[Dict[str, Any]],
    eligibility_criteria: List[str],
    field_level_instructions: List[Dict[str, Any]],
    skipped_tbd_items: List[str],
) -> str:
    """Structure the parsed BRD fields into a typed object and save to state."""
    parsed = ParsedBrd(
        in_scope_items=in_scope_items,
        out_of_scope_items=out_of_scope_items,
        date_criteria=[DateCriteria(**d) for d in date_criteria],
        eligibility_criteria=eligibility_criteria,
        field_level_instructions=[
            FieldInstruction(**f) for f in field_level_instructions
        ],
        skipped_tbd_items=skipped_tbd_items,
    )
    tool_context.state["parsed_brd"] = parsed.model_dump()
    return "BRD structured successfully."


def structure_layout_fields(
    tool_context: ToolContext, layouts: List[Dict[str, Any]]
) -> str:
    """Structure the layout fields into a typed object and save to state."""
    parsed_layouts = []
    for l in layouts:
        # Convert field dictionaries into LayoutField objects
        fields = [LayoutField(**f) for f in l.get("fields", [])]
        parsed = ParsedLayout(
            source_file_name=l.get("source_file_name", "unknown"),
            field_count=l.get("field_count", len(fields)),
            fields=fields,
        )
        parsed_layouts.append(parsed)

    tool_context.state["parsed_layouts"] = [l.model_dump() for l in parsed_layouts]
    return f"Layout parsed: {sum(l.field_count for l in parsed_layouts)} total fields across {len(parsed_layouts)} file(s)."


def distill_transcript_decisions(
    tool_context: ToolContext,
    decisions: List[Dict[str, Any]],
    vendor_context: str | None = None,
    frequency_notes: str | None = None,
) -> str:
    """Categorize and distill transcript decisions into a typed object and save to state."""
    parsed = ParsedTranscript(
        decisions=[TranscriptDecision(**d) for d in decisions],
        vendor_context=vendor_context,
        frequency_notes=frequency_notes,
    )
    tool_context.state["parsed_transcript"] = parsed.model_dump()
    return f"Transcript distilled: {len(parsed.decisions)} confirmed decisions."


def classify_domains(
    tool_context: ToolContext,
    tagged_fields: List[Dict[str, Any]],
    domain_summary: Dict[str, int],
    primary_domain: str,
) -> str:
    """Classify data domains and save the domain tagging report to state."""
    parsed = DomainTaggedFields(
        tagged_fields=[TaggedField(**t) for t in tagged_fields],
        domain_summary=domain_summary,
        primary_domain=primary_domain,
    )
    tool_context.state["domain_tagged_fields"] = parsed.model_dump()
    return f"Domain classification complete. Primary domain: {primary_domain}."


def detect_ambiguities(
    tool_context: ToolContext,
    ambiguities: List[Dict[str, Any]],
    fields_in_layout_not_brd: List[str],
    fields_in_brd_not_layout: List[str],
) -> str:
    """Detect conflicts and missing items to generate an ambiguity report and save to state."""
    parsed_ambiguities = [AmbiguityItem(**a) for a in ambiguities]

    total_conflicts = sum(1 for a in parsed_ambiguities if a.item_type == "conflict")
    total_missing = sum(1 for a in parsed_ambiguities if a.item_type == "missing")
    high_count = sum(1 for a in parsed_ambiguities if a.severity == "HIGH")

    can_proceed = high_count == 0

    report = AmbiguityReport(
        ambiguities=parsed_ambiguities,
        total_conflicts=total_conflicts,
        total_missing=total_missing,
        fields_in_layout_not_brd=fields_in_layout_not_brd,
        fields_in_brd_not_layout=fields_in_brd_not_layout,
        can_proceed=can_proceed,
    )
    tool_context.state["ambiguity_report"] = report.model_dump()
    return f"Ambiguity detection complete. can_proceed={can_proceed}. HIGH items: {high_count}."
