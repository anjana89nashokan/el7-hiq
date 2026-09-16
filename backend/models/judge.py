from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from pydantic import BaseModel, Field


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class JudgeVerdict(str, Enum):
    """
    Three-level verdict system.
    PASS   — forward to BSA with no additional annotation
    WARN   — forward to BSA with highlighted concerns; does not block
    BLOCK  — return to originating agent; does not consume a BSA review cycle
    """

    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class RuleVerdict(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class RuleScore(BaseModel):
    """Score produced by a single judge rule evaluation."""

    rule_id: str
    rule_name: str
    verdict: RuleVerdict
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    evidence: str
    citations: list[str] = Field(
        default_factory=list,
        description="BRD section refs or field names that support this verdict",
    )
    blocking: bool = False
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable fixes if verdict is WARN or FAIL",
    )


class JudgeEvaluation(BaseModel):
    """Complete evaluation produced by a judge run."""

    evaluation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    phase: str = "requirement"
    checkpoint: str = "H1"
    judge_mode: str
    verdict: JudgeVerdict
    overall_score: float = Field(ge=0.0, le=1.0)
    rule_scores: list[RuleScore]
    blocking_rules: list[str]
    warnings: list[str]
    summary: str
    recommendation: str
    evaluated_at: str = Field(default_factory=_utc_now_iso)
    judge_model: str
    evaluation_latency_ms: int = 0


class RevisionDirective(BaseModel):
    """
    Structured instruction set produced by the Post-Judge.
    The RequirementInterpreter uses this — not the raw BSA feedback text —
    to know exactly what to fix in the next revision.
    """

    directive_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    source: str
    failed_rules: list[str]
    bsa_feedback_raw: str | None = None
    structured_fixes: list[dict[str, Any]]
    priority_order: list[str]
    context_additions: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context to inject into the re-run (e.g. resolved ambiguity)",
    )
    created_at: str = Field(default_factory=_utc_now_iso)
