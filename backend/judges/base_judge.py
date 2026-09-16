from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import structlog
except Exception:  # pragma: no cover - fallback for lean test envs
    import logging

    class _StructlogShim:
        @staticmethod
        def get_logger():
            class _ShimLogger:
                def __init__(self):
                    self._logger = logging.getLogger(__name__)

                def _format(self, msg, kwargs):
                    if not kwargs:
                        return msg
                    kw_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
                    return f"{msg} {kw_str}"

                def info(self, msg, *args, **kwargs):
                    self._logger.info(self._format(msg, kwargs), *args)

                def exception(self, msg, *args, **kwargs):
                    self._logger.exception(self._format(msg, kwargs), *args)

                def error(self, msg, *args, **kwargs):
                    self._logger.error(self._format(msg, kwargs), *args)

                def warning(self, msg, *args, **kwargs):
                    self._logger.warning(self._format(msg, kwargs), *args)

                def debug(self, msg, *args, **kwargs):
                    self._logger.debug(self._format(msg, kwargs), *args)

            return _ShimLogger()

    structlog = _StructlogShim()

try:  # pragma: no cover - import safety for unit tests
    from google.cloud import aiplatform  # noqa: F401
    import vertexai
    from vertexai.generative_models import GenerationConfig, GenerativeModel
except Exception:  # pragma: no cover
    vertexai = None
    GenerativeModel = Any  # type: ignore[assignment]
    GenerationConfig = None

from config.settings import config
from models.judge import JudgeEvaluation, JudgeVerdict, RuleScore, RuleVerdict

logger = structlog.get_logger()


class AuditEventType(str, Enum):
    AGENT_INVOKED = "AGENT_INVOKED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    JUDGE_VERDICT_OVERRIDDEN = "JUDGE_VERDICT_OVERRIDDEN"
    FEEDBACK_APPLIED = "FEEDBACK_APPLIED"


@dataclass
class AuditTrail:
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, event_type: AuditEventType | str, session_id: str, **payload: Any) -> None:
        event = {
            "event_type": event_type.value if isinstance(event_type, AuditEventType) else str(event_type),
            "session_id": session_id,
            "payload": payload,
            "created_at": time.time(),
        }
        self.events.append(event)
        try:
            logger.info("judge_audit_event", **event)
        except Exception:
            logger.info("judge_audit_event %s", event)

    def clear(self) -> None:
        self.events.clear()


audit_trail = AuditTrail()


class BaseJudge(ABC):
    """
    Abstract base class for all LLM judges in the pipeline.
    """

    def __init__(self):
        self.model_name = getattr(config, "GEMINI_MODEL", None) or getattr(
            config, "AGENT_MODEL", "gemini-3.1-pro-preview"
        )
        self.project_id = getattr(config, "GOOGLE_CLOUD_PROJECT", None) or getattr(
            config, "PROJECT_ID", None
        ) or getattr(config, "GOOGLE_CLOUD_PROJECT", "")
        self.location = getattr(config, "GOOGLE_CLOUD_LOCATION", None) or getattr(
            config, "LOCATION", None
        ) or getattr(config, "GOOGLE_CLOUD_LOCATION", "us-central1")
        self._model: GenerativeModel | None = None

    def _get_model(self) -> GenerativeModel:
        """
        Initialize and cache the Vertex AI Gemini model.
        """
        if self._model is None:
            if vertexai is None or GenerationConfig is None:
                raise RuntimeError("Vertex AI SDK is unavailable in this environment.")
            vertexai.init(project=self.project_id, location=self.location)
            self._model = GenerativeModel(
                model_name=self.model_name,
                generation_config=GenerationConfig(
                    temperature=0.0,
                    top_p=1.0,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )
        return self._model

    @staticmethod
    def _clean_json_text(raw_text: str) -> str:
        text = raw_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()

    @staticmethod
    def _repair_json(text: str) -> str:
        """Attempt to close unterminated strings/objects in truncated JSON."""
        # Close any unterminated string by finding unmatched quotes
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
            elif ch == "\\" and in_string:
                escaped = True
            elif ch == '"':
                in_string = not in_string
        if in_string:
            text += '"'

        # Balance braces and brackets
        stack = []
        in_string = False
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
            elif ch == "\\" and in_string:
                escaped = True
            elif ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch in ('{', '['):
                    stack.append('}' if ch == '{' else ']')
                elif ch in ('}', ']') and stack:
                    stack.pop()
        text += "".join(reversed(stack))
        return text

    async def llm_call(self, prompt: str) -> dict:
        """
        Make a single LLM call and return parsed JSON.
        """
        backoff = 0.25
        raw_text = ""
        for attempt in range(3):
            try:
                model = self._get_model()
                if hasattr(model, "generate_content_async"):
                    response = await model.generate_content_async(prompt)
                else:  # pragma: no cover - sync SDK fallback
                    response = await asyncio.to_thread(model.generate_content, prompt)
                raw_text = getattr(response, "text", "") or ""
                parsed = json.loads(self._clean_json_text(raw_text))
                return parsed if isinstance(parsed, dict) else {"items": parsed}
            except json.JSONDecodeError:
                try:
                    repaired = self._repair_json(self._clean_json_text(raw_text))
                    parsed = json.loads(repaired)
                    return parsed if isinstance(parsed, dict) else {"items": parsed}
                except json.JSONDecodeError:
                    pass
                if attempt == 2:
                    logger.error("judge_llm_parse_failed", attempt=attempt + 1)
                    return {"error": "parse_failed", "raw": raw_text}
                logger.warning("judge_llm_parse_failed", attempt=attempt + 1)
            except Exception as exc:
                logger.exception("judge_llm_call_failed", attempt=attempt + 1, error=str(exc))
                if attempt == 2:
                    return {"error": "llm_failed", "raw": raw_text, "message": str(exc)}
            await asyncio.sleep(backoff)
            backoff *= 2
        return {"error": "llm_failed", "raw": raw_text}

    def aggregate_scores(self, rule_scores: list[RuleScore]) -> tuple[float, JudgeVerdict]:
        """
        Compute overall score as weighted average of rule scores.
        """
        if not rule_scores:
            return 0.0, JudgeVerdict.BLOCK

        total_weight = sum(rule.weight for rule in rule_scores)
        if total_weight <= 0:
            normalized_weights = [1 / len(rule_scores)] * len(rule_scores)
        else:
            normalized_weights = [rule.weight / total_weight for rule in rule_scores]

        overall_score = sum(
            rule.score * normalized_weight
            for rule, normalized_weight in zip(rule_scores, normalized_weights)
        )

        has_blocking_fail = any(
            rule.blocking and rule.verdict == RuleVerdict.FAIL for rule in rule_scores
        )
        has_fail = any(rule.verdict == RuleVerdict.FAIL for rule in rule_scores)
        has_warn = any(rule.verdict == RuleVerdict.WARN for rule in rule_scores)

        if has_blocking_fail:
            return round(overall_score, 4), JudgeVerdict.BLOCK
        if has_fail or has_warn:
            return round(overall_score, 4), JudgeVerdict.WARN
        return round(overall_score, 4), JudgeVerdict.PASS

    @abstractmethod
    async def evaluate(self, *args, **kwargs):
        raise NotImplementedError
