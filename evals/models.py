"""Typed inputs, metadata, and outputs for the L4 agent evals.

The suite grades outcomes, so the output carries the reply, the write attempts, the number
check, and the run's cost and latency. The metadata tells each grader what this case expects
and how a trial database must be seeded before the turn.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "answers",
    "writes",
    "honesty",
    "injection",
    "advice",
    "never_moves",
    "long_threads",
]

CATEGORY_LABELS: dict[str, str] = {
    "answers": "Answers and what-ifs",
    "writes": "Writes",
    "honesty": "Honesty when data is missing",
    "injection": "Injection",
    "advice": "Advice boundary",
    "never_moves": "Never moving money / passwords",
    "long_threads": "Long threads after a summary",
}


class EvalInput(BaseModel):
    case_id: str
    messages: list[str]


class InjectionSpec(BaseModel):
    note_id: str
    source: Literal["payee", "statement", "photo"]
    text: str
    target: Literal[
        "preference",
        "settings",
        "schedule",
        "goal",
        "movement",
        "correction",
        "move_money",
        "password",
        "exfiltration",
        "system_prompt",
    ]


class LongThreadSpec(BaseModel):
    decision: str
    user_turns: int = 48
    context_window: int = 6_000
    expected_terms: list[list[str]] = Field(default_factory=list)


class EvalMeta(BaseModel):
    category: Category
    tags: list[str] = Field(default_factory=list)
    expected: dict[str, Any] = Field(default_factory=dict)
    injection: InjectionSpec | None = None
    long_thread: LongThreadSpec | None = None
    judge_rubric: str | None = None


class ToolAttempt(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    outcome: Literal["attempted", "approved"] = "attempted"


class EvalOutput(BaseModel):
    reply: str
    replies: list[str] = Field(default_factory=list)
    model_name: str = ""
    conversation_id: str = ""
    message_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    number_flags: list[str] = Field(default_factory=list)
    money_flags: list[str] = Field(default_factory=list)
    cost_usd: float | None = None
    latency_s: float = 0.0
    started_at: str = ""
    attempts: list[ToolAttempt] = Field(default_factory=list)
    approvals: list[ToolAttempt] = Field(default_factory=list)
    ending: str = "complete"
    error: str | None = None
    trial_db: str = ""
    history_messages: int = 0


class CheckResult(BaseModel):
    name: str
    passed: bool
    reason: str = ""
