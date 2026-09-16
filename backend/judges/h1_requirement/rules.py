from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from typing import Any, Protocol

from judges.h1_requirement.prompts import (
    AMBIGUITY_DETECTION_PROMPT,
    COMPLIANCE_DETECTION_PROMPT,
    DOMAIN_CLASSIFICATION_PROMPT,
    FILTER_COUNT_CHECK_PROMPT,
    HALLUCINATION_CHECK_PROMPT,
    SCOPE_EXTRACTION_PROMPT,
    TRANSCRIPT_RULES_PROMPT,
)
from judges.h1_requirement.schemas import JudgeInputH1
from models.judge import RuleScore, RuleVerdict

RULE_WEIGHT = 1.0 / 6.0
R1_CHECK_WEIGHTS = {
    "purpose": 0.20,
    "scope": 0.25,
    "filters": 0.25,
    "layout": 0.15,
    "compliance": 0.15,
}


class RuleFunction(Protocol):
    async def __call__(self, input: JudgeInputH1, llm_call: callable) -> RuleScore:
        ...


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value.strip()).lower()
    if isinstance(value, (list, tuple, set)):
        return " ".join(_normalize_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        return " ".join(
            f"{_normalize_text(key)} {_normalize_text(val)}" for key, val in value.items()
        )
    return str(value).strip().lower()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _fuzzy_match(left: str, right: str, threshold: float = 0.72) -> bool:
    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm in right_norm or right_norm in left_norm:
        return True
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= threshold


def _sentence_split(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]


def _serialize_requirement_model(requirement_model) -> str:
    return json.dumps(requirement_model.model_dump(mode="json"), sort_keys=True)


def _scope_pairs(scope: dict[str, Any]) -> list[tuple[str, Any]]:
    pairs: list[tuple[str, Any]] = []
    for key, value in scope.items():
        if value not in (None, "", [], {}, ()):
            pairs.append((f"scope.{key}", value))
    return pairs


def _filter_items(explicit_filters: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for index, filter_item in enumerate(explicit_filters):
        items.append((f"explicit_filters[{index}]", filter_item))
    return items


def _implicit_rule_items(implicit_rules: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for index, rule in enumerate(implicit_rules[:5]):
        items.append((f"implicit_rules[{index}]", rule))
    return items


def _join_documents(brd_text: str, transcript_texts: list[str]) -> str:
    transcript_block = "\n\n".join(transcript_texts)
    return f"BRD:\n{brd_text}\n\nTRANSCRIPTS:\n{transcript_block}".strip()


def _find_model_resolution(field_name: str, requirement_model) -> str | None:
    field_norm = _normalize_text(field_name)
    if not field_norm:
        return None
    scope_match = next(
        (
            f"scope.{key}={value}"
            for key, value in requirement_model.scope.items()
            if _fuzzy_match(field_norm, key, 0.68) and value not in (None, "", [], {})
        ),
        None,
    )
    if scope_match:
        return scope_match
    for filter_item in requirement_model.explicit_filters:
        field = filter_item.get("field") or filter_item.get("field_name") or ""
        if _fuzzy_match(field_norm, field, 0.68):
            return json.dumps(filter_item, sort_keys=True)
    for output_field in requirement_model.output_fields:
        field = output_field.get("field_name") or output_field.get("attribute_name") or ""
        if _fuzzy_match(field_norm, field, 0.75):
            return json.dumps(output_field, sort_keys=True)
    return None


def _model_text_for_scope(requirement_model) -> str:
    return _normalize_text(requirement_model.scope) + " " + _normalize_text(
        requirement_model.explicit_filters
    )


def _tokens(text: str) -> set[str]:
    stopwords = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "for",
        "in",
        "on",
        "with",
        "is",
        "are",
        "be",
        "we",
        "will",
        "only",
    }
    return {
        token
        for token in re.findall(r"\b[a-zA-Z0-9_]+\b", _normalize_text(text))
        if token not in stopwords
    }


def _statement_polarity(text: str) -> str:
    normalized = _normalize_text(text)
    if any(token in normalized for token in ["out of scope", "exclude", "excluding", "do not", "not include"]):
        return "exclude"
    if any(token in normalized for token in ["only", "must include", "in scope", "include", "including"]):
        return "include"
    return "neutral"


def _contradicts_brd(statement: str, brd_text: str) -> str | None:
    statement_tokens = _tokens(statement)
    statement_polarity = _statement_polarity(statement)
    if not statement_tokens or statement_polarity == "neutral":
        return None
    for sentence in _sentence_split(brd_text):
        sentence_tokens = _tokens(sentence)
        if len(statement_tokens & sentence_tokens) < 2:
            continue
        sentence_polarity = _statement_polarity(sentence)
        if sentence_polarity != "neutral" and sentence_polarity != statement_polarity:
            return sentence
    return None


def _domain_hint_present(primary_domain: str, explicit_filters: list[dict[str, Any]]) -> bool:
    domain = _normalize_text(primary_domain)
    filter_text = _normalize_text(explicit_filters)
    if domain == "claims":
        return any(token in filter_text for token in ["claim", "clm", "diagnosis", "procedure"])
    if domain == "provider":
        return any(token in filter_text for token in ["provider", "npi", "physician", "facility"])
    return True


def _domain_normalize(domain: str) -> str:
    value = _normalize_text(domain).replace("human resources", "hr")
    mapping = {
        "claim": "claims",
        "claims": "claims",
        "provider": "provider",
        "providers": "provider",
        "member": "member",
        "members": "member",
        "pharmacy": "pharmacy",
        "enrollment": "enrollment",
        "finance": "finance",
        "billing": "finance",
        "hr": "hr",
        "other": "other",
        "eligibility": "member",
    }
    return mapping.get(value, value)


def _domain_synonym(left: str, right: str) -> bool:
    pairs = {
        ("member", "enrollment"),
        ("enrollment", "member"),
        ("member", "eligibility"),
        ("eligibility", "member"),
        ("pharmacy", "claims"),
        ("claims", "pharmacy"),
    }
    left_norm = _domain_normalize(left)
    right_norm = _domain_normalize(right)
    return left_norm == right_norm or (left_norm, right_norm) in pairs


async def rule_r1_completeness(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    fail_reasons: list[str] = []
    warn_reasons: list[str] = []
    recommendations: list[str] = []
    citations: list[str] = []

    purpose_ok = _word_count(requirement_model.extract_purpose) >= 10
    purpose_score = 1.0 if purpose_ok else 0.0
    if not purpose_ok:
        fail_reasons.append("extract_purpose missing or too short")
        recommendations.append("Repopulate extract_purpose with a complete business-purpose summary from the BRD.")

    required_scope_keys = ["company", "LOB", "funding", "date_range"]
    missing_scope_keys = [key for key in required_scope_keys if key not in requirement_model.scope]
    empty_scope_keys = [
        key
        for key in required_scope_keys
        if key in requirement_model.scope and requirement_model.scope.get(key) in (None, "", [], {})
    ]
    if missing_scope_keys:
        scope_score = 0.0
        fail_reasons.append(f"scope missing keys: {missing_scope_keys}")
        recommendations.extend(
            [f"Repopulate scope.{key} from the relevant BRD section." for key in missing_scope_keys]
        )
    elif empty_scope_keys:
        scope_score = max(0.0, 1 - (len(empty_scope_keys) / len(required_scope_keys)))
        warn_reasons.append(f"scope empty values: {empty_scope_keys}")
        recommendations.extend(
            [f"Fill scope.{key} with the explicit value from the BRD." for key in empty_scope_keys]
        )
    else:
        scope_score = 1.0

    filter_response = await llm_call(FILTER_COUNT_CHECK_PROMPT.format(brd_text=input.brd_text))
    brd_filter_count = int(filter_response.get("filter_count") or 0)
    model_filter_count = len(requirement_model.explicit_filters)
    if brd_filter_count <= 0:
        filter_score = 1.0
    else:
        filter_ratio = model_filter_count / max(brd_filter_count, 1)
        if filter_ratio < 0.70:
            filter_score = max(0.0, round(filter_ratio, 4))
            fail_reasons.append("significant filter under-extraction")
            recommendations.append(
                f"Re-run the BRD parser — approximately {max(brd_filter_count - model_filter_count, 0)} filter conditions appear to be missing."
            )
        elif filter_ratio < 0.90:
            filter_score = round(filter_ratio, 4)
            warn_reasons.append("minor filter under-extraction")
            recommendations.append(
                f"Review the BRD for {max(brd_filter_count - model_filter_count, 0)} missed filter conditions and add them to explicit_filters."
            )
        else:
            filter_score = 1.0

    output_fields_count = len(requirement_model.output_fields)
    total_field_count = requirement_model.total_field_count
    if total_field_count == 0:
        layout_score = 0.0
        fail_reasons.append("layout field count is zero")
        recommendations.append("Re-run the Layout Parser — no output fields were captured.")
    elif output_fields_count < total_field_count:
        layout_score = round(output_fields_count / max(total_field_count, 1), 4)
        fail_reasons.append("layout fields were dropped")
        recommendations.append(
            f"Layout Parser dropped {total_field_count - output_fields_count} fields — inspect file-layout parsing errors."
        )
    else:
        layout_score = 1.0

    compliance_response = await llm_call(
        COMPLIANCE_DETECTION_PROMPT.format(brd_text=input.brd_text)
    )
    has_compliance = bool(compliance_response.get("has_compliance"))
    terms_found = compliance_response.get("terms_found") or []
    relevant_sentences = compliance_response.get("relevant_sentences") or []
    citations.extend(relevant_sentences)
    if has_compliance and not requirement_model.compliance_flags:
        compliance_score = 0.5
        warn_reasons.append("compliance references present but compliance_flags empty")
        recommendations.append(
            "Search the BRD for compliance and privacy terms and populate compliance_flags."
        )
    else:
        compliance_score = 1.0

    overall_score = (
        purpose_score * R1_CHECK_WEIGHTS["purpose"]
        + scope_score * R1_CHECK_WEIGHTS["scope"]
        + filter_score * R1_CHECK_WEIGHTS["filters"]
        + layout_score * R1_CHECK_WEIGHTS["layout"]
        + compliance_score * R1_CHECK_WEIGHTS["compliance"]
    )
    verdict = (
        RuleVerdict.FAIL
        if fail_reasons
        else RuleVerdict.WARN
        if warn_reasons
        else RuleVerdict.PASS
    )
    evidence = (
        f"Scope captured: {len(required_scope_keys) - len(missing_scope_keys) - len(empty_scope_keys)}/{len(required_scope_keys)}. "
        f"Filters: {model_filter_count} extracted vs ~{brd_filter_count} in BRD. "
        f"Layout: {output_fields_count}/{total_field_count} fields. "
        f"Compliance: {'flags found' if requirement_model.compliance_flags else 'no flags — verify'}."
    )
    if terms_found:
        evidence += f" Compliance terms detected: {', '.join(map(str, terms_found))}."

    return RuleScore(
        rule_id="R1_COMPLETENESS",
        rule_name="BRD Completeness",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=False,
        recommendations=recommendations,
    )


async def rule_r2_no_hallucination(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    document_text = _join_documents(input.brd_text, input.transcript_texts)
    sampled_items = _scope_pairs(requirement_model.scope) + _filter_items(
        requirement_model.explicit_filters
    ) + _implicit_rule_items(requirement_model.implicit_rules)

    supported = 0
    inferred = 0
    fabricated = 0
    citations: list[str] = []
    fabricated_items: list[str] = []
    recommendations: list[str] = []

    for field_context, value in sampled_items:
        response = await llm_call(
            HALLUCINATION_CHECK_PROMPT.format(
                value=json.dumps(value, sort_keys=True) if not isinstance(value, str) else value,
                field_context=field_context,
                document_text=document_text,
            )
        )
        verdict = _normalize_text(response.get("verdict"))
        quote = str(response.get("supporting_quote") or "").strip()
        if quote:
            citations.append(quote)
        if verdict == "supported":
            supported += 1
        elif verdict == "inferred":
            inferred += 1
            recommendations.append(
                f"Confirm {field_context} with the BSA — it appears inferred rather than explicit."
            )
        else:
            fabricated += 1
            fabricated_items.append(f"{field_context}={value}")
            recommendations.append(
                f"Remove or replace {field_context} — no supporting source sentence was found in the BRD or transcripts."
            )

    total_checked = max(len(sampled_items), 1)
    overall_score = (supported + (0.5 * inferred)) / total_checked
    if fabricated > 0:
        verdict = RuleVerdict.FAIL
    elif inferred > 0:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Checked {total_checked} values: {supported} supported, {inferred} inferred, {fabricated} fabricated."
    )
    if fabricated_items:
        evidence += f" Fabricated: {', '.join(fabricated_items)}."

    return RuleScore(
        rule_id="R2_NO_HALLUCINATION",
        rule_name="No Hallucination",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=fabricated > 0,
        recommendations=recommendations,
    )


async def rule_r3_ambiguity_coverage(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    response = await llm_call(AMBIGUITY_DETECTION_PROMPT.format(brd_text=input.brd_text))
    judge_ambiguities = response.get("ambiguities") or []
    model_ambiguities = requirement_model.ambiguities

    matched_count = 0
    missed_items: list[dict[str, Any]] = []
    silent_resolutions: list[str] = []
    recommendations: list[str] = []
    citations: list[str] = []

    for judge_ambiguity in judge_ambiguities:
        statement = str(judge_ambiguity.get("statement") or "")
        description = str(judge_ambiguity.get("description") or "")
        citations.append(statement)
        matched = any(
            _fuzzy_match(description, item.get("description", ""))
            or _fuzzy_match(statement, item.get("description", ""))
            or _fuzzy_match(statement, item.get("source", ""))
            for item in model_ambiguities
        )
        if matched:
            matched_count += 1
            continue
        missed_items.append(judge_ambiguity)
        affected_field = str(judge_ambiguity.get("affected_field") or "")
        resolved_value = _find_model_resolution(affected_field, requirement_model)
        if resolved_value:
            silent_resolutions.append(f"{affected_field} -> {resolved_value}")
            recommendations.append(
                f"Field {affected_field or 'unknown'} was silently resolved. Replace the resolved value with an explicit ambiguity flag."
            )
        recommendations.append(
            f"Add ambiguity flag for '{statement}' with description '{description}'."
        )

    judge_detected_count = len(judge_ambiguities)
    coverage_rate = matched_count / max(judge_detected_count, 1)
    silent_resolution_count = len(silent_resolutions)
    overall_score = max(0.0, coverage_rate - (silent_resolution_count * 0.3))

    if silent_resolution_count > 0 or coverage_rate < 0.70:
        verdict = RuleVerdict.FAIL
    elif coverage_rate < 0.90:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Judge detected {judge_detected_count} ambiguities. "
        f"Model flagged {matched_count} ({coverage_rate:.0%} coverage). "
        f"Missed: {len(missed_items)}. Silent resolutions: {silent_resolution_count}."
    )
    if silent_resolutions:
        evidence += f" Silent resolutions: {', '.join(silent_resolutions)}."

    return RuleScore(
        rule_id="R3_AMBIGUITY_COVERAGE",
        rule_name="Ambiguity Coverage",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=silent_resolution_count > 0,
        recommendations=recommendations,
    )


async def rule_r4_scope_boundary(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    response = await llm_call(SCOPE_EXTRACTION_PROMPT.format(brd_text=input.brd_text))
    in_scope = response.get("in_scope") or []
    out_of_scope = response.get("out_of_scope") or []
    model_scope_text = _model_text_for_scope(requirement_model)

    matched_in_scope = sum(1 for statement in in_scope if _fuzzy_match(statement, model_scope_text))
    missing_in_scope = [statement for statement in in_scope if not _fuzzy_match(statement, model_scope_text)]
    leaking_populations = [statement for statement in out_of_scope if _fuzzy_match(statement, model_scope_text)]
    out_scope_violations = len(leaking_populations)
    total_in_scope = len(in_scope)
    in_scope_coverage = matched_in_scope / max(total_in_scope, 1)
    overall_score = max(0.0, in_scope_coverage - (out_scope_violations * 0.4))

    recommendations = [
        f"Remove '{statement}' from the model scope or explicit filters because the BRD marks it out of scope."
        for statement in leaking_populations
    ]
    recommendations.extend(
        [f"Add in-scope population from the BRD: '{statement}'." for statement in missing_in_scope]
    )

    domain_warn = not _domain_hint_present(
        requirement_model.primary_domain, requirement_model.explicit_filters
    )
    if domain_warn:
        recommendations.append(
            f"Add domain-consistent scope hints for the {requirement_model.primary_domain} domain."
        )

    if out_scope_violations > 0 or in_scope_coverage < 0.80:
        verdict = RuleVerdict.FAIL
    elif in_scope_coverage < 0.95 or domain_warn:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"In-scope: {matched_in_scope}/{total_in_scope} BRD statements captured. "
        f"Out-of-scope violations: {out_scope_violations}. "
        f"Leaking populations: {leaking_populations if leaking_populations else 'none'}."
    )

    citations = [*in_scope, *out_of_scope]
    return RuleScore(
        rule_id="R4_SCOPE_BOUNDARY",
        rule_name="Scope Boundary",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=out_scope_violations > 0,
        recommendations=recommendations,
    )


async def rule_r5_transcript_consistency(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    if not input.transcript_texts:
        return RuleScore(
            rule_id="R5_TRANSCRIPT_CONSISTENCY",
            rule_name="Transcript Consistency",
            verdict=RuleVerdict.PASS,
            score=1.0,
            weight=RULE_WEIGHT,
            evidence="No transcripts provided — rule not applicable.",
            citations=[],
            blocking=False,
            recommendations=[],
        )

    prescriptive_rules: list[dict[str, Any]] = []
    for transcript_text in input.transcript_texts:
        response = await llm_call(
            TRANSCRIPT_RULES_PROMPT.format(transcript_text=transcript_text)
        )
        prescriptive_rules.extend(response.get("prescriptive") or [])

    matched = 0
    missed_rules: list[str] = []
    uncaptured_conflicts = 0
    silent_overrides = 0
    conflicts_surfaced = 0
    recommendations: list[str] = []
    citations: list[str] = []

    for statement_info in prescriptive_rules:
        statement = str(statement_info.get("statement") or "")
        field_affected = str(statement_info.get("field_affected") or "")
        citations.append(statement)

        implicit_match = any(
            _fuzzy_match(statement, rule.get("rule_description", ""))
            or _fuzzy_match(statement, rule.get("decision_text", ""))
            for rule in requirement_model.implicit_rules
        )
        if implicit_match:
            matched += 1
        else:
            missed_rules.append(statement)
            recommendations.append(
                f"Add to implicit_rules: '{statement}' from the provided transcript."
            )

        conflicting_brd_sentence = _contradicts_brd(statement, input.brd_text)
        if conflicting_brd_sentence:
            conflict_match = any(
                _fuzzy_match(statement, conflict.get("description", ""))
                or _fuzzy_match(conflicting_brd_sentence, conflict.get("description", ""))
                for conflict in requirement_model.conflicts_with_brd
            )
            if conflict_match:
                conflicts_surfaced += 1
            else:
                uncaptured_conflicts += 1
                recommendations.append(
                    f"Add to conflicts_with_brd: transcript says '{statement}' but BRD says '{conflicting_brd_sentence}'."
                )
                model_resolution = _find_model_resolution(field_affected or statement, requirement_model)
                if model_resolution and _fuzzy_match(statement, model_resolution, 0.55):
                    silent_overrides += 1
                    recommendations.append(
                        f"Revert the silently applied transcript override for {field_affected or 'the affected field'} and record the conflict explicitly."
                    )

    total_prescriptive = len(prescriptive_rules)
    coverage_rate = matched / max(total_prescriptive, 1)
    overall_score = max(
        0.0,
        coverage_rate - (uncaptured_conflicts * 0.2) - (silent_overrides * 0.4),
    )

    if silent_overrides > 0 or overall_score < 0.70:
        verdict = RuleVerdict.FAIL
    elif overall_score < 0.90 or uncaptured_conflicts > 0:
        verdict = RuleVerdict.WARN
    else:
        verdict = RuleVerdict.PASS

    evidence = (
        f"Transcripts provided: {len(input.transcript_texts)}. Prescriptive rules found: {total_prescriptive}. "
        f"Captured: {matched}/{total_prescriptive}. Conflicts surfaced: {conflicts_surfaced}. "
        f"Silent overrides: {silent_overrides}."
    )

    return RuleScore(
        rule_id="R5_TRANSCRIPT_CONSISTENCY",
        rule_name="Transcript Consistency",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=citations,
        blocking=silent_overrides > 0,
        recommendations=recommendations,
    )


async def rule_r6_domain_classification(input: JudgeInputH1, llm_call: callable) -> RuleScore:
    requirement_model = input.requirement_model
    response = await llm_call(
        DOMAIN_CLASSIFICATION_PROMPT.format(brd_text=input.brd_text)
    )
    judge_domain = str(response.get("domain") or "Other")
    judge_confidence = float(response.get("confidence") or 0.0)

    model_domain = requirement_model.primary_domain
    exact_match = _domain_normalize(judge_domain) == _domain_normalize(model_domain)
    synonym_match = not exact_match and _domain_synonym(judge_domain, model_domain)

    if exact_match:
        domain_score = 1.0
        verdict = RuleVerdict.PASS
    elif synonym_match:
        domain_score = 0.6
        verdict = RuleVerdict.WARN
    else:
        domain_score = 0.0
        verdict = RuleVerdict.FAIL

    field_count = len(requirement_model.output_fields)
    filter_count = len(requirement_model.explicit_filters)
    expected_low = 1 + (field_count // 20) + (filter_count // 5)
    expected_high = expected_low + 3
    expected_mid = math.floor((expected_low + expected_high) / 2)

    complexity_gap_low = requirement_model.complexity_score < (expected_low - 1)
    complexity_gap_high = requirement_model.complexity_score > (expected_high + 2)
    complexity_warn = complexity_gap_low or complexity_gap_high
    complexity_far_off = requirement_model.complexity_score < max(0, expected_low - 3) or requirement_model.complexity_score > (expected_high + 4)
    complexity_component = 0.5 if complexity_far_off else 0.7 if complexity_warn else 1.0

    catalog_component = 1.0 if requirement_model.recommended_catalogs else 0.5
    overall_score = (
        0.5 * domain_score + 0.3 * complexity_component + 0.2 * catalog_component
    )

    recommendations: list[str] = []
    if not exact_match:
        recommendations.append(
            f"Update primary_domain to '{judge_domain}' or document why '{model_domain}' is preferable."
        )
    if complexity_warn:
        recommendations.append(
            f"Adjust complexity_score toward {expected_mid}; the current field and filter counts suggest a range of {expected_low}-{expected_high}."
        )
    if not requirement_model.recommended_catalogs:
        recommendations.append(
            f"Populate recommended_catalogs based on the '{model_domain}' domain."
        )

    if verdict != RuleVerdict.FAIL:
        if overall_score < 0.70 or complexity_warn or not requirement_model.recommended_catalogs:
            verdict = RuleVerdict.WARN
        else:
            verdict = RuleVerdict.PASS

    evidence = (
        f"Judge domain: {judge_domain} (confidence {judge_confidence:.2f}). "
        f"Model domain: {model_domain}. Match: {exact_match or synonym_match}. "
        f"Complexity: model={requirement_model.complexity_score}, expected={expected_low}–{expected_high}. "
        f"Catalogs: {requirement_model.recommended_catalogs}."
    )

    return RuleScore(
        rule_id="R6_DOMAIN_CLASSIFICATION",
        rule_name="Domain Classification Plausibility",
        verdict=verdict,
        score=round(overall_score, 4),
        weight=RULE_WEIGHT,
        evidence=evidence,
        citations=[str(response.get("reason") or "")] if response.get("reason") else [],
        blocking=False,
        recommendations=recommendations,
    )
