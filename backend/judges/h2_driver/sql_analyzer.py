"""Deterministic SQL analysis utilities for the H2 Driver judge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TRANSFORMATION_PATTERNS = [
    r"\bSUBSTR\s*\(",
    r"\bSUBSTRING\s*\(",
    r"\bLEFT\s*\(",
    r"\bRIGHT\s*\(",
    r"\bTRIM\s*\(",
    r"\bLTRIM\s*\(",
    r"\bRTRIM\s*\(",
    r"\bUPPER\s*\(",
    r"\bLOWER\s*\(",
    r"\bREPLACE\s*\(",
    r"\bCONCAT\s*\(",
    r"\|\|",
    r"\bCOALESCE\s*\(",
    r"\bNVL\s*\(",
    r"\bIFNULL\s*\(",
    r"\bROUND\s*\(",
    r"\bFLOOR\s*\(",
    r"\bCEIL\s*\(",
    r"\bABS\s*\(",
    r"\bMOD\s*\(",
    r"\bTO_DATE\s*\(",
    r"\bTO_CHAR\s*\(",
    r"\bDATE_FORMAT\s*\(",
    r"\bFORMAT\s*\(",
    r"\bEXTRACT\s*\(",
    r"\bDATEDIFF\s*\(",
    r"\bDATEADD\s*\(",
    r"\bSUM\s*\(",
    r"\bCOUNT\s*\(",
    r"\bAVG\s*\(",
    r"\bMAX\s*\(",
    r"\bMIN\s*\(",
    r"\bCASE\b",
    r"\bWHEN\b",
    r"\bDECODE\s*\(",
    r"\bTO_NUMBER\s*\(",
    r"\bTO_VARCHAR\s*\(",
    r"\bCONVERT\s*\(",
]

_INCLUSION_OPS = {"IN", "=", ">", "<", ">=", "<=", "BETWEEN", "LIKE", "IS NULL", "EXISTS"}
_EXCLUSION_OPS = {"NOT IN", "!=", "<>", "NOT LIKE", "NOT BETWEEN", "IS NOT NULL", "NOT EXISTS"}


@dataclass
class ParsedPredicate:
    raw: str
    field_name: str | None = None
    operator: str | None = None
    values: list[str] = field(default_factory=list)
    is_compound: bool = False
    line_number: int = 0


@dataclass
class TransformationMatch:
    predicate_raw: str
    pattern_matched: str
    match_text: str
    severity: str = "block"


@dataclass
class LogicIssue:
    issue_type: str
    predicates_involved: list[str]
    description: str
    severity: str


def _strip_outer_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i < len(text) - 1:
                    balanced = False
                    break
        if balanced:
            text = text[1:-1].strip()
        else:
            break
    return text


def _split_on_top_level(text: str) -> list[str]:
    """Split a WHERE clause body on top-level AND/OR (case-insensitive)."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    quote_char = ""
    buf = []
    i = 0
    upper = text.upper()
    n = len(text)
    while i < n:
        ch = text[i]
        if in_quote:
            buf.append(ch)
            if ch == quote_char:
                in_quote = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = True
            quote_char = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            depth -= 1
            buf.append(ch)
            i += 1
            continue
        if depth == 0:
            if upper[i : i + 5] == " AND " or upper[i : i + 5] == " OR  ":
                parts.append("".join(buf).strip())
                buf = []
                i += 5
                continue
            if upper[i : i + 4] == " OR ":
                parts.append("".join(buf).strip())
                buf = []
                i += 4
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


_OPERATOR_RE = re.compile(
    r"""(?ix)
    \b(?:NOT\s+IN|IN|NOT\s+LIKE|LIKE|NOT\s+BETWEEN|BETWEEN|
        IS\s+NOT\s+NULL|IS\s+NULL|NOT\s+EXISTS|EXISTS)\b
    | (?:!=|<>|>=|<=|=|>|<)
    """,
)


