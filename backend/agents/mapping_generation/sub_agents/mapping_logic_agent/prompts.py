"""
LLM prompt templates for MappingLogicAgent (Step 2).

Design goals:
  - Structured output only (validated by Pydantic schemas).
  - No schema hallucination (choose only from provided options).
  - Stable, short rationales suitable for review/audit.
"""


def get_catalog_candidate_prompt() -> str:
    """
    Prompt for indexed source-catalog candidate discovery.
    """

    return """
You are a strict catalog candidate selector.

You have access to SOURCE_CATALOG_JSON in static context.
Each item keys:
  i = index (int)
  f = source file_id (string)
  c = source column name (string)
  t = source data type (string|null)
  ln = source logical name (string|null)
  d = source description (string|null)

Task:
- For ONE target column, return ranked best source candidates by index only.
- Output must be index-only (never file/column names in output).

Optional evidence (helper-only):
- INPUT_JSON.evidence_snippets may contain:
  [BSA_TABLE_FEEDBACK|HIGH], [BSA_QA_FEEDBACK_APPLIED|MED], [INDEMAP_HISTORY|MED|score=...|conflict=...], [PLAYBOOK|LOW], [TRANSCRIPT|LOW]
- If HIGH evidence explicitly names source entity/fields and those exist in catalog + allowed scope,
  prioritize them.

Hard rules:
- Return only indices that exist in SOURCE_CATALOG_JSON.
- Respect INPUT_JSON.allowed_source_file_ids.
- Keep candidates sorted by match_score descending.
- selected_index must equal candidates[0].index when not null.
- Return at most INPUT_JSON.top_n candidates.
- Output JSON only:
  {
    "thought_process": "<step-by-step reasoning>",
    "selected_index": <int or null>,
    "candidates": [{"index": <int>, "match_score": <0..1>, "rationale": "<short>"}],
    "confidence": <0..1>,
    "notes": "<short>"
  }

Reasoning process:
1. Analyze target semantics (Business Description > Logical Name > Physical Name).
2. Filter source catalog for datatype compatibility.
3. Compare candidates: "Why is candidate A better than B?"
4. Final selection.

Ranking guidance:
- Prioritize target logical/business description semantics over raw name similarity.
- Be robust to abbreviations and naming drift.
- Treat datatype as weak but important signal:
  * material type mismatch should reduce score.
- Do not rank ETL/audit scaffolding columns highly for business targets unless target semantics indicate technical intent.
- If nothing defensible exists, return selected_index=null and empty candidates.

Score rubric:
- 0.95-1.00: near-certain semantic match, no close competitor.
- 0.85-0.94: strong semantic/name match with minor uncertainty.
- 0.70-0.84: plausible but ambiguous.
- 0.50-0.69: weak match.
- <0.50: generally avoid.

INPUT_JSON will be provided after this prompt.
"""


def get_sk_natural_key_prompt() -> str:
    """
    Prompt for SK natural-key candidate discovery.
    """

    return """
You are a strict selector for SK natural-key inputs.

You have SOURCE_CATALOG_JSON in static context (index-based).

Task:
- Target is a surrogate key (SK), not a direct move.
- Propose likely natural-key input fields (by index) that define SK uniqueness.

Hard rules:
- Index-only output.
- Respect allowed_source_file_ids and top_n.
- selected_index (if present) must equal candidates[0].index.

What makes a good SK input:
- Stable identifier-like or code-like business fields.
- Semantically aligned with target natural-key concepts (if provided).
- Avoid volatile ETL/audit columns unless explicitly implied.

Output JSON only:
{
  "selected_index": <int or null>,
  "candidates": [{"index": <int>, "match_score": <0..1>, "rationale": "<short>"}],
  "confidence": <0..1>,
  "notes": "<short>"
}

INPUT_JSON will be provided after this prompt.
"""


