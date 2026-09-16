"""
Step 3 (HITL) - Review question builder utilities (non-LLM).

This module contains deterministic helpers used by ReviewQuestionBuilderAgent to:
  - decide review scope
  - dedupe and prioritize questions
  - assemble ReviewQuestion objects from Step2State issues + question seeds

LLM-related code (agents, structured tool calls, caching) must stay in:
  - agents/mapping_review/sub_agents/review_question_builder_agent/agent.py
"""

from __future__ import annotations

from typing import Iterable

from agents.mapping_generation.models import (
    CandidateSource,
    IssueSeverity,
    IssueType,
    MappingRow,
    OpenIssue,
    RuleType,
    Step2State,
)
from agents.mapping_ingestion.models import ColumnRef
from agents.mapping_review.models import (
    AnswerFormat,
    AnswerSpec,
    QuestionPriority,
    ReviewQuestion,
    ReviewQuestionKind,
    SelectOption,
)
from config.settings import config


def _priority_rank(p: QuestionPriority) -> int:
    return {"P0": 0, "P1": 1, "P2": 2}.get(p.value, 9)


def _severity_to_priority(sev: IssueSeverity) -> QuestionPriority:
    if sev == IssueSeverity.ERROR:
        return QuestionPriority.P0
    if sev == IssueSeverity.WARN:
        return QuestionPriority.P1
    return QuestionPriority.P2


def _safe_step3_priority(value: str, fallback: QuestionPriority = QuestionPriority.P1) -> QuestionPriority:
    try:
        return QuestionPriority(value)
    except Exception:
        return fallback


def _infer_kind(*, issues: list[OpenIssue], rows: list[MappingRow]) -> ReviewQuestionKind:
    issue_types = {i.issue_type for i in issues}
    if IssueType.JOIN_UNKNOWN in issue_types:
        return ReviewQuestionKind.JOIN_KEYS
    if IssueType.MISSING_SOURCE_FIELD in issue_types or IssueType.AMBIGUOUS_MAPPING in issue_types:
        return ReviewQuestionKind.SOURCE_FIELDS
    if IssueType.CONFLICTING_EVIDENCE in issue_types or IssueType.SCHEMA_MISMATCH in issue_types:
        return ReviewQuestionKind.CONFIRM_ROW
    if IssueType.MISSING_AK_DEFINITION in issue_types:
        return ReviewQuestionKind.OTHER

    rule_types = {r.rule_type for r in rows}
    if RuleType.LOOKUP in rule_types:
        for r in rows:
            if r.rule_type == RuleType.LOOKUP and (not r.lookup_tables):
                return ReviewQuestionKind.LOOKUP_TABLE
            if r.rule_type == RuleType.LOOKUP and r.join_condition and bool(getattr(r.join_condition, "is_unknown", False)):
                return ReviewQuestionKind.JOIN_KEYS
    if RuleType.SUBSTRING in rule_types:
        return ReviewQuestionKind.TRANSFORMATION
    if RuleType.CASE in rule_types or RuleType.IF_ELSE in rule_types:
        return ReviewQuestionKind.MULTI_RULE_SPLIT

    return ReviewQuestionKind.OTHER


def _default_answer_spec(*, kind: ReviewQuestionKind, priority: QuestionPriority, has_options: bool) -> AnswerSpec:
    required = priority == QuestionPriority.P0
    if kind == ReviewQuestionKind.JOIN_KEYS:
        return AnswerSpec(
            answer_format=AnswerFormat.JOIN_KEY_PICKER,
            is_required=required,
            allow_multi=True,
            placeholder="Provide explicit join keys (left = right).",
        )
    if kind == ReviewQuestionKind.CONFIRM_ROW:
        return AnswerSpec(answer_format=AnswerFormat.BOOLEAN, is_required=required, allow_multi=False)
    if kind == ReviewQuestionKind.SOURCE_FIELDS:
        if has_options:
            return AnswerSpec(answer_format=AnswerFormat.SINGLE_SELECT, is_required=required, allow_multi=False)
        return AnswerSpec(
            answer_format=AnswerFormat.COLUMN_PICKER,
            is_required=required,
            allow_multi=True,
            placeholder="Pick the correct source column(s).",
        )
    if kind == ReviewQuestionKind.RULE_TYPE:
        return AnswerSpec(answer_format=AnswerFormat.RULE_TYPE_SELECT, is_required=required, allow_multi=False)
    return AnswerSpec(answer_format=AnswerFormat.TEXT, is_required=required, allow_multi=False)


