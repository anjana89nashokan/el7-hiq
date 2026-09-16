from __future__ import annotations

import json
from typing import Any

from judges.h5_metadata.naming_checker import (
    NamingViolationType,
    analyze_cast_safety,
    check_all_attribute_names,
    check_data_type_validity,
    check_duplicate_attribute_names,
    check_file_name,
    check_position_sequence,
    compute_naming_conformance_score,
)
from judges.h5_metadata.prompts import (
    MEANING_PRESERVATION_PROMPT,
    TRANSFORMATION_FIDELITY_PROMPT,
    TYPE_COHERENCE_PROMPT,
)
from judges.h5_metadata.schema_validator import (
    compute_template_completeness_score,
    validate_indimap_template,
)
from judges.h5_metadata.schemas import JudgeInputH5
from models.judge import RuleScore, RuleVerdict

RULE_WEIGHT = 1.0 / 6.0


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


# ---------------------------------------------------------------------------
# R3 — Template Schema Validity (deterministic)
# ---------------------------------------------------------------------------
async def rule_r3_template_schema_validity(
    input: JudgeInputH5, llm_call, deterministic_analysis: dict | None = None
) -> RuleScore:
    template_str = input.metadata_output.indimap_template_json or ""

    parsed_template: Any = None
    json_parse_error: str | None = None
    try:
        parsed_template = json.loads(template_str) if template_str else None
        if parsed_template is None:
            json_parse_error = "indimap_template_json is empty."
    except json.JSONDecodeError as exc:
        json_parse_error = f"JSON parse error at position {exc.pos}: {exc.msg}"

    if json_parse_error:
        return RuleScore(
            rule_id="R3_TEMPLATE_SCHEMA",
            rule_name="Template Schema Validity",
            verdict=RuleVerdict.FAIL,
            score=0.0,
            weight=RULE_WEIGHT,
            evidence=f"INVALID JSON: {json_parse_error}",
            citations=[],
            blocking=True,
            recommendations=[
                "Template Export agent produced invalid JSON. Re-run Template Export — do not attempt manual JSON repair."
            ],
        )

    result = validate_indimap_template(parsed_template if isinstance(parsed_template, dict) else {})
    attributes_dump = [a.model_dump() for a in input.metadata_output.attributes]

    duplicates = check_duplicate_attribute_names(attributes_dump)
    position_issues = check_position_sequence(attributes_dump)

    template_attribute_count = len(parsed_template.get("attributes") or []) if isinstance(parsed_template, dict) else 0
    count_mismatch = template_attribute_count != len(input.metadata_output.attributes)

    block_count = result.block_count + len(duplicates) + len(position_issues)
    if count_mismatch:
        block_count += 1

    total_validations = max(result.file_fields_validated + result.attributes_validated, 1)
    schema_score = (total_validations - result.block_count) / total_validations
    schema_score -= len(position_issues) * 0.05
    schema_score -= len(duplicates) * 0.10
    if count_mismatch:
        schema_score -= 0.15
    schema_score = max(0.0, schema_score)

    recommendations: list[str] = []
    citations: list[str] = []
    for error in result.errors[:25]:
        recommendations.append(f"{error.path}: {error.description}")
        citations.append(error.path)
    for dup in duplicates:
        recommendations.append(
            f"Duplicate attribute name '{dup}' — rename one to ensure uniqueness."
        )
    for issue in position_issues:
        recommendations.append(issue.get("description", ""))
    if count_mismatch:
        recommendations.append(
            f"Template lists {template_attribute_count} attributes but metadata_output has "
            f"{len(input.metadata_output.attributes)}. Re-export the template."
        )

    if block_count > 0:
        verdict = RuleVerdict.FAIL
    elif result.warn_count > 0:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Schema validation: {result.block_count} blocking errors, {result.warn_count} warnings. "
        f"File fields: {result.file_fields_validated}. Attributes validated: {result.attributes_validated}. "
        f"Position issues: {len(position_issues)}. Duplicates: {len(duplicates)}. "
        f"Template/attribute count match: {not count_mismatch}."
    )

    return RuleScore(
        rule_id="R3_TEMPLATE_SCHEMA",
        rule_name="Template Schema Validity",
        verdict=verdict,
        score=round(schema_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations[:10],
        blocking=block_count > 0,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R4 — Completeness (deterministic)
# ---------------------------------------------------------------------------
async def rule_r4_completeness(
    input: JudgeInputH5, llm_call, deterministic_analysis: dict | None = None
) -> RuleScore:
    attributes = input.metadata_output.attributes
    layout_fields = input.original_layout_fields or []

    layout_field_names = {
        str(f.get("field_name") or f.get("name") or "").upper()
        for f in layout_fields
        if (f.get("field_name") or f.get("name"))
    }
    layout_field_names.discard("")
    metadata_field_names = {a.name.upper() for a in attributes if a.name}

    missing_from_metadata = layout_field_names - metadata_field_names
    extra_in_metadata = metadata_field_names - layout_field_names

    h4_field_names = {
        str(f.get("field_name") or "").upper() for f in input.h4_mapping_spec.fields
    }
    h4_field_names.discard("")

    orphan_fields: list[str] = []
    derived_extras: list[str] = []
    for attribute in attributes:
        upper_name = attribute.name.upper()
        if upper_name in h4_field_names:
            continue
        if attribute.is_derived:
            derived_extras.append(attribute.name)
        else:
            orphan_fields.append(attribute.name)

    no_match_handling_errors: list[str] = []
    for field_name in input.h4_mapping_spec.no_match_fields or []:
        attribute = next((a for a in attributes if a.name.upper() == field_name.upper()), None)
        if attribute is None:
            continue
        if (
            attribute.source_table != "PENDING_BSA_CLARIFICATION"
            or attribute.source_column != "PENDING_BSA_CLARIFICATION"
            or attribute.match_type != "no_match"
        ):
            no_match_handling_errors.append(field_name)

    unimplemented_overrides: list[str] = []
    for field_name, override in (input.h4_mapping_spec.bsa_h4_overrides or {}).items():
        attribute = next((a for a in attributes if a.name.upper() == field_name.upper()), None)
        if attribute is None:
            unimplemented_overrides.append(field_name)
            continue
        if isinstance(override, dict):
            if (
                override.get("source_table")
                and attribute.source_table != override.get("source_table")
            ):
                unimplemented_overrides.append(field_name)
                continue
            if (
                override.get("source_column")
                and attribute.source_column != override.get("source_column")
            ):
                unimplemented_overrides.append(field_name)
                continue
            if override.get("transformation") and attribute.transformation != override.get(
                "transformation"
            ):
                unimplemented_overrides.append(field_name)

    template_completeness = 0.0
    try:
        parsed_template = json.loads(input.metadata_output.indimap_template_json or "{}")
        template_completeness = compute_template_completeness_score(parsed_template)
    except Exception:
        template_completeness = 0.0

    layout_coverage = 1.0 - (len(missing_from_metadata) / max(len(layout_field_names), 1))
    h4_coverage = max(0.0, 1.0 - len(orphan_fields) * 0.05)
    override_score = max(0.0, 1.0 - len(unimplemented_overrides) * 0.20)

    overall = (
        0.35 * template_completeness
        + 0.40 * layout_coverage
        + 0.15 * h4_coverage
        + 0.10 * override_score
    )

    blocking = bool(missing_from_metadata) or bool(no_match_handling_errors) or bool(unimplemented_overrides)

    recommendations: list[str] = []
    for missing in sorted(missing_from_metadata)[:25]:
        recommendations.append(
            f"Field '{missing}' from the original layout has no metadata attribute — restore or document the rename."
        )
    for field_name in no_match_handling_errors[:25]:
        recommendations.append(
            f"Field '{field_name}' was NO MATCH at H4 but has been promoted to a mapped state — revert to NO MATCH."
        )
    for override in unimplemented_overrides[:25]:
        recommendations.append(
            f"BSA H4 override for '{override}' is not reflected in the metadata — implement the BSA-specified mapping."
        )
    if extra_in_metadata and not all(a.is_derived for a in attributes if a.name.upper() in extra_in_metadata):
        recommendations.append(
            f"Metadata contains {len(extra_in_metadata)} fields not in the original layout — confirm they are intentional derivations."
        )
    for orphan in orphan_fields[:10]:
        recommendations.append(f"Field '{orphan}' has no H4 mapping — verify provenance.")

    if blocking or overall < 0.85:
        verdict = RuleVerdict.FAIL
    elif extra_in_metadata or orphan_fields or overall < 0.95:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Layout fields: {len(layout_field_names)}. Metadata attributes: {len(metadata_field_names)}. "
        f"Missing from metadata: {len(missing_from_metadata)}. Extra in metadata: {len(extra_in_metadata)}. "
        f"NO MATCH handling errors: {len(no_match_handling_errors)}. "
        f"Unimplemented BSA H4 overrides: {len(unimplemented_overrides)}. "
        f"Template completeness: {template_completeness:.1%}."
    )

    return RuleScore(
        rule_id="R4_COMPLETENESS",
        rule_name="Completeness",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=sorted(missing_from_metadata)[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R1 — Naming Conformance (mostly deterministic)
# ---------------------------------------------------------------------------
async def rule_r1_naming_conformance(
    input: JudgeInputH5, llm_call, deterministic_analysis: dict | None = None
) -> RuleScore:
    file_violations = check_file_name(input.metadata_output.file_metadata.file_name)
    attribute_dumps = [a.model_dump() for a in input.metadata_output.attributes]
    attr_violations = check_all_attribute_names(attribute_dumps)
    all_violations = file_violations + attr_violations

    auto_correctable = [v for v in attr_violations if v.auto_correctable]
    manual_required = [v for v in attr_violations if not v.auto_correctable]

    auto_records = input.metadata_output.naming_auto_corrections or []
    manual_records = input.metadata_output.naming_manual_flags or []

    correction_not_applied = 0
    for record in auto_records:
        corrected = str(record.get("corrected_name") or "").upper()
        if not corrected:
            continue
        if not any(a.name.upper() == corrected for a in input.metadata_output.attributes):
            correction_not_applied += 1

    flagged_paths = {str(r.get("field_path") or "") for r in manual_records}
    unflagged_manual = [v for v in manual_required if v.field_path not in flagged_paths]

    meaning_risk = 0
    for record in auto_records:
        original = str(record.get("original_name") or "")
        corrected = str(record.get("corrected_name") or "")
        if not original or not corrected:
            continue
        if original.upper() == corrected.upper():
            continue
        if original.replace("_", "") == corrected.replace("_", ""):
            continue
        # Significantly different — ask LLM if meaning is preserved
        description = ""
        for attribute in input.metadata_output.attributes:
            if attribute.name.upper() == corrected.upper():
                description = attribute.description
                break
        response = await llm_call(
            MEANING_PRESERVATION_PROMPT.format(
                original_name=original,
                corrected_name=corrected,
                description=description or "(no description)",
            )
        )
        if response.get("meaning_preserved") is False:
            meaning_risk += 1

    total_attributes = max(len(input.metadata_output.attributes), 1)
    block_count = sum(1 for v in all_violations if v.severity == "block")
    conformance_rate = 1.0 - (block_count / max(total_attributes + 1, 1))
    judge_computed = compute_naming_conformance_score(all_violations)
    agent_claimed = float(input.metadata_output.naming_conformance_score or 0.0)
    score_discrepancy = abs(agent_claimed - judge_computed) > 0.10

    overall = judge_computed

    blocking = (block_count > 0 and conformance_rate < 0.85) or correction_not_applied > 0
    if blocking:
        verdict = RuleVerdict.FAIL
    elif judge_computed < 0.95 or unflagged_manual or meaning_risk or score_discrepancy:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    recommendations: list[str] = []
    for violation in all_violations[:25]:
        if violation.suggested_correction:
            recommendations.append(
                f"{violation.field_path}: {violation.description} Suggested: {violation.suggested_correction}"
            )
        else:
            recommendations.append(f"{violation.field_path}: {violation.description}")
    if correction_not_applied:
        recommendations.append(
            f"Naming Standardizer logged {correction_not_applied} correction(s) that were not applied to the attribute list."
        )
    for violation in unflagged_manual[:10]:
        recommendations.append(
            f"Attribute '{violation.field_name}' has manual-only naming violation that was not flagged for BSA review."
        )
    if score_discrepancy:
        recommendations.append(
            f"Naming Standardizer self-reported {agent_claimed:.1%} but judge computed {judge_computed:.1%}."
        )

    evidence = (
        f"File name: {len(file_violations)} violations. "
        f"Attributes: {len(attr_violations)} violations across {total_attributes} fields. "
        f"Auto-correctable: {len(auto_correctable)}. Manual review needed: {len(manual_required)}. "
        f"Corrections not applied: {correction_not_applied}. Meaning risks: {meaning_risk}. "
        f"Judge conformance: {judge_computed:.1%} vs agent {agent_claimed:.1%}."
    )

    return RuleScore(
        rule_id="R1_NAMING_CONFORMANCE",
        rule_name="Naming Conformance",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=[v.field_path for v in all_violations[:10]],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R2 — Type Safety
# ---------------------------------------------------------------------------
async def rule_r2_type_safety(
    input: JudgeInputH5, llm_call, deterministic_analysis: dict | None = None
) -> RuleScore:
    attributes = input.metadata_output.attributes
    invalid_types: list[dict] = []
    for attribute in attributes:
        if not check_data_type_validity(attribute.data_type):
            invalid_types.append(
                {"name": attribute.name, "data_type": attribute.data_type}
            )

    casts_applied = input.metadata_output.type_casts_applied or []
    cast_warnings = input.metadata_output.type_cast_warnings or []
    warned_casts: set[tuple[str, str, str]] = {
        (
            str(w.get("attribute_name") or "").upper(),
            str(w.get("source_type") or "").upper(),
            str(w.get("target_type") or "").upper(),
        )
        for w in cast_warnings
    }

    unacknowledged_lossy: list[dict] = []
    acknowledged_lossy: list[dict] = []
    precision_risks: list[dict] = []

    for cast in casts_applied:
        issue = analyze_cast_safety(
            source_type=str(cast.get("source_type") or ""),
            target_type=str(cast.get("target_type") or ""),
            source_precision=cast.get("source_precision"),
            target_precision=cast.get("target_precision"),
            source_scale=cast.get("source_scale"),
            target_scale=cast.get("target_scale"),
            cast_expression=cast.get("cast_expression"),
            attribute_name=str(cast.get("attribute_name") or ""),
        )
        if issue is None:
            continue
        key = (
            issue.attribute_name.upper(),
            issue.source_type.upper(),
            issue.target_type.upper(),
        )
        if issue.severity == "block":
            if key in warned_casts:
                acknowledged_lossy.append(issue.__dict__)
            else:
                unacknowledged_lossy.append(issue.__dict__)
        else:
            precision_risks.append(issue.__dict__)

    semantic_mismatches: list[dict] = []
    domain = input.h4_mapping_spec.fields[0].get("domain") if input.h4_mapping_spec.fields else "data"
    for attribute in attributes:
        description = (attribute.description or "").lower()
        data_type = (attribute.data_type or "").upper()
        suspicious = (
            ("date" in description and not any(k in data_type for k in ("DATE", "DATETIME", "TIMESTAMP")))
            or ("amount" in description and not any(k in data_type for k in ("DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")))
            or ("indicator" in description and "BOOL" not in data_type and "CHAR" not in data_type)
        )
        if not suspicious:
            continue
        response = await llm_call(
            TYPE_COHERENCE_PROMPT.format(
                attr_name=attribute.name,
                description=attribute.description or "",
                data_type=attribute.data_type,
                source_type=attribute.source_table or "",
                domain=domain or "data",
            )
        )
        if response.get("type_appropriate") is False:
            semantic_mismatches.append(
                {
                    "name": attribute.name,
                    "data_type": attribute.data_type,
                    "suggested_type": response.get("suggested_type"),
                    "explanation": response.get("explanation"),
                }
            )

    total_attributes = max(len(attributes), 1)
    valid_count = total_attributes - len(invalid_types)
    if casts_applied:
        safe_casts = len(casts_applied) - len(unacknowledged_lossy)
        type_score = 0.6 * (valid_count / total_attributes) + 0.4 * (
            safe_casts / max(len(casts_applied), 1)
        )
    else:
        type_score = valid_count / total_attributes
    type_score = max(0.0, type_score)

    blocking = bool(invalid_types) or bool(unacknowledged_lossy) or type_score < 0.90
    if blocking:
        verdict = RuleVerdict.FAIL
    elif acknowledged_lossy or precision_risks or semantic_mismatches or type_score < 0.98:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    recommendations: list[str] = []
    for invalid in invalid_types[:25]:
        recommendations.append(
            f"Attribute '{invalid['name']}': data_type '{invalid['data_type']}' is invalid. Use a valid IndiMap type."
        )
    for issue in unacknowledged_lossy[:25]:
        recommendations.append(
            f"UNACKNOWLEDGED LOSSY CAST on '{issue['attribute_name']}': "
            f"{issue['source_type']} → {issue['target_type']}. {issue['description']}"
        )
    for mismatch in semantic_mismatches[:10]:
        recommendations.append(
            f"Attribute '{mismatch['name']}' may have wrong type. Suggested: {mismatch.get('suggested_type')}."
        )

    evidence = (
        f"Attributes: {total_attributes}. Invalid data types: {len(invalid_types)}. "
        f"Casts applied: {len(casts_applied)}. Unacknowledged lossy: {len(unacknowledged_lossy)}. "
        f"Acknowledged lossy: {len(acknowledged_lossy)}. Precision risks: {len(precision_risks)}. "
        f"Semantic mismatches: {len(semantic_mismatches)}. Judge type score: {type_score:.1%}."
    )

    return RuleScore(
        rule_id="R2_TYPE_SAFETY",
        rule_name="Type Safety",
        verdict=verdict,
        score=round(type_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=[i["name"] for i in invalid_types[:10]],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R5 — Round-Trip Consistency
# ---------------------------------------------------------------------------
async def rule_r5_round_trip_consistency(
    input: JudgeInputH5, llm_call, deterministic_analysis: dict | None = None
) -> RuleScore:
    h4_fields_by_name = {
        str(f.get("field_name") or "").upper(): f for f in input.h4_mapping_spec.fields
    }
    attributes_by_name = {a.name.upper(): a for a in input.metadata_output.attributes}

    source_changed: list[str] = []
    match_type_changed: list[str] = []
    reference_changed: list[str] = []
    transformation_changed: list[str] = []
    transformation_added: list[str] = []
    transformation_dropped: list[str] = []
    illegal_promotion: list[str] = []
    confidence_drift: list[str] = []
    reopen_h4_required = False

    for upper_name, h4_field in h4_fields_by_name.items():
        attribute = attributes_by_name.get(upper_name)
        if attribute is None:
            continue
        if str(h4_field.get("source_table") or "").upper() != attribute.source_table.upper():
            source_changed.append(attribute.name)
        if str(h4_field.get("source_column") or "").upper() != attribute.source_column.upper():
            if attribute.name not in source_changed:
                source_changed.append(attribute.name)
        if (h4_field.get("join_path") or None) != (attribute.join_path or None):
            # join_path drift is treated as source change
            if attribute.name not in source_changed:
                source_changed.append(attribute.name)
        if str(h4_field.get("match_type") or "") != attribute.match_type:
            match_type_changed.append(attribute.name)
            if h4_field.get("match_type") == "no_match" and attribute.match_type in {
                "exact",
                "near_exact",
                "partial",
                "transformed",
            }:
                illegal_promotion.append(attribute.name)
                reopen_h4_required = True
        h4_ref = h4_field.get("indimap_reference")
        if h4_ref and h4_ref != attribute.indimap_reference:
            reference_changed.append(attribute.name)

        h4_transform = h4_field.get("transformation")
        meta_transform = attribute.transformation
        if h4_transform and not meta_transform:
            transformation_dropped.append(attribute.name)
        elif not h4_transform and meta_transform:
            transformation_added.append(attribute.name)
        elif h4_transform and meta_transform and _norm(h4_transform) != _norm(meta_transform):
            response = await llm_call(
                TRANSFORMATION_FIDELITY_PROMPT.format(
                    h4_transform=h4_transform,
                    metadata_transform=meta_transform,
                    attr_name=attribute.name,
                    source_type=h4_field.get("source_type", ""),
                    target_type=attribute.data_type,
                )
            )
            if response.get("equivalent") is False:
                transformation_changed.append(attribute.name)

        h4_confidence = h4_field.get("confidence_score")
        if isinstance(h4_confidence, (int, float)):
            if abs(float(h4_confidence) - float(attribute.confidence_score)) > 0.05:
                confidence_drift.append(attribute.name)

    total_fields = max(len(h4_fields_by_name), 1)
    source_error_rate = len(source_changed) / total_fields
    transform_error_rate = (
        len(transformation_changed) + len(transformation_dropped)
    ) / total_fields
    fidelity_score = 1.0 - source_error_rate * 0.5 - transform_error_rate * 0.3
    fidelity_score -= len(illegal_promotion) * 0.20
    fidelity_score -= len(transformation_added) * 0.05
    fidelity_score -= len(confidence_drift) * 0.02
    fidelity_score = max(0.0, fidelity_score)

    blocking = (
        bool(source_changed)
        or bool(illegal_promotion)
        or bool(transformation_dropped)
        or bool(transformation_changed)
        or fidelity_score < 0.85
    )

    if blocking:
        verdict = RuleVerdict.FAIL
    elif transformation_added or match_type_changed or confidence_drift or reference_changed:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    recommendations: list[str] = []
    for field_name in source_changed[:25]:
        recommendations.append(
            f"Field '{field_name}': source mapping was changed without BSA approval — revert to H4-approved source."
        )
    for field_name in transformation_dropped[:10]:
        recommendations.append(
            f"Approved transformation for '{field_name}' was removed — restore or reopen H4."
        )
    for field_name in illegal_promotion[:10]:
        recommendations.append(
            f"Field '{field_name}' was NO MATCH at H4 but is now mapped — REOPEN H4 (cannot fix at H5)."
        )
    for field_name in transformation_changed[:10]:
        recommendations.append(
            f"Transformation for '{field_name}' is semantically different from H4-approved — revert."
        )
    for field_name in transformation_added[:10]:
        recommendations.append(
            f"Unapproved transformation added for '{field_name}' — remove or seek BSA approval."
        )

    evidence = (
        f"H4 fields: {total_fields}. Source changed: {len(source_changed)}. "
        f"Transformation altered: {len(transformation_changed)}. Transformation dropped: {len(transformation_dropped)}. "
        f"Transformation added: {len(transformation_added)}. Illegal promotions: {len(illegal_promotion)}. "
        f"Confidence drift: {len(confidence_drift)}. Round-trip fidelity: {fidelity_score:.1%}."
    )

    rule_score = RuleScore(
        rule_id="R5_ROUND_TRIP",
        rule_name="Round-Trip Consistency",
        verdict=verdict,
        score=round(fidelity_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=source_changed[:10] + illegal_promotion[:5],
        blocking=blocking,
        recommendations=recommendations[:25],
    )
    # Stash reopen_h4_required for the orchestrator/post-judge via a known marker
    if reopen_h4_required:
        rule_score.recommendations.insert(
            0, "REOPEN_H4_REQUIRED: NO MATCH field promoted to mapped state — H4 must be re-reviewed."
        )
    return rule_score


# ---------------------------------------------------------------------------
# R6 — Agent Score Calibration
# ---------------------------------------------------------------------------
async def rule_r6_agent_score_calibration(
    input: JudgeInputH5,
    llm_call,
    judge_naming_score: float = 0.0,
    judge_type_score: float = 0.0,
    judge_completeness_score: float = 0.0,
) -> RuleScore:
    agent_naming = float(input.metadata_output.naming_conformance_score or 0.0)
    agent_type = float(input.metadata_output.type_conformance_score or 0.0)
    agent_completeness = float(input.metadata_output.completeness_score or 0.0)

    dimensions = [
        ("naming", agent_naming, judge_naming_score, 0.95),
        ("type", agent_type, judge_type_score, 0.98),
        ("completeness", agent_completeness, judge_completeness_score, 0.95),
    ]

    significant_inflation: list[dict] = []
    significant_deflation: list[dict] = []
    false_threshold_pass: list[dict] = []
    below_threshold: list[dict] = []
    severe_inflation = False

    for name, agent_score, judge_score, threshold in dimensions:
        discrepancy = agent_score - judge_score
        if discrepancy > 0.20:
            severe_inflation = True
            significant_inflation.append({"dimension": name, "agent": agent_score, "judge": judge_score})
        elif discrepancy > 0.15:
            significant_inflation.append({"dimension": name, "agent": agent_score, "judge": judge_score})
        elif discrepancy < -0.15:
            significant_deflation.append({"dimension": name, "agent": agent_score, "judge": judge_score})

        if agent_score >= threshold and judge_score < threshold:
            false_threshold_pass.append({"dimension": name, "threshold": threshold})
        elif agent_score < threshold and judge_score < threshold:
            below_threshold.append({"dimension": name, "threshold": threshold})

    calibration_errors = len(significant_inflation) + len(false_threshold_pass)
    calibration_warns = len(significant_deflation) + len(below_threshold)
    calibration_score = max(0.0, 1.0 - calibration_errors * 0.25 - calibration_warns * 0.05)

    blocking = severe_inflation or bool(false_threshold_pass)
    if blocking:
        verdict = RuleVerdict.FAIL
    elif significant_inflation or below_threshold or significant_deflation:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    recommendations: list[str] = []
    for entry in false_threshold_pass:
        recommendations.append(
            f"Agent reports {entry['dimension']} score above {entry['threshold']:.0%} threshold but judge disagrees — BSA must review."
        )
    for entry in significant_inflation:
        recommendations.append(
            f"Agent {entry['dimension']} score appears inflated (agent {entry['agent']:.1%} vs judge {entry['judge']:.1%})."
        )
    for entry in below_threshold:
        recommendations.append(
            f"{entry['dimension']} score below required threshold {entry['threshold']:.0%}."
        )

    evidence = (
        f"Naming: agent={agent_naming:.1%} vs judge={judge_naming_score:.1%} "
        f"(Δ{agent_naming - judge_naming_score:+.1%}). "
        f"Type: agent={agent_type:.1%} vs judge={judge_type_score:.1%} "
        f"(Δ{agent_type - judge_type_score:+.1%}). "
        f"Completeness: agent={agent_completeness:.1%} vs judge={judge_completeness_score:.1%} "
        f"(Δ{agent_completeness - judge_completeness_score:+.1%}). "
        f"False threshold passes: {len(false_threshold_pass)}."
    )

    return RuleScore(
        rule_id="R6_SCORE_CALIBRATION",
        rule_name="Agent Score Calibration",
        verdict=verdict,
        score=round(calibration_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=[entry["dimension"] for entry in false_threshold_pass],
        blocking=blocking,
        recommendations=recommendations[:15],
    )