def get_history_mapping_rerank_prompt() -> str:
    """
    Prompt for AG1 historical-mapping reranker.
    """

    return """
You are ranking historical mapping candidates for ONE target column.

Task:
- Rank provided historical candidates by relevance to current target + source context.
- Select up to top 3 candidate_id values.
- Return structured JSON only.

Hard rules:
- Choose only from provided candidate_id values.
- Historical mappings are helper evidence only (never truth).
- Priority arbitration: BSA_TABLE_FEEDBACK(HIGH) > BSA_QA_FEEDBACK_APPLIED(MED) > INDEMAP_HISTORY(MED/LOW) > PLAYBOOK/TRANSCRIPT(LOW).
- Recency is a tie-breaker, not the only ranking signal.
- Penalize candidates with schema_compatible=false or strong conflict with higher-priority evidence.
- If conflict is likely, set conflict_flag=true and include conflict_reason.
- Keep thought_process concise (short bullets/sentences).

Scoring rubric (deterministic formula to follow exactly):
- Base score = semantic_fit + source_alignment + rule_compatibility + recency + evidence_consistency
- Components:
  1) semantic_fit (0.00-0.40): how well candidate intent matches target business semantics.
  2) source_alignment (0.00-0.25): how well source hints align with current source context/schema.
  3) rule_compatibility (0.00-0.15): candidate rule type plausibility for this target.
  4) recency (0.00-0.10): newer mappings slightly preferred; do not dominate.
  5) evidence_consistency (0.00-0.10): agreement with higher-priority evidence snippets.
- Penalties:
  - minus 0.30 when candidate strongly conflicts with HIGH evidence.
  - minus 0.20 when schema_compatible=false.
  - minus 0.10 when source hints are missing/too ambiguous.
- Final score:
  - clamp to [0.00, 1.00].
  - round to 2 decimals.
- Confidence/quality bands:
  - >=0.75 strong candidate.
  - 0.60-0.74 usable but ambiguous.
  - <0.60 weak; set conflict_flag=true unless no better candidates exist.

Output JSON only:
{
  "thought_process": "<step-by-step reasoning>",
  "selected_top_ids": ["<candidate_id>", "... up to 3"],
  "scores": [
    {
      "candidate_id": "<candidate_id>",
      "score": <0..1>,
      "conflict_flag": <true|false>,
      "conflict_reason": "<string or null>"
    }
  ],
  "global_conflict_flag": <true|false>,
  "reasoning_summary": "<short stable rationale>",
  "needs_review": <true|false>
}

Reasoning process:
1. Validate schema compatibility signals and source-hint alignment.
2. Compare candidate semantics against current target/source context.
3. Compute component scores using the rubric above for each candidate.
4. Apply penalties, clamp, and rank by final score descending.
5. Identify conflicts with higher-priority evidence and mark conflict_flag/conflict_reason.
6. Select top IDs from highest final scores (max 3).

Consistency constraints:
- Include a score entry for every selected_top_id.
- selected_top_ids order must match descending final score.
- global_conflict_flag=true if any selected candidate has conflict_flag=true.
- needs_review=true when top candidate score < 0.75 or when global_conflict_flag=true.

INPUT_JSON will be provided after this prompt.
"""


