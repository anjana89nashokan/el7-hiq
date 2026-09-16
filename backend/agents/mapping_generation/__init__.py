"""
Step 2: Mapping Generation package.

Why this package exists:
  - Step 1 (mapping_ingestion) produces a SharedState JSON artifact.
  - Step 2 consumes that SharedState and produces a Step2State draft mapping artifact.

Exports:
  - `step2_main_agent`: ADK structural wiring (SequentialAgent)
  - `run_step2_pipeline`: deterministic Python entrypoint used by the API / scripts
"""

from .agent import run_step2_pipeline, step2_main_agent

__all__ = ["step2_main_agent", "run_step2_pipeline"]

