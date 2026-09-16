from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from judges.h2_driver.prompts import (
    DIMENSION_EXTRACTION_PROMPT,
    FIELD_COMPLIANCE_CHECK_PROMPT,
    FYI_COHERENCE_PROMPT,
    FYI_FIELD_IDENTIFICATION_PROMPT,
    INTENT_DIRECTION_PROMPT,
    TRACEABILITY_CHECK_PROMPT,
    VALUE_SET_COMPLETENESS_PROMPT,
)
from judges.h2_driver.schemas import JudgeInputH2
from judges.h2_driver.sql_analyzer import (
    classify_operator_direction,
    detect_logic_issues,
    detect_transformations,
    estimate_predicate_selectivity,
    extract_field_names,
    parse_predicates,
)
from models.judge import RuleScore, RuleVerdict

RULE_WEIGHT = 1.0 / 7.0


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def _fuzzy_match(left: str, right: str, threshold: float = 0.72) -> bool:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= threshold


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# R1 — BRD Traceability
# ---------------------------------------------------------------------------
async def rule_r1_brd_traceability(input: JudgeInputH2, llm_call) -> RuleScore:
    predicates = input.driver_criteria.predicates or []
    total = max(len(predicates), 1)

    untraceable = 0
    faithful = 0
    partial = 0
    misrepresents = 0
    not_found = 0
    citations: list[str] = []
    recommendations: list[str] = []

    for predicate in predicates:
        brd_source_text = _safe_text(predicate.get("brd_source_text"))
        brd_section = _safe_text(predicate.get("brd_section"))
        raw = _safe_text(predicate.get("raw") or predicate.get("predicate") or predicate.get("standard_field"))

        if not brd_source_text or not brd_section:
            untraceable += 1
            recommendations.append(
                f"Predicate '{raw}' is missing BRD source citation. Add brd_source_text and brd_section."
            )
            continue

        # If the claimed source actually appears in the BRD, give credit for faithfulness
        if input.brd_text and brd_source_text.lower() in input.brd_text.lower():
            faithful += 1
            citations.append(brd_source_text)
            continue

        # Otherwise consult LLM
        response = await llm_call(
            TRACEABILITY_CHECK_PROMPT.format(
                predicate_raw=raw,
                brd_source_text=brd_source_text,
                brd_section=brd_section,
                brd_text=input.brd_text or "",
            )
        )
        verdict = _normalize(response.get("verdict"))
        explanation = _safe_text(response.get("explanation"))
        suggested = _safe_text(response.get("corrected_predicate"))
        if verdict == "faithful":
            faithful += 1
            citations.append(brd_source_text)
        elif verdict == "partially_faithful":
            partial += 1
            recommendations.append(
                f"Predicate '{raw}' partially supported by BRD: {explanation or brd_source_text}"
            )
        elif verdict == "misrepresents":
            misrepresents += 1
            recommendations.append(
                f"Predicate '{raw}' misrepresents BRD. {explanation}"
                + (f" Suggested: {suggested}" if suggested else "")
            )
        else:
            not_found += 1
            recommendations.append(
                f"Citation '{brd_source_text}' for '{raw}' not found in BRD."
            )

    structure_score = 1.0 - (untraceable / total)
    citation_total = max(faithful + partial + misrepresents + not_found, 1)
    citation_score = (faithful + 0.5 * partial) / citation_total

    h1_resolutions = input.h1_requirement_model.bsa_h1_resolutions or {}
    missing_resolutions: list[str] = []
    for resolution_id, resolution_value in h1_resolutions.items():
        target = _normalize(resolution_value)
        target_id = _normalize(resolution_id)
        found = False
        for predicate in predicates:
            blob = _normalize(json.dumps(predicate, sort_keys=True, default=str))
            if (target and target in blob) or (target_id and target_id in blob):
                found = True
                break
        if not found:
            missing_resolutions.append(resolution_id)
            recommendations.append(
                f"BSA H1 resolution '{resolution_id}' = '{resolution_value}' has no matching driver predicate."
            )

    if h1_resolutions:
        resolution_score = 1.0 - (len(missing_resolutions) / len(h1_resolutions))
    else:
        resolution_score = 1.0

    overall = (0.35 * structure_score) + (0.45 * citation_score) + (0.20 * resolution_score)

    blocking = misrepresents > 0 or not_found > 0 or len(missing_resolutions) > 0
    if blocking or overall < 0.75:
        verdict = RuleVerdict.FAIL
    elif overall < 0.90:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Checked {total} predicates: {faithful} faithful, {partial} partially faithful, "
        f"{misrepresents} misrepresent BRD, {not_found} citations not found, {untraceable} untraceable. "
        f"BSA H1 resolutions implemented: {len(h1_resolutions) - len(missing_resolutions)}/{len(h1_resolutions)}."
    )

    return RuleScore(
        rule_id="R1_BRD_TRACEABILITY",
        rule_name="BRD Traceability",
        verdict=verdict,
        score=round(max(0.0, overall), 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R2 — No Transformation Leakage (deterministic)
# ---------------------------------------------------------------------------
async def rule_r2_no_transformation_leakage(input: JudgeInputH2, llm_call) -> RuleScore:
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)
    matches = detect_transformations(predicates)

    activated_rules = input.driver_criteria.activated_rules or []
    parameterization_violations: list[str] = []
    transformation_keywords = re.compile(
        r"\b(SUBSTR|UPPER|LOWER|TRIM|CONCAT|CASE|TO_CHAR|TO_DATE|REPLACE|DECODE)\b",
        re.IGNORECASE,
    )
    for rule_text in activated_rules:
        if transformation_keywords.search(str(rule_text)):
            parameterization_violations.append(str(rule_text))

    if re.search(r"\bHAVING\b", where_clause, re.IGNORECASE):
        parameterization_violations.append("HAVING clause present in driver — aggregation is not allowed.")

    transformation_count = len(matches) + len(parameterization_violations)

    if transformation_count == 0:
        return RuleScore(
            rule_id="R2_NO_TRANSFORMATION_LEAKAGE",
            rule_name="No Transformation Leakage",
            verdict=RuleVerdict.PASS,
            score=1.0,
            weight=RULE_WEIGHT,
            evidence=f"No transformation expressions detected across {len(predicates)} predicates.",
            citations=[],
            blocking=False,
            recommendations=[],
        )

    evidence_lines = [f"TRANSFORMATION DETECTED: {transformation_count} violation(s) found."]
    recommendations: list[str] = []
    citations: list[str] = []
    for match in matches:
        evidence_lines.append(
            f"[{match.predicate_raw}] matched pattern {match.pattern_matched!r} text {match.match_text!r}"
        )
        citations.append(match.predicate_raw)
        recommendations.append(
            f"Move '{match.match_text}' from driver predicate '{match.predicate_raw}' to Transformation Rules; "
            "use the raw column directly."
        )
    for violation in parameterization_violations:
        evidence_lines.append(f"[parameterization] {violation}")
        recommendations.append(
            f"Move parameterization rule '{violation}' out of the driver — drivers contain only selection logic."
        )

    return RuleScore(
        rule_id="R2_NO_TRANSFORMATION_LEAKAGE",
        rule_name="No Transformation Leakage",
        verdict=RuleVerdict.FAIL,
        score=0.0,
        weight=RULE_WEIGHT,
        evidence=" ".join(evidence_lines),
        citations=citations[:25],
        blocking=True,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R3 — Standard Field Compliance
# ---------------------------------------------------------------------------
async def rule_r3_standard_field_compliance(input: JudgeInputH2, llm_call) -> RuleScore:
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)
    field_names = extract_field_names(predicates)
    standards = input.standards_dictionary or {}
    standard_values = {value.upper() for value in standards.values()}
    standard_values |= {key.upper() for key in standards.keys() if key.isupper()}

    compliant = 0
    non_compliant: list[str] = []
    case_violations: list[str] = []
    recommendations: list[str] = []

    for field_name in field_names:
        if field_name != field_name.upper():
            case_violations.append(field_name)
            recommendations.append(
                f"Rename '{field_name}' to '{field_name.upper()}' — standard fields are UPPER_SNAKE_CASE."
            )
        if field_name.upper() in standard_values:
            compliant += 1
        else:
            non_compliant.append(field_name)

    unmapped_used: list[str] = []
    unmapped_set = {f.upper() for f in (input.driver_criteria.unmapped_fields or [])}
    for field_name in field_names:
        if field_name.upper() in unmapped_set:
            unmapped_used.append(field_name)
            recommendations.append(
                f"'{field_name}' was flagged as unmapped by Business-to-Technical Mapper — do not use in driver."
            )

    domain = input.h1_requirement_model.primary_domain or "data"
    confirmed_compliant = compliant
    for field_name in list(non_compliant):
        response = await llm_call(
            FIELD_COMPLIANCE_CHECK_PROMPT.format(field_name=field_name, domain=domain)
        )
        is_standard = bool(response.get("is_standard"))
        suggested = _safe_text(response.get("likely_standard_equivalent"))
        if is_standard:
            confirmed_compliant += 1
            recommendations.append(
                f"'{field_name}' is a standard field but missing from the standards_dictionary — add it."
            )
        else:
            recommendations.append(
                f"Replace '{field_name}' with '{suggested or 'a standard field'}' — verify against ADW Standards."
            )

    total = max(len(field_names), 1)
    compliant_score = confirmed_compliant / total
    unmapped_penalty = len(unmapped_used) * 0.3
    case_penalty = len(case_violations) * 0.05
    overall = max(0.0, compliant_score - unmapped_penalty - case_penalty)

    blocking = len(unmapped_used) > 0 or overall < 0.80
    if blocking:
        verdict = RuleVerdict.FAIL
    elif overall < 0.95 or case_violations:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Fields in driver: {len(field_names)}. Compliant: {confirmed_compliant}. "
        f"Non-compliant: {max(len(field_names) - confirmed_compliant, 0)}. "
        f"Unmapped fields used: {len(unmapped_used)}. Case violations: {len(case_violations)}. "
        f"Non-compliant fields: {non_compliant[:10]}"
    )

    return RuleScore(
        rule_id="R3_STANDARD_FIELD_COMPLIANCE",
        rule_name="Standard Field Compliance",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=non_compliant[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R4 — Logical Consistency
# ---------------------------------------------------------------------------
async def rule_r4_logical_consistency(input: JudgeInputH2, llm_call) -> RuleScore:
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)
    issues = detect_logic_issues(predicates)

    always_false = [i for i in issues if i.issue_type == "always_false"]
    always_true = [i for i in issues if i.issue_type == "always_true"]
    redundant = [i for i in issues if i.issue_type == "redundant"]
    type_mismatches = [i for i in issues if i.issue_type == "type_mismatch"]

    recommendations: list[str] = []
    for issue in always_false:
        recommendations.append(f"Contradiction: {issue.description}. Rewrite the predicate set.")
    for issue in always_true:
        recommendations.append(f"Tautology: {issue.description}. Drop or correct the predicate.")
    for issue in redundant:
        recommendations.append(f"Redundancy: {issue.description}.")
    for issue in type_mismatches:
        recommendations.append(f"Type mismatch: {issue.description}.")

    estimated = (input.driver_criteria.estimated_row_impact or "").lower()
    selectivities = [estimate_predicate_selectivity(p) for p in predicates]
    impact_warn = False
    if estimated == "narrow" and selectivities and all(s == "broad" for s in selectivities):
        impact_warn = True
        recommendations.append(
            "Agent estimated 'narrow' scope, but every predicate is broad — verify selectivity."
        )
    if estimated == "broad" and selectivities and all(s == "narrow" for s in selectivities):
        impact_warn = True
        recommendations.append(
            "Agent estimated 'broad' scope, but every predicate is narrow — verify selectivity."
        )

    logic_score = 0.0 if always_false else 1.0
    consistency_score = max(0.0, 1.0 - 0.10 * len(always_true) - 0.05 * len(redundant))
    null_score = max(0.0, 1.0 - 0.05 * len(type_mismatches))
    impact_score = 0.7 if impact_warn else 1.0
    overall = (
        0.45 * logic_score
        + 0.25 * consistency_score
        + 0.15 * null_score
        + 0.15 * impact_score
    )

    blocking = bool(always_false)
    if blocking:
        verdict = RuleVerdict.FAIL
    elif overall < 0.70:
        verdict = RuleVerdict.FAIL
    elif overall < 0.90 or always_true or redundant or type_mismatches or impact_warn:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Logic issues: {len(always_false)} always-false, {len(always_true)} always-true, "
        f"{len(redundant)} redundant, {len(type_mismatches)} type mismatch. "
        f"Estimated impact: {estimated or 'unknown'}."
    )

    citations = [issue.description for issue in issues][:10]

    return RuleScore(
        rule_id="R4_LOGICAL_CONSISTENCY",
        rule_name="Logical Consistency",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R5 — IN/NOT IN Direction Correctness
# ---------------------------------------------------------------------------
async def rule_r5_operator_direction(input: JudgeInputH2, llm_call) -> RuleScore:
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)

    explicit_filters = input.h1_requirement_model.explicit_filters or []
    intent_map: dict[str, str] = {}
    for filter_item in explicit_filters:
        field_name = (
            filter_item.get("standard_field")
            or filter_item.get("field")
            or filter_item.get("field_name")
            or ""
        )
        if not field_name:
            continue

        operator_hint = (filter_item.get("operator") or "").lower()
        if operator_hint in {"include", "in", "="}:
            intent_map[field_name.upper()] = "INCLUDE"
            continue
        if operator_hint in {"exclude", "not in", "<>", "!="}:
            intent_map[field_name.upper()] = "EXCLUDE"
            continue

        source_text = _safe_text(
            filter_item.get("source_text")
            or filter_item.get("source")
            or filter_item.get("brd_source_text")
        )
        response = await llm_call(INTENT_DIRECTION_PROMPT.format(brd_source_text=source_text))
        intent = _normalize(response.get("intent")).upper() or "AMBIGUOUS"
        intent_map[field_name.upper()] = intent

    direction_errors: list[str] = []
    ambiguous_directions: list[str] = []
    scope_logic_warn: list[str] = []
    matched_count = 0
    correct_count = 0
    recommendations: list[str] = []

    for predicate in predicates:
        field_name = (predicate.field_name or "").upper()
        if not field_name:
            continue
        brd_intent = intent_map.get(field_name)
        if not brd_intent:
            continue
        matched_count += 1
        direction = classify_operator_direction(predicate)
        if brd_intent == "AMBIGUOUS":
            ambiguous_directions.append(predicate.raw)
            recommendations.append(
                f"BRD intent for '{field_name}' is ambiguous — flag as BSA Clarification."
            )
            continue
        if brd_intent == "INCLUDE" and direction == "exclusion":
            direction_errors.append(predicate.raw)
            recommendations.append(
                f"INVERSION: '{predicate.raw}' — BRD intends INCLUDE but driver uses exclusion. "
                f"Change operator to IN/=."
            )
            continue
        if brd_intent == "EXCLUDE" and direction == "inclusion":
            direction_errors.append(predicate.raw)
            recommendations.append(
                f"INVERSION: '{predicate.raw}' — BRD intends EXCLUDE but driver uses inclusion. "
                f"Change operator to NOT IN/<>."
            )
            continue
        correct_count += 1

        selectivity = estimate_predicate_selectivity(predicate)
        if selectivity == "narrow" and direction == "exclusion":
            scope_logic_warn.append(predicate.raw)
        if selectivity == "broad" and direction == "inclusion":
            scope_logic_warn.append(predicate.raw)

    total = max(matched_count, 1)
    direction_error_rate = len(direction_errors) / total
    ambiguous_rate = len(ambiguous_directions) / total
    overall = max(0.0, 1.0 - direction_error_rate * 0.8 - ambiguous_rate * 0.1)

    blocking = bool(direction_errors)
    if blocking:
        verdict = RuleVerdict.FAIL
    elif ambiguous_directions or scope_logic_warn:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Checked {matched_count} predicate directions. Correct: {correct_count}. "
        f"Direction errors: {len(direction_errors)}. Ambiguous intent: {len(ambiguous_directions)}. "
        f"Scope/logic mismatch warnings: {len(scope_logic_warn)}."
    )

    return RuleScore(
        rule_id="R5_OPERATOR_DIRECTION",
        rule_name="Operator Direction Correctness",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=direction_errors[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R6 — Population Dimension Coverage
# ---------------------------------------------------------------------------
async def rule_r6_population_coverage(input: JudgeInputH2, llm_call) -> RuleScore:
    response = await llm_call(
        DIMENSION_EXTRACTION_PROMPT.format(
            brd_text=input.brd_text or "",
            scope_json=json.dumps(input.h1_requirement_model.scope or {}, default=str),
        )
    )
    dimensions = response.get("dimensions") or []
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)
    field_names_upper = {(p.field_name or "").upper() for p in predicates if p.field_name}

    covered = 0
    missing: list[str] = []
    recommendations: list[str] = []
    for dimension in dimensions:
        if not dimension.get("required"):
            continue
        expected_fields = [str(f).upper() for f in dimension.get("expected_standard_fields") or []]
        if any(field_name in field_names_upper for field_name in expected_fields):
            covered += 1
            continue
        # Fuzzy fallback — does any predicate field name resemble the dimension name?
        dim_name = _normalize(dimension.get("dimension"))
        if any(_fuzzy_match(dim_name, name) for name in field_names_upper):
            covered += 1
            continue
        missing.append(dimension.get("dimension") or "<unnamed>")
        recommendations.append(
            f"Dimension '{dimension.get('dimension')}' required by BRD ('{dimension.get('brd_evidence')}') "
            f"is missing. Expected fields: {expected_fields or 'unknown'}."
        )

    required_count = sum(1 for d in dimensions if d.get("required"))
    activated_rules = input.driver_criteria.activated_rules or []
    activated_blob = " ".join(str(r) for r in activated_rules).upper()
    extra_predicates: list[str] = []
    expected_field_set: set[str] = set()
    for dimension in dimensions:
        for field_name in dimension.get("expected_standard_fields") or []:
            expected_field_set.add(str(field_name).upper())
    for predicate in predicates:
        field_name = (predicate.field_name or "").upper()
        if not field_name:
            continue
        if field_name in expected_field_set:
            continue
        if field_name in activated_blob:
            continue  # parameterization addition — accepted
        extra_predicates.append(predicate.raw)

    incomplete_value_sets: list[str] = []
    for predicate in predicates:
        if (predicate.operator or "").upper() != "IN":
            continue
        # Match driver predicate to a BRD filter by field
        matching_filter = next(
            (
                f
                for f in input.h1_requirement_model.explicit_filters
                if (f.get("standard_field") or f.get("field") or "").upper()
                == (predicate.field_name or "").upper()
            ),
            None,
        )
        if not matching_filter:
            continue
        source_text = _safe_text(
            matching_filter.get("source_text")
            or matching_filter.get("source")
            or matching_filter.get("brd_source_text")
        )
        if not source_text:
            continue
        check = await llm_call(
            VALUE_SET_COMPLETENESS_PROMPT.format(
                brd_source_text=source_text,
                predicate_raw=predicate.raw,
                field_name=predicate.field_name,
                driver_values=json.dumps(predicate.values),
            )
        )
        if not check.get("complete"):
            incomplete_value_sets.append(predicate.raw)
            recommendations.append(
                f"IN clause for '{predicate.field_name}' may be incomplete. "
                f"Missing: {check.get('missing_values')}."
            )

    coverage_rate = covered / max(required_count, 1)
    extra_penalty = len(extra_predicates) * 0.05
    value_set_penalty = len(incomplete_value_sets) * 0.10
    overall = max(0.0, coverage_rate - extra_penalty - value_set_penalty)

    blocking = len(missing) > 0
    if blocking or overall < 0.80:
        verdict = RuleVerdict.FAIL
    elif extra_predicates or incomplete_value_sets:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"BRD dimensions: {required_count} required. Covered: {covered}/{required_count}. "
        f"Missing: {missing}. Extra predicates: {len(extra_predicates)}. "
        f"Incomplete value sets: {len(incomplete_value_sets)}."
    )

    return RuleScore(
        rule_id="R6_POPULATION_COVERAGE",
        rule_name="Population Dimension Coverage",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=missing[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )


# ---------------------------------------------------------------------------
# R7 — FYI Usage Correctness
# ---------------------------------------------------------------------------
async def rule_r7_fyi_usage(input: JudgeInputH2, llm_call) -> RuleScore:
    fyi_lookups = input.driver_criteria.fyi_lookups or []
    where_clause = input.driver_criteria.where_clause or ""
    predicates = parse_predicates(where_clause)
    field_names_upper = {(p.field_name or "").upper() for p in predicates if p.field_name}

    fyi_without_field = 0
    unused_fyi = 0
    non_fyi_values = 0
    recommendations: list[str] = []
    citations: list[str] = []

    for lookup in fyi_lookups:
        standard_field = _safe_text(lookup.get("standard_field"))
        values_resolved = lookup.get("values_resolved") or []
        fyi_table = _safe_text(lookup.get("fyi_table"))

        if not standard_field and values_resolved:
            fyi_without_field += 1
            recommendations.append(
                f"FYI '{fyi_table}' was consulted before identifying the standard field. "
                "Identify the standard field first via ADW Standards, then use FYI for values."
            )
            continue

        if standard_field and standard_field.upper() not in field_names_upper:
            unused_fyi += 1
            recommendations.append(
                f"FYI resolved values for '{standard_field}' but the field is not used in the driver."
            )
            continue

        # Compare driver values for this field against FYI values
        matching_predicate = next(
            (p for p in predicates if (p.field_name or "").upper() == standard_field.upper()),
            None,
        )
        if matching_predicate and values_resolved:
            check = await llm_call(
                FYI_COHERENCE_PROMPT.format(
                    standard_field=standard_field,
                    fyi_values=json.dumps(values_resolved),
                    driver_values=json.dumps(matching_predicate.values),
                )
            )
            if not check.get("consistent", True):
                non_fyi_values += 1
                citations.append(matching_predicate.raw)
                recommendations.append(
                    f"Driver values for '{standard_field}' include items not in FYI set: "
                    f"{check.get('values_not_in_fyi')}."
                )

    fyi_field_identification = 0
    if fyi_lookups and (input.driver_criteria.unmapped_fields or []):
        ident = await llm_call(
            FYI_FIELD_IDENTIFICATION_PROMPT.format(
                fyi_lookups_json=json.dumps(fyi_lookups, default=str),
                unmapped_fields=json.dumps(input.driver_criteria.unmapped_fields),
            )
        )
        if ident.get("misuse_detected"):
            misused = ident.get("misused_lookups") or []
            fyi_field_identification = max(1, len(misused))
            recommendations.append(
                "FYI was used to identify fields rather than to resolve values. "
                "Correct order: BRD → Standard Field → FYI values."
            )

    process_score = (
        1.0
        if fyi_without_field == 0 and fyi_field_identification == 0
        else 0.0
    )
    coherence_score = max(0.0, 1.0 - 0.15 * non_fyi_values)
    usage_score = max(0.0, 1.0 - 0.10 * unused_fyi)
    overall = 0.60 * process_score + 0.25 * coherence_score + 0.15 * usage_score

    blocking = process_score == 0.0
    if blocking or overall < 0.75:
        verdict = RuleVerdict.FAIL
    elif overall < 0.90:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"FYI lookups performed: {len(fyi_lookups)}. "
        f"FYI without known field: {fyi_without_field}. "
        f"Unused FYI results: {unused_fyi}. "
        f"Values outside FYI set: {non_fyi_values}. "
        f"FYI field-identification misuse: {fyi_field_identification}."
    )

    return RuleScore(
        rule_id="R7_FYI_USAGE",
        rule_name="FYI Usage Correctness",
        verdict=verdict,
        score=round(overall, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations[:10],
        blocking=blocking,
        recommendations=recommendations[:25],
    )