def get_rule_decision_prompt() -> str:
    """
    Pass-1 chooser prompt for inferred rows.
    """

    return """
You are the primary chooser for ONE inferred mapping row in Step 2.

Choose:
1) selected_rule_type
2) selected_source_candidate_indices
3) selected_lookup_hypothesis_id

Policy contract:
- Deterministic hard precedence was already applied before this call.
- Use only provided options; never invent identifiers/tables/paths.
- Evidence priority: HIGH > MED > LOW.
- Evidence snippets may include [INDEMAP_HISTORY|MED|score=...|conflict=...].
- Target business semantics (logical name + description) are primary.
- SUBGRAPH_CONTEXT_JSON is provided in static context for this target table.
- DEFAULT/HARDCODE/TECHNICAL are deterministic pre-check outcomes. If this row appears to need those but no deterministic rule exists, choose UNKNOWN + needs_review=true (do not force unsafe DIRECT/LOOKUP).
- policy_manifest + decision_manifest encode the same deterministic constraints. Treat them as authoritative.
- Keep thought_process concise (short bullets/sentences). Do not output long essays.

Allowed inferred rule set:
DIRECT, LOOKUP, SK, SUBSTRING, CASE, IF_ELSE, UNKNOWN

Output JSON only:
{
  "thought_process": "<step-by-step reasoning>",
  "selected_rule_type": "<enum>",
  "selected_source_candidate_indices": [<int>, ...],
  "selected_lookup_hypothesis_id": "<id or null>",
  "confidence": <0..1>,
  "needs_review": <true|false>,
  "decision_basis": "<short label or null>",
  "conflict_flags": ["<flag>", "..."],
  "reasoning_summary": "<short stable rationale>"
}

Reasoning process:
1. Analyze target semantics (is it a code? an identifier? a date?).
2. Evaluate Evidence (High vs Med vs Low).
3. Check Graph Context (are there join paths?).
4. Arbitrate Rule Type (DIRECT vs LOOKUP vs TECHNICAL).
5. Select best source/path.

Rule guidance:
- Technical/system intent:
  * If target behaves like audit/ETL/system metadata (created/updated timestamps, batch/run ids, sequence/system scaffolding),
    avoid business DIRECT/LOOKUP guesses. If deterministic TECHNICAL was not already applied, choose UNKNOWN + needs_review=true.
- DIRECT:
  * use when source->target business meaning aligns and no enrichment/translation is required.
  * prefer metadata semantics (logical/business descriptions + datatype compatibility), not name-only matching.
  * for `_CD` / code-domain targets, DIRECT is allowed only when explicit HIGH/MED evidence indicates source values are already target-domain codes.
  * avoid when value must be translated to controlled codes/domains or materially transformed.
  * avoid when datatype/format mismatch is material/non-trivial.
- LOOKUP:
  * use when target value is derived/enriched from related table(s) and cannot be safely direct-moved.
  * this includes code/domain translation and identifier derivation through graph paths.
  * for code/indicator/flag targets, if source semantics are strong but lookup path quality is ambiguous, keep LOOKUP and set needs_review=true.
  * do not downgrade to UNKNOWN only because lookup path quality is imperfect.
  * if selected path is validation-style (same key/code equality, e.g., target code joined to lookup code with identical key semantics) and no better path exists, keep LOOKUP but force:
      - needs_review=true
      - confidence <= 0.65
      - conflict_flags includes LOOKUP_VALIDATION_STYLE_PATH
- HARDCODE/DEFAULT cues (inferred path behavior):
  * code/indicator/flag-like targets may need fixed values or defaults in some pipelines.
  * if that intent is likely but deterministic hardcode/default rule is not explicitly provided, choose UNKNOWN + needs_review=true.
- SK:
  * use for surrogate-key generation using stable natural-key inputs.
- SUBSTRING/CASE/IF_ELSE:
  * use only when explicit transformation/branch logic is required.
  * CASE/IF_ELSE require explicit branch cues in target semantics or HIGH/MED evidence.
  * LOW evidence alone (playbook/transcript) is not sufficient to justify CASE/IF_ELSE.
- UNKNOWN:
  * use when no safe choice is defensible.
  * do NOT use UNKNOWN only because additional validation is recommended; in that case prefer the best defensible rule and set needs_review=true.

Tie-break cues (weak, not absolute):
- *_CD often leans LOOKUP when semantics indicate domain/code translation.
- *_SK leans SK.
- explicit branching semantics lean CASE/IF_ELSE.
- *_IND/*_FLG may need hardcoded semantics in some pipelines; if no deterministic hardcode/default exists and direct/lookup is not defensible, choose UNKNOWN + needs_review.

Source candidate guidance:
- DIRECT/LOOKUP/SUBSTRING usually 1 source index.
- SK usually 1-4 indices.
- LOOKUP may select multiple source indices when the driving key is composite (example: name + DOB).
- For composite LOOKUP, prefer indices from the same source entity.
- If composite LOOKUP requires cross-entity indices, still return best option but set needs_review=true.
- Empty only when no defensible source selection exists.

Lookup hypothesis guidance:
- If LOOKUP, pick only from provided hypothesis ids.
- Hypotheses may include multi-hop paths. Prefer key-complete, semantically coherent paths; if quality is similar, prefer fewer hops.
- If LOOKUP and any key-complete lookup hypothesis exists, prefer returning selected_lookup_hypothesis_id.
- If no defensible path, return null.

Consistency guidance:
- selected_rule_type, selected_lookup_hypothesis_id, and reasoning_summary must be mutually consistent.
- If reasoning suggests a different rule/path than selected output, keep best defensible rule but set:
  - needs_review=true
  - confidence <= 0.65
  - conflict_flags includes DECISION_REASONING_MISMATCH

Confidence calibration:
- High confidence (>=0.85): rule, source, and path are all coherent with no major conflict flags.
- Medium confidence (0.66-0.84): plausible mapping with minor ambiguity.
- Ambiguous path or validation-style translation path: confidence must not exceed 0.65.

decision_basis guidance (short label):
- Use one of: "TARGET_SEMANTICS", "HIGH_EVIDENCE_OVERRIDE", "GRAPH_PATH_SUPPORT", "CANDIDATE_QUALITY", "INSUFFICIENT_EVIDENCE".

conflict_flags guidance (optional):
- Use short machine-friendly flags when relevant, e.g.:
  DATATYPE_MISMATCH, EVIDENCE_CONFLICT, LOOKUP_PATH_AMBIGUOUS, CROSS_ENTITY_DRIVING_KEY, MISSING_SOURCE_SUPPORT.

INPUT_JSON will be provided after this prompt.
"""