def _parse_leaf(predicate_text: str, line_number: int = 0) -> ParsedPredicate:
    raw = predicate_text.strip()
    inner = _strip_outer_parens(raw)
    match = _OPERATOR_RE.search(inner)
    if not match:
        return ParsedPredicate(raw=raw, line_number=line_number)

    operator = re.sub(r"\s+", " ", match.group(0).strip()).upper()
    left = inner[: match.start()].strip()
    right = inner[match.end():].strip()

    field_name = left.strip("()").strip()

    values: list[str] = []
    if operator in {"IN", "NOT IN"}:
        right_inner = right.strip()
        if right_inner.startswith("("):
            right_inner = right_inner[1:]
        if right_inner.endswith(")"):
            right_inner = right_inner[:-1]
        values = [v.strip().strip("'").strip('"') for v in right_inner.split(",") if v.strip()]
    elif operator in {"BETWEEN", "NOT BETWEEN"}:
        m = re.match(r"(.+?)\s+AND\s+(.+)", right, flags=re.IGNORECASE)
        if m:
            values = [m.group(1).strip().strip("'\""), m.group(2).strip().strip("'\"")]
        else:
            values = [right.strip()]
    elif operator in {"IS NULL", "IS NOT NULL"}:
        values = []
    else:
        values = [right.strip().strip("'\"")]

    return ParsedPredicate(
        raw=raw,
        field_name=field_name or None,
        operator=operator,
        values=values,
        line_number=line_number,
    )


def parse_predicates(where_clause: str) -> list[ParsedPredicate]:
    """Parse a SQL WHERE clause into individual predicates."""
    if not where_clause or not where_clause.strip():
        return []

    text = where_clause.strip()
    if text.upper().startswith("WHERE "):
        text = text[6:]

    parts = _split_on_top_level(text)
    if not parts:
        parts = [text]

    predicates: list[ParsedPredicate] = []
    for idx, part in enumerate(parts, start=1):
        # If the part still contains top-level AND/OR after stripping outer parens, recurse
        stripped = _strip_outer_parens(part)
        sub_parts = _split_on_top_level(stripped)
        if len(sub_parts) > 1:
            for sub_idx, sub in enumerate(sub_parts, start=1):
                pred = _parse_leaf(sub, line_number=idx * 100 + sub_idx)
                pred.is_compound = True
                predicates.append(pred)
        else:
            predicates.append(_parse_leaf(part, line_number=idx))
    return predicates


def detect_transformations(predicates: list[ParsedPredicate]) -> list[TransformationMatch]:
    matches: list[TransformationMatch] = []
    for predicate in predicates:
        text = predicate.raw or ""
        for pattern in TRANSFORMATION_PATTERNS:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                matches.append(
                    TransformationMatch(
                        predicate_raw=predicate.raw,
                        pattern_matched=pattern,
                        match_text=match.group(0),
                        severity="block",
                    )
                )
    return matches


def detect_logic_issues(predicates: list[ParsedPredicate]) -> list[LogicIssue]:
    issues: list[LogicIssue] = []
    if not predicates:
        return issues

    by_field: dict[str, list[ParsedPredicate]] = {}
    for predicate in predicates:
        if predicate.field_name:
            by_field.setdefault(predicate.field_name.upper(), []).append(predicate)

    for field_name, preds in by_field.items():
        eq_values: set[str] = set()
        in_values_sets: list[set[str]] = []
        not_in_values_sets: list[set[str]] = []
        has_is_null = False
        has_is_not_null = False
        for predicate in preds:
            op = (predicate.operator or "").upper()
            values = {str(v).upper() for v in predicate.values}
            if op == "=":
                eq_values |= values
            elif op == "IN":
                in_values_sets.append(values)
            elif op == "NOT IN":
                not_in_values_sets.append(values)
            elif op == "IS NULL":
                has_is_null = True
            elif op == "IS NOT NULL":
                has_is_not_null = True

        if len(eq_values) > 1:
            issues.append(
                LogicIssue(
                    issue_type="always_false",
                    predicates_involved=[p.raw for p in preds if (p.operator or "").upper() == "="],
                    description=f"{field_name} cannot equal multiple distinct values simultaneously: {sorted(eq_values)}",
                    severity="block",
                )
            )

        for in_set in in_values_sets:
            for not_in_set in not_in_values_sets:
                if in_set and in_set.issubset(not_in_set):
                    issues.append(
                        LogicIssue(
                            issue_type="always_false",
                            predicates_involved=[p.raw for p in preds],
                            description=f"{field_name} IN values are entirely covered by NOT IN exclusion.",
                            severity="block",
                        )
                    )

        if has_is_null and has_is_not_null:
            issues.append(
                LogicIssue(
                    issue_type="always_true",
                    predicates_involved=[p.raw for p in preds],
                    description=f"{field_name} IS NULL OR IS NOT NULL — tautology with no filter effect.",
                    severity="warn",
                )
            )

        for predicate in preds:
            op = (predicate.operator or "").upper()
            if op == "=" and in_values_sets:
                eq_val = (predicate.values[0] if predicate.values else "").upper()
                for in_set in in_values_sets:
                    if eq_val and eq_val in in_set and len(in_set) > 1:
                        issues.append(
                            LogicIssue(
                                issue_type="redundant",
                                predicates_involved=[predicate.raw],
                                description=f"{field_name} = {eq_val} is redundant alongside an IN clause containing it.",
                                severity="warn",
                            )
                        )
                        break

        # Type mismatch heuristic: code/id field with unquoted numeric value
        if field_name.endswith("_CD") or field_name.endswith("_ID"):
            for predicate in preds:
                for raw_value in predicate.values:
                    raw = str(raw_value).strip()
                    if raw and raw.isdigit() and not (
                        f"'{raw}'" in (predicate.raw or "") or f'"{raw}"' in (predicate.raw or "")
                    ):
                        issues.append(
                            LogicIssue(
                                issue_type="type_mismatch",
                                predicates_involved=[predicate.raw],
                                description=f"{field_name} compared against unquoted numeric value '{raw}' — codes/ids are typically strings.",
                                severity="warn",
                            )
                        )
                        break

    return issues


