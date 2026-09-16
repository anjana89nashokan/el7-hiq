"""
FinalValidatorAndExporterAgent (Step 4) - deterministic validation/export placeholder.

In the agreed Step 4 design, this stage:
  - runs strict validation of final mapping references
  - packages final artifacts and (later) exports Excel

For now, we persist Step4State JSON and record unresolved items/warnings; Excel export will be added later.
"""

from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.mapping_apply_review.models import IssuePlan, IssueResolution, ManualAction, WarningItem
from agents.mapping_generation.models import MappingRow, Step2State
from agents.mapping_ingestion.models import SharedState
from agents.mapping_review.models import Step3State
from utils.step4_final_validator_utils import finalize_post_apply

final_validator_exporter_agent = SequentialAgent(
    name="final_validator_exporter_agent",
    sub_agents=[],
    description="Step 4 sub-agent placeholder for validation + (future) Excel export.",
)

def run_final_validator_exporter_agent(
    *,
    shared_state: SharedState,
    step2_state: Step2State,
    step3_state: Step3State,
    rows_by_id: dict[str, MappingRow],
    warnings: list[WarningItem],
    manual_actions_by_row: dict[str, list[ManualAction]],
    issue_plans: list[IssuePlan],
) -> tuple[list[WarningItem], dict[str, list[ManualAction]], list[IssueResolution], dict[str, bool]]:
    """
    Subagent C runtime (deterministic):
      - schema validation (Step 1)
      - issue ledger (Step 2 open_issues, plus Step 3 trace links)
      - export deferred
    """
    return finalize_post_apply(
        shared_state=shared_state,
        step2_state=step2_state,
        step3_state=step3_state,
        rows_by_id=rows_by_id,
        warnings=warnings,
        manual_actions_by_row=manual_actions_by_row,
        issue_plans=issue_plans,
    )


__all__ = ["final_validator_exporter_agent", "run_final_validator_exporter_agent"]