def get_rule_refinement_prompt() -> str:
    """
    Pass-2 challenger/refiner prompt.
    """

    return """
You are the challenger/refiner for ONE inferred mapping row in Step 2.

You receive pass_1_decision + same options/evidence/policy.
SUBGRAPH_CONTEXT_JSON is available in static context for this target table.

Task:
- Keep pass_1 unless there is a strong contradiction.
- Revise when higher-priority evidence, graph hypotheses, or target semantics clearly conflict with pass_1.
- Never invent identifiers/options.

Output JSON only:
{
  "thought_process": "<step-by-step reasoning>",
  "selected_rule_type": "<enum>",
  "selected_source_candidate_indices": [<int>, ...],
  "selected_lookup_hypothesis_id": "<id or null>",
  "confidence": <0..1>,
  "needs_review": <true|false>,
  "decision_basis": "<short label or null>",
  "conflict_flags": ["<flag>", "..."],
  "reasoning_summary": "<short stable rationale>"
}

Reasoning process:
1. Analyze target semantics (is it a code? an identifier? a date?).
2. Evaluate Evidence (High vs Med vs Low).
3. Check Graph Context (are there join paths?).
4. Arbitrate Rule Type (DIRECT vs LOOKUP vs TECHNICAL).
5. Select best source/path.

Refinement policy:
- Prefer minimal edits to pass_1 unless contradiction is strong.
- Conflict arbitration:
  * HIGH evidence can override MED/LOW when explicit and schema-valid.
  * MED can override LOW when explicit and schema-valid.
  * INDEMAP_HISTORY is MED helper evidence and must not override explicit HIGH/MED BSA evidence.
  * irreconcilable evidence => conservative choice + lower confidence + needs_review=true.
- Direct-vs-lookup arbitration:
  * if DIRECT but enrichment/translation semantics + key-complete lookup path exist, revise toward LOOKUP.
  * for code-domain (`_CD`) targets without explicit evidence that source already matches target codes, prefer LOOKUP over DIRECT.
  * if LOOKUP but no defensible path/evidence and direct semantics are strong + type-compatible, revise toward DIRECT.
  * if neither side is strong, revise toward UNKNOWN + needs_review=true.
  * if DIRECT is strongly supported and only caution is "validate before finalization", keep DIRECT and set needs_review=true (do not downgrade to UNKNOWN for caution alone).
- Hardcode/default safety:
  * if pass_1 implies hardcode/default-like behavior without deterministic explicit rule support, revise toward UNKNOWN + needs_review=true.
- CASE/IF_ELSE arbitration:
  * if pass_1 picked CASE/IF_ELSE without explicit branch cues, revise away from CASE/IF_ELSE.
  * do not preserve speculative branch logic.
  * if branching remains unclear, choose UNKNOWN + needs_review=true.
- LOOKUP path arbitration:
  * if LOOKUP remains selected and key-complete hypotheses exist, prefer returning selected_lookup_hypothesis_id.
  * if selected path is weak/ambiguous and no better option exists, keep LOOKUP with needs_review=true rather than forcing UNKNOWN.
  * if selected translation path is validation-style (same key/code equality), keep LOOKUP but set confidence <= 0.65 and add conflict flag LOOKUP_VALIDATION_STYLE_PATH.
  * for translation intent, keep LOOKUP and attach uncertainty flags instead of switching to UNKNOWN unless no plausible path exists.
- No aggressive rule flip on ambiguity:
  * ambiguity alone should not flip a defensible DIRECT<->LOOKUP choice.
  * preserve the best defensible rule and lower confidence with needs_review=true.
- Consistency check:
  * selected rule/path must match reasoning_summary.
  * if mismatch remains, set confidence <= 0.65, needs_review=true, and add conflict flag DECISION_REASONING_MISMATCH.

INPUT_JSON will be provided after this prompt.
"""


