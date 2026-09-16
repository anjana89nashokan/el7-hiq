from __future__ import annotations

from typing import Iterable, List

from .schemas import KpiScore, PerItemJudgment


COMPLETENESS_DEF = (
    "Completeness = required items present in the produced output / total required items. "
    "Measures coverage of what the layer was expected to produce."
)
HALLUCINATION_DEF = (
    "Hallucination rate = produced items that contradict the source OR are not supported by the source / "
    "total produced items.  Measures how often the layer invents content."
)
GROUNDEDNESS_DEF = (
    "Groundedness = produced items supported by cited source evidence and not contradicting it / "
    "total produced items.  Measures faithfulness to the source artifacts."
)
INSTRUCTION_DEF = (
    "Instruction adherence = items that follow the layer-specific rules (naming, types, required "
    "fields, format) / total items judged.  Measures how well the output respects the agent's spec."
)


def _safe_ratio(num: int, denom: int) -> float:
    return float(num) / float(denom) if denom > 0 else 0.0


def _required(items: Iterable[PerItemJudgment]) -> List[PerItemJudgment]:
    return [i for i in items if i.item_type == "required"]


def _produced(items: Iterable[PerItemJudgment]) -> List[PerItemJudgment]:
    return [i for i in items if i.item_type == "produced"]


def compute(items: List[PerItemJudgment]) -> dict[str, KpiScore]:
    """
    Aggregate per-item judgments into the 4 KPIs.

    The LLM emits the booleans per item; this function is pure Python so the
    final KPI numbers are reproducible from the persisted artifact.
    """
    required = _required(items)
    produced = _produced(items)

    completeness_num = sum(1 for i in required if i.present_in_output is True)
    completeness_den = len(required)

    hallucination_num = sum(
        1
        for i in produced
        if (i.contradicts_source is True) or (i.supported_by_source is False)
    )
    hallucination_den = len(produced)

    grounded_num = sum(
        1
        for i in produced
        if (i.supported_by_source is True) and (i.contradicts_source is not True)
    )
    grounded_den = len(produced)

    instr_num = sum(1 for i in items if i.follows_instructions is True)
    instr_den = len(items)

    return {
        "completeness": KpiScore(
            score=_safe_ratio(completeness_num, completeness_den),
            numerator=completeness_num,
            denominator=completeness_den,
            definition=COMPLETENESS_DEF,
        ),
        "hallucination_rate": KpiScore(
            score=_safe_ratio(hallucination_num, hallucination_den),
            numerator=hallucination_num,
            denominator=hallucination_den,
            definition=HALLUCINATION_DEF,
        ),
        "groundedness": KpiScore(
            score=_safe_ratio(grounded_num, grounded_den),
            numerator=grounded_num,
            denominator=grounded_den,
            definition=GROUNDEDNESS_DEF,
        ),
        "instruction_adherence": KpiScore(
            score=_safe_ratio(instr_num, instr_den),
            numerator=instr_num,
            denominator=instr_den,
            definition=INSTRUCTION_DEF,
        ),
    }
