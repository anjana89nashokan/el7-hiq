from google.adk.tools import FunctionTool

from judges.h1_requirement.post_judge import run_post_judge_h1
from judges.h1_requirement.pre_judge import run_pre_judge_h1

pre_judge_h1_tool = FunctionTool(func=run_pre_judge_h1)
post_judge_h1_tool = FunctionTool(func=run_post_judge_h1)

__all__ = [
    "post_judge_h1_tool",
    "pre_judge_h1_tool",
    "run_post_judge_h1",
    "run_pre_judge_h1",
]