def _target_key(c: ColumnRef) -> tuple[str, str]:
    return (c.entity_id, c.column_name)


def _dedupe_key(*, target: ColumnRef | None, kind: ReviewQuestionKind) -> tuple[str, str, str]:
    if not target:
        return ("", "", kind.value)
    return (target.entity_id, target.column_name, kind.value)


def _merge_question(a: ReviewQuestion, b: ReviewQuestion) -> ReviewQuestion:
    priority = a.priority if _priority_rank(a.priority) <= _priority_rank(b.priority) else b.priority
    return ReviewQuestion(
        question_id=a.question_id,
        priority=priority,
        kind=a.kind,
        question_candidate_id=a.question_candidate_id or b.question_candidate_id,
        issue_ids=sorted(set(a.issue_ids) | set(b.issue_ids)),
        row_ids=sorted(set(a.row_ids) | set(b.row_ids)),
        target_column=a.target_column or b.target_column,
        question_text=a.question_text or b.question_text,
        context_summary=a.context_summary or b.context_summary,
        evidence_refs=a.evidence_refs or b.evidence_refs,
        answer_spec=a.answer_spec,
        options=a.options or b.options,
    )


def _candidate_options(rows: Iterable[MappingRow], *, top_n: int) -> list[SelectOption]:
    candidates: list[CandidateSource] = []
    for r in rows:
        if r.candidate_sources_topk:
            candidates.extend(r.candidate_sources_topk)

    deduped: dict[tuple[str, str], CandidateSource] = {}
    for c in candidates:
        key = (c.source_entity.entity_id, c.source_column_name)
        if key not in deduped or c.score > deduped[key].score:
            deduped[key] = c

    ranked = sorted(deduped.values(), key=lambda c: c.score, reverse=True)[: max(0, top_n)]
    out: list[SelectOption] = []
    for i, c in enumerate(ranked, start=1):
        out.append(
            SelectOption(
                option_id=f"SRC_{i}",
                label=f"{c.source_entity.entity_id}.{c.source_column_name} (score={c.score:.2f})",
                value={
                    "source_entity": c.source_entity.model_dump(),
                    "source_column_name": c.source_column_name,
                    "score": c.score,
                },
            )
        )
    return out


def _iter_rows_for_target(step2_state: Step2State, target: ColumnRef) -> list[MappingRow]:
    return [
        r
        for r in step2_state.column_mappings
        if r.target_table.entity_id == target.entity_id and r.target_column_name == target.column_name
    ]


def _iter_issues_for_target(step2_state: Step2State, target: ColumnRef) -> list[OpenIssue]:
    return [i for i in step2_state.open_issues if _target_key(i.target_column) == _target_key(target)]


def question_from_candidate(*, step2_state: Step2State, candidate, issue_lookup: dict[str, OpenIssue]) -> ReviewQuestion:
    target = candidate.target_column
    rows = _iter_rows_for_target(step2_state, target)
    issues = _iter_issues_for_target(step2_state, target)

    kind = _infer_kind(issues=issues, rows=rows)
    options_top_n = max(0, int(getattr(config, "STEP3_SOURCE_OPTIONS_TOP_N", 5)))
    options = _candidate_options(rows, top_n=options_top_n) if kind == ReviewQuestionKind.SOURCE_FIELDS else []

    issue_ids = set(i.issue_id for i in issues)
    for r in rows:
        issue_ids |= set(r.open_issue_ids)
    issue_ids = {i for i in issue_ids if i in issue_lookup}

    priority = _safe_step3_priority(getattr(candidate.priority, "value", str(candidate.priority)))
    answer_spec = _default_answer_spec(kind=kind, priority=priority, has_options=bool(options))

    return ReviewQuestion(
        question_id=candidate.question_id,
        priority=priority,
        kind=kind,
        question_candidate_id=candidate.question_id,
        issue_ids=sorted(issue_ids),
        row_ids=sorted({r.row_id for r in rows}),
        target_column=target,
        question_text=candidate.question_text,
        context_summary=candidate.context_summary,
        evidence_refs=candidate.evidence_refs,
        answer_spec=answer_spec,
        options=options,
    )


