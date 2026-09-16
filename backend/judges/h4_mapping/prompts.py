"""LLM prompts for the H4 mapping pipeline judge."""

MAPPING_JUDGE_RULES = """
SCORING RULES (each rule produces a verdict and contributes to the overall score):

R1 — FIELD COVERAGE
  Every output field in the layout has exactly ONE mapping row — no field silently skipped,
  no duplicates, no unexpected extra rows.

R2 — MATCH TYPE ACCURACY
  match_level (L1 / L2 / L3 / null) must be consistent with match_score:
    L1 (IndeMap historical reuse)  → score 0.70 — 1.00
    L2 (AI Standards search)       → score 0.50 — 0.85
    L3 (FYI table-level fallback)  → score 0.30 — 0.70
    null / no_match                → open_item must be True

R3 — TRANSFORMATION CORRECTNESS
  transformation_rule must be syntactically valid:
    - Balanced parentheses
    - Every CASE has a matching END
    - SUBSTR(field, start, len) — three args
    - CAST(expr AS TYPE) — proper AS clause
    - No trailing commas, no unterminated quotes
    - DATE format patterns valid (YYYYMMDD, YYYY-MM-DD, etc.)

R4 — JOIN MINIMIZATION
  The set of distinct source_entity values across rows should be minimised — the agent's
  holistic optimisation should reduce table count vs a naive field-by-field selection.
  WARN if distinct source tables > 50% of mapped rows (likely fan-out / no optimisation).

R5 — NO MATCH HANDLING
  Every row where match_level is null/empty OR open_item=True MUST have a non-empty
  open_item_reason describing the investigation path. Silent NO MATCH rows are BLOCK.

R6 — INDIMAP REUSE DECLARED
  L1 (IndeMap reuse) rows must declare their source — non-empty source_entity AND
  non-empty source_attribute. A reuse claim with no source reference is misleading.

R7 — TRANSFORMATION vs DRIVER SEPARATION
  - transformation_rule must NOT contain WHERE clauses or filter predicates
    (those belong in the driver layer's common_filter).
  - common_filter (driver SQL WHERE) must NOT contain transformation expressions
    (CASE WHEN, COALESCE, CAST, SUBSTR, UPPER, LOWER, TRIM, FORMAT, IIF, NVL, DECODE).
"""

MAPPING_JUDGE_PROMPT = """
You are an LLM judge evaluating a Mapping Generation output for a healthcare data extract
system (BSA DATAMAP AI).

The mapping_row_agent processes each REQUIRED layout field through an L1→L2→L3 waterfall:
  L1 = IndeMap historical mapping reuse (BQ vector search)
  L2 = AI Data Delivery Standards (AnswerQuery)
  L3 = FYI_TBL_COLS table-level fallback

The output is a `mapping_result.json` containing:
  - common_rules (from BRD common_rules tab)
  - transformation_rules.target_entity / driver_table_required / history_data_pull
  - transformation_rules.common_filter (the SQL WHERE clause from the driver layer)
  - transformation_rules.rows[] — one entry per layout field with source/transformation logic.

You must score the mapping output against the seven rules below.

{rules}

=== BRD CONTEXT ===
in_scope:           {in_scope}
out_of_scope:       {out_of_scope}
requirements:       {requirements}
common_rules:       {common_rules_json}
file_attributes:    {file_attributes_json}

=== DRIVER CONTEXT (for separation check R7) ===
common_filter (driver SQL WHERE):
{common_filter}

driver_predicates (fields used by the driver):
{driver_predicates_json}

=== LAYOUT (source of truth for R1) ===
layout_columns ({layout_count}):
{layout_columns_json}

=== MAPPING OUTPUT ===
common_rules ({common_rules_count} rows):
{mapping_common_rules_json}

transformation_rules header:
  target_entity:         {target_entity}
  driver_table_required: {driver_table_required}
  history_data_pull:     {history_data_pull}
  common_filter:         (see above)

transformation_rules.rows (first 30 of {row_count}):
{rows_json}

=== DETERMINISTIC PRE-CHECK RESULTS ===
{deterministic_findings}

=== YOUR TASK ===
Evaluate the mapping output against R1–R7. For each finding, cite the specific
target_attribute (and column where relevant) and the rule it violates.

Respond with ONLY a valid JSON object (no markdown, no preamble):
{{
  "verdict": "PASS" | "WARN" | "BLOCK",
  "score": <0.0-1.0>,
  "summary": "<one sentence describing overall mapping quality>",
  "findings": ["<R<n>: target_attribute='...' — specific issue>", ...],
  "recommendations": ["<precise fix instruction>", ...]
}}

BLOCK if: layout fields uncovered/duplicated, NO MATCH without open_item_reason,
          transformations contain WHERE clauses, driver SQL contains transformations.
WARN if:  match_score outside the band for declared match_level, syntactically suspect
          transformation_rule, source-table fan-out, missing source_entity on L1 rows.
PASS if:  every layout field mapped exactly once, match bands respected, transformations
          syntactically clean, NO MATCH items documented, driver/mapping concerns separated.
"""

MAPPING_OVERALL_SUMMARY_PROMPT = """
You are summarizing a mapping generation quality review for a BSA (Business Systems Analyst)
who will decide whether to approve the mapping output for healthcare data extract processing.

Write in plain business language — no SQL or technical jargon.

REVIEW RESULT:
  Mapping evaluation: {step_verdict} (score {step_score:.2f})
  Overall: {overall_verdict} (score {overall_score:.2f})
  Ready for BSA review: {can_proceed}

KEY FINDINGS (top issues):
{all_findings}

Write 2-3 sentences for the BSA followed by a single actionable note.
Respond with ONLY:
{{"summary": "<2-3 plain-English sentences>", "bsa_note": "<one concrete action for the BSA>"}}
"""