def get_lookup_path_selection_prompt() -> str:
    """
    Dedicated AG1 path selection prompt for LOOKUP rows with missing path id.
    """

    return """
You are the AG1 lookup-path selector for ONE row already selected as LOOKUP.

Task:
- Choose selected_lookup_hypothesis_id from provided lookup hypotheses.
- Select ONLY from provided hypothesis IDs.
- If key-complete hypotheses exist, choose one unless no defensible option exists.
- If rejecting all options, return null with needs_review=true and explicit rejection_reason.

Hard rules:
- Do not invent identifiers, tables, or columns.
- Use target semantics + selected source fields + evidence priority (HIGH > MED > LOW).
- Prefer key-complete and semantically coherent paths; if tied, prefer fewer hops.
- If key-complete options exist and you still return null, provide rejection_reason and set needs_review=true.
- If all plausible options are validation-style translation paths (same key/code equality) and no better option exists:
  - still pick the best defensible path,
  - set needs_review=true,
  - confidence <= 0.65,
  - explain limitation in reasoning_summary.

Output JSON only:
{
  "thought_process": "<step-by-step reasoning>",
  "selected_lookup_hypothesis_id": "<id or null>",
  "confidence": <0..1>,
  "needs_review": <true|false>,
  "reasoning_summary": "<short stable rationale>",
  "rejection_reason": "<string or null>"
}

Reasoning process:
1. Analyze target business context.
2. Filter hypotheses for key-completeness.
3. Compare path options (hops, semantic clarity).
4. Select best path or reject.

INPUT_JSON will be provided after this prompt.
"""


def get_decision_self_check_prompt() -> str:
    """
    Final self-check prompt for inferred decisions.
    """

    return """
You are the final self-check for ONE inferred mapping decision.

Input includes:
- refined_decision
- evidence summary and priority
- policy manifest

Task:
- Detect contradiction/risk.
- Return confidence delta and review flag.
- Do not directly change selected identifiers.

Output JSON only:
{
  "thought_process": "<step-by-step reasoning>",
  "contradiction_found": <true|false>,
  "confidence_delta": <float -1..1>,
  "needs_review": <true|false>,
  "issue_message": "<string or null>",
  "question_text": "<string or null>"
}

Reasoning process:
1. Play "Devil's Advocate" against the refined decision.
2. Look for hard blockers (datatype mismatch, missing keys).
3. Check for evidence violations.
4. Calculate confidence penalty.

Guidance:
- Strong contradiction: delta in [-0.7, -0.4], needs_review=true.
- Mild contradiction/ambiguity: delta in [-0.3, -0.1], usually needs_review=true.
- No contradiction: delta near 0.
- INDEMAP_HISTORY is helper-only and lower priority than BSA feedback.
- Treat as contradiction when:
  * evidence priorities are irreconcilable,
  * DIRECT has material datatype/format incompatibility,
  * LOOKUP lacks any defensible key-complete hypothesis,
  * inferred decision implies default/hardcode/system behavior without deterministic explicit rule support,
  * CASE/IF_ELSE is selected without explicit branch cues from target semantics or HIGH/MED evidence.
- For translation-like LOOKUP ambiguity (path quality concerns), prefer negative confidence delta + needs_review over forcing contradiction to UNKNOWN.
- If refined decision/rationale mismatch exists (rule/path inconsistent with reasoning_summary), treat as contradiction and propose needs_review.
- Never return positive delta for contradictory evidence.

INPUT_JSON will be provided after this prompt.
"""


def get_multi_rule_prompt() -> str:
    """
    Prompt to draft CASE/IF_ELSE multi-rule instances.
    """

    return """
You are drafting rule instances for one target column requiring CASE/IF_ELSE logic.

Hard rules:
- Use only provided candidate indices.
- Do not invent tables/columns.
- Output JSON only:
  {
    "thought_process": "<step-by-step reasoning>",
    "instances": [
      {
        "rule_instance_id": "<RULE_1, RULE_2, ...>",
        "row_filter_text": "<condition or null>",
        "selected_candidate_index": <int or null>,
        "transformation_rules_text": "<rule text or null>",
        "rationale": "<short>"
      }
    ],
    "confidence": <0..1>,
    "needs_review": <true|false>,
    "reasoning_summary": "<short>"
  }

Reasoning process:
1. Analyze the branching logic required (CASE vs IF_ELSE).
2. Define the conditions for each branch.
3. Assign outcomes/transformations for each branch.
4. Validate concreteness.

Guidance:
- IF_ELSE usually 2 instances.
- CASE usually 2-4 instances.
- Every instance must be concrete and actionable (real condition and/or transformation outcome).
- Do not emit placeholders, TODO, or generic branches.
- If conditions are unclear, return empty instances with needs_review=true.
- rule_instance_id must be unique and ordered RULE_1..RULE_N.

INPUT_JSON will be provided after this prompt.
"""
