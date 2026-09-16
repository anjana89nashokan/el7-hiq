from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class QuestionWordsmithInput(BaseModel):
    question_id: str = Field(..., description="Question id to rewrite.")
    priority: str = Field(..., description="P0/P1/P2 string.")
    kind: str = Field(..., description="ReviewQuestionKind string.")
    target_table_id: str = Field(..., description="Target table id (entity_id).")
    target_column_name: str = Field(..., description="Target column name.")
    baseline_question_text: str = Field(..., description="Current question text to improve.")
    baseline_context_summary: Optional[str] = Field(default=None, description="Current context summary.")
    issue_messages: List[str] = Field(default_factory=list, description="Relevant issue messages from Step 2.")
    option_labels: List[str] = Field(default_factory=list, description="Human-readable option labels, if any.")


class QuestionWordsmithBatchRequest(BaseModel):
    items: List[QuestionWordsmithInput] = Field(default_factory=list, description="Batch items to rewrite.")


class QuestionWordsmithOutputItem(BaseModel):
    question_id: str = Field(..., description="Question id being rewritten.")
    question_text: str = Field(..., description="Rewritten question text.")
    context_summary: Optional[str] = Field(default=None, description="Rewritten/cleaned context summary.")


class QuestionWordsmithBatchOutput(BaseModel):
    items: List[QuestionWordsmithOutputItem] = Field(default_factory=list, description="Rewritten items.")