def extract_field_names(predicates: list[ParsedPredicate]) -> list[str]:
    seen: list[str] = []
    seen_upper: set[str] = set()
    for predicate in predicates:
        if predicate.field_name and predicate.field_name.upper() not in seen_upper:
            seen.append(predicate.field_name)
            seen_upper.add(predicate.field_name.upper())
    return seen


def classify_operator_direction(predicate: ParsedPredicate) -> str:
    op = (predicate.operator or "").upper()
    if op in _INCLUSION_OPS:
        return "inclusion"
    if op in _EXCLUSION_OPS:
        return "exclusion"
    return "unknown"


def estimate_predicate_selectivity(predicate: ParsedPredicate) -> str:
    op = (predicate.operator or "").upper()
    field_name = (predicate.field_name or "").upper()
    n_values = len(predicate.values)

    if op == "IN" and n_values <= 3 and field_name.endswith("_CD"):
        return "narrow"
    if op == "NOT IN" and n_values >= 5:
        return "broad"
    if op == "IS NOT NULL":
        return "broad"
    if op in {"BETWEEN", "NOT BETWEEN"} and ("_DT" in field_name or field_name.endswith("DATE")):
        return "medium"
    if op == "LIKE":
        if any(str(v).startswith("%") for v in predicate.values):
            return "broad"
        return "medium"
    if op == "=":
        return "narrow"
    if op == "IN" and n_values <= 5:
        return "narrow"
    if op == "IN":
        return "medium"
    return "unknown"


def build_sql_analysis_report(where_clause: str) -> dict[str, Any]:
    """Run the full deterministic analysis once and serialize."""
    predicates = parse_predicates(where_clause)
    transformations = detect_transformations(predicates)
    logic_issues = detect_logic_issues(predicates)
    return {
        "parsed_predicates": [
            {
                "raw": p.raw,
                "field_name": p.field_name,
                "operator": p.operator,
                "values": p.values,
                "is_compound": p.is_compound,
                "line_number": p.line_number,
            }
            for p in predicates
        ],
        "detected_transformations": [
            {
                "predicate_raw": t.predicate_raw,
                "pattern_matched": t.pattern_matched,
                "match_text": t.match_text,
                "severity": t.severity,
            }
            for t in transformations
        ],
        "logic_issues": [
            {
                "issue_type": i.issue_type,
                "predicates_involved": i.predicates_involved,
                "description": i.description,
                "severity": i.severity,
            }
            for i in logic_issues
        ],
        "field_names_used": extract_field_names(predicates),
        "operator_directions": [
            {"raw": p.raw, "direction": classify_operator_direction(p)} for p in predicates
        ],
        "selectivity_estimates": [
            {"raw": p.raw, "selectivity": estimate_predicate_selectivity(p)} for p in predicates
        ],
    }