def question_from_issue(*, step2_state: Step2State, issue: OpenIssue) -> ReviewQuestion:
    target = issue.target_column
    rows = _iter_rows_for_target(step2_state, target)
    issues = [issue]
    kind = _infer_kind(issues=issues, rows=rows)

    options_top_n = max(0, int(getattr(config, "STEP3_SOURCE_OPTIONS_TOP_N", 5)))
    options = _candidate_options(rows, top_n=options_top_n) if kind == ReviewQuestionKind.SOURCE_FIELDS else []
    priority = _severity_to_priority(issue.severity)
    answer_spec = _default_answer_spec(kind=kind, priority=priority, has_options=bool(options))

    question_text = (issue.suggested_question or "").strip()
    if not question_text:
        if kind == ReviewQuestionKind.JOIN_KEYS:
            question_text = f"Provide the join keys required to populate {target.entity_id}.{target.column_name}."
        elif kind == ReviewQuestionKind.SOURCE_FIELDS:
            question_text = f"Select the correct source column(s) for {target.entity_id}.{target.column_name}."
        else:
            question_text = f"Clarify how to populate {target.entity_id}.{target.column_name}."

    context = issue.message.strip() if issue.message else None

    return ReviewQuestion(
        question_id=f"Q_{issue.issue_id}",
        priority=priority,
        kind=kind,
        question_candidate_id=None,
        issue_ids=[issue.issue_id],
        row_ids=sorted({r.row_id for r in rows if issue.issue_id in set(r.open_issue_ids)} or {r.row_id for r in rows}),
        target_column=target,
        question_text=question_text,
        context_summary=context,
        evidence_refs=issue.evidence_refs,
        answer_spec=answer_spec,
        options=options,
    )


def sort_questions(questions: list[ReviewQuestion]) -> list[ReviewQuestion]:
    def _k(q: ReviewQuestion):
        t = q.target_column
        return (
            _priority_rank(q.priority),
            (t.entity_id if t else ""),
            (t.column_name if t else ""),
            q.kind.value,
            q.question_id,
        )

    return sorted(questions, key=_k)


def build_review_questions_deterministic(*, step2_state: Step2State) -> list[ReviewQuestion]:
    """
    Build Step 3 review questions deterministically (no LLM).
    """
    issue_lookup = {i.issue_id: i for i in step2_state.open_issues}

    questions_by_key: dict[tuple[str, str, str], ReviewQuestion] = {}
    question_ids_seen: set[str] = set()
    issue_ids_covered_by_candidates: set[str] = set()

    for qc in step2_state.question_candidates:
        q = question_from_candidate(step2_state=step2_state, candidate=qc, issue_lookup=issue_lookup)
        question_ids_seen.add(q.question_id)
        issue_ids_covered_by_candidates.update(q.issue_ids or [])
        key = _dedupe_key(target=q.target_column, kind=q.kind)
        questions_by_key[key] = _merge_question(questions_by_key[key], q) if key in questions_by_key else q

    for issue in step2_state.open_issues:
        if issue.issue_id in issue_ids_covered_by_candidates:
            continue
        if f"Q_{issue.issue_id}" in question_ids_seen:
            continue
        q = question_from_issue(step2_state=step2_state, issue=issue)
        key = _dedupe_key(target=q.target_column, kind=q.kind)
        questions_by_key[key] = _merge_question(questions_by_key[key], q) if key in questions_by_key else q

    # Safety net: ensure unique question_id across final list even if different dedupe keys collide.
    by_question_id: dict[str, ReviewQuestion] = {}
    for q in sort_questions(list(questions_by_key.values())):
        if q.question_id in by_question_id:
            by_question_id[q.question_id] = _merge_question(by_question_id[q.question_id], q)
        else:
            by_question_id[q.question_id] = q
    return sort_questions(list(by_question_id.values()))
