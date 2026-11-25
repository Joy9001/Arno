"""Run the agent evals, and aggregate `pass^3`.

This is the only code path that turns `models.ALLOW_MODEL_REQUESTS` on. Tests and the rest of
CI keep it off, so a test can never spend money.

Every trial runs in its own cloned Postgres database. The task returns an `EvalOutput`; the
graders read the database after the task and before the lifecycle drops it.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic_ai import models as pydantic_models
from pydantic_ai.messages import (
    DeferredToolRequests,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.tools import DeferredToolResults
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport, ReportCase

from arno.agent.builder import build_agent
from arno.agent.deps import AgentDeps
from arno.agent.history import (
    create_thread,
    ensure_history,
    mark_run_started,
    record_user_message,
)
from arno.agent.models import model_for
from arno.agent.number_check import check_messages, classify_messages
from arno.config import REPO_ROOT, get_settings
from arno.db.conn import connect, current_database_url
from arno.demo.template import drop_database
from arno.observability import bootstrap_observability
from evals.cases import build_cases, case_meta, summarize
from evals.graders import grade_case
from evals.lifecycle import DEFAULT_TEMPLATE, build_eval_template, lifecycle_factory
from evals.models import (
    CATEGORY_LABELS,
    EvalInput,
    EvalMeta,
    EvalOutput,
    ToolAttempt,
)

RESULTS_DIR = REPO_ROOT / "evals" / "results"


# ---------------------------------------------------------------------------
# One trial


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_." else "-" for char in value)[:80]


def _reply(result: Any) -> str:
    output = result.output
    if isinstance(output, str):
        return output
    return ""


def _tool_attempts(messages: list[ModelMessage], run_id: str) -> list[ToolAttempt]:
    attempts: list[ToolAttempt] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        if getattr(message, "run_id", None) != run_id:
            continue
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                try:
                    args = part.args_as_dict()
                except Exception:  # noqa: BLE001 - malformed args are still an attempt
                    args = {"raw": str(part.args)}
                attempts.append(ToolAttempt(tool_name=part.tool_name, args=args))
    return attempts


def _approval_attempts(output: Any) -> list[ToolAttempt]:
    if not isinstance(output, DeferredToolRequests):
        return []
    attempts: list[ToolAttempt] = []
    for part in output.approvals:
        try:
            args = part.args_as_dict()
        except Exception:  # noqa: BLE001
            args = {"raw": str(part.args)}
        attempts.append(ToolAttempt(tool_name=part.tool_name, args=args, outcome="attempted"))
    return attempts


def _completed_tools(messages: list[ModelMessage]) -> list[str]:
    names: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or part.outcome != "success":
                continue
            names.add(part.tool_name)
            if part.tool_name != "run_code" or not isinstance(part.metadata, dict):
                continue
            nested = part.metadata.get("tool_returns")
            if isinstance(nested, dict):
                names.update(
                    tool.tool_name
                    for tool in nested.values()
                    if isinstance(tool, ToolReturnPart) and tool.outcome == "success"
                )
    return sorted(names)


def _seed_long_thread(spec: Any) -> list[ModelMessage]:
    decision = spec.decision
    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=(
                        "Long-term planning note. " + decision + " Keep this decision for later."
                    )
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart("Noted. I'll keep that decision and the reasons behind it.")]
        ),
    ]
    filler = (
        "Let's go through the week's routine again. I want to keep the context long while the "
        "important decision stays early, so this turn is filler about groceries, transport, and "
        "the usual monthly errands. Nothing new to decide here."
    )
    for index in range(spec.user_turns):
        messages.append(
            ModelRequest(parts=[UserPromptPart(content=f"Filler turn {index + 1}: {filler}")])
        )
        messages.append(ModelResponse(parts=[TextPart("Noted; nothing new to decide.")]))
    return messages


async def run_turn(
    inp: EvalInput,
    *,
    meta: EvalMeta,
    model: Model | None = None,
    model_factory: Callable[[], Model] | None = None,
    model_name: str,
    project_name: str | None = None,
) -> EvalOutput:
    """One eval trial. Native Pydantic AI spans come from the process bootstrap."""
    del project_name
    return await _run_turn(
        inp, meta=meta, model=model, model_factory=model_factory, model_name=model_name
    )


async def _run_turn(
    inp: EvalInput,
    *,
    meta: EvalMeta,
    model: Model | None = None,
    model_factory: Callable[[], Model] | None = None,
    model_name: str,
) -> EvalOutput:
    """Run one trial and return what the graders need.

    The database context variable is already set by the lifecycle. The agent is built per
    trial so the context window belongs to this trial alone. A factory builds a fresh model for
    each trial. Instrumentation is
    the process bootstrap's one provider and eval project, not one provider per trial.
    """
    if model_factory is not None:
        model = model_factory()
    if model is None:
        raise ValueError("run_turn needs a model or a model_factory")
    started = datetime.now(UTC)
    started_at = started.isoformat()
    replies: list[str] = []
    message_ids: list[str] = []
    run_ids: list[str] = []
    completed_tools: list[str] = []
    attempts: list[ToolAttempt] = []
    approvals: list[ToolAttempt] = []
    number_flags: list[str] = []
    derived_flags: list[str] = []
    money_flags: list[str] = []
    cost = Decimal("0")
    ending = "complete"
    error: str | None = None
    history: list[ModelMessage] = []
    latency = 0.0
    cards_approved = 0
    # A multi-message case grades its last turn: "nothing was written" means since the user's
    # last message, not since the case began.
    last_turn_at = ""

    agent = build_agent(
        model=model,
        backup=None,
        context_window=meta.long_thread.context_window if meta.long_thread else None,
    )

    async with connect() as conn:
        thread = await create_thread(conn, title=f"Eval {inp.case_id}")
        thread_id = str(thread["id"])
        history, outside = await ensure_history(conn, thread)
        if meta.long_thread is not None:
            history = [*history, *_seed_long_thread(meta.long_thread)]

        for index, text in enumerate(inp.messages):
            message_id = f"eval_{uuid.uuid4().hex}"
            run_id = f"run_{uuid.uuid4().hex}"
            user_row = await record_user_message(
                conn, thread_id=thread_id, client_id=message_id, text=text
            )
            await mark_run_started(conn, thread_id, run_id)
            message_ids.append(str(user_row["id"]))
            run_ids.append(run_id)
            deps = AgentDeps(
                conversation_id=thread_id,
                message_id=str(user_row["id"]),
                run_id=run_id,
                flagged=outside if index == 0 else await _flagged(conn, thread_id),
                user_texts=(text,),
            )
            last_turn_at = datetime.now(UTC).isoformat()
            turn_start = time.perf_counter()
            turn_run_ids = [run_id]
            try:
                result = await agent.run(
                    text,
                    message_history=history,
                    deps=deps,
                    run_id=run_id,
                    conversation_id=thread_id,
                    timeout=300,
                    retries=2,
                )
                # A confirm card is the simulated user's yes when the case grades what lands
                # after it. A money-move trial keeps every card pending: a simulated yes would
                # erase the question the case is asking.
                approving = meta.category != "never_moves"
                for _round in range(3):
                    if not approving or not isinstance(result.output, DeferredToolRequests):
                        break
                    cards = _approval_attempts(result.output)
                    if not cards:
                        break
                    approvals.extend(cards)
                    cards_approved += len(cards)
                    # A deferred-tool resume is its own agent run, so it needs its own run id.
                    resume_run_id = f"run_{uuid.uuid4().hex}"
                    turn_run_ids.append(resume_run_id)
                    run_ids.append(resume_run_id)
                    result = await agent.run(
                        message_history=[*result.all_messages()],
                        deps=deps,
                        run_id=resume_run_id,
                        deferred_tool_results=DeferredToolResults(
                            approvals={part.tool_call_id: True for part in result.output.approvals}
                        ),
                        timeout=300,
                        retries=2,
                    )
            except Exception as exc:  # noqa: BLE001 - a failed trial is still a transcript
                error = f"{type(exc).__name__}: {exc}"
                ending = "failed"
                replies.append("")
                break
            elapsed = time.perf_counter() - turn_start
            latency += elapsed
            history = [*result.all_messages()]
            reply = _reply(result)
            replies.append(reply)
            turn_messages = [
                message for message in history if getattr(message, "run_id", None) in turn_run_ids
            ]
            completed_tools = _completed_tools(turn_messages)
            for turn_run_id in turn_run_ids:
                attempts.extend(_tool_attempts(turn_messages, turn_run_id))
            if isinstance(result.output, DeferredToolRequests):
                # A card still pending: keep it in the transcript as a card, not only a call.
                approvals.extend(_approval_attempts(result.output))
                ending = "approval_required"
            classified = classify_messages(reply, history)
            number_flags.extend(shown for shown, _label in classified)
            derived_flags.extend(shown for shown, label in classified if label == "derived")
            money_flags.extend(check_messages(reply, history, kinds=("money",)))
            usage = result.usage
            if usage.cost is not None:
                cost += usage.cost
            if isinstance(result.output, DeferredToolRequests):
                break

    return EvalOutput(
        reply=replies[-1] if replies else "",
        replies=replies,
        model_name=model_name,
        conversation_id=str(thread["id"]) if "thread" in locals() else "",
        message_ids=message_ids,
        run_ids=run_ids,
        number_flags=number_flags,
        derived_flags=derived_flags,
        money_flags=money_flags,
        cost_usd=float(cost) if cost else None,
        latency_s=latency,
        started_at=started_at,
        completed_tools=completed_tools,
        attempts=attempts,
        approvals=approvals,
        ending=ending,
        error=error,
        cards_approved=cards_approved,
        last_turn_at=last_turn_at,
        trial_db=current_database_url(),
        history_messages=len(history),
    )


async def _flagged(conn: Any, thread_id: str) -> bool:
    row = await conn.fetchrow("SELECT outside_text_at FROM conversations WHERE id = $1", thread_id)
    return bool(row and row["outside_text_at"] is not None)


# ---------------------------------------------------------------------------
# Evaluator and dataset


@dataclass
class OutcomeEvaluator(Evaluator[EvalInput, EvalOutput, EvalMeta]):
    async def evaluate(
        self, ctx: EvaluatorContext[EvalInput, EvalOutput, EvalMeta]
    ) -> dict[str, EvaluationReason]:
        db_url = str(ctx.attributes.get("trial_db_url") or current_database_url())
        meta = ctx.metadata
        if meta is None:
            raise ValueError("eval case has no metadata")
        checks = await grade_case(db_url=db_url, output=ctx.output, meta=meta)
        results: dict[str, EvaluationReason] = {}
        for check in checks:
            results[check.name] = EvaluationReason(check.passed, check.reason)
        return results


def build_dataset(
    *,
    model_name: str,
    categories: set[str] | None = None,
    tags: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
) -> tuple[Dataset[EvalInput, EvalOutput, EvalMeta], dict[str, EvalMeta]]:
    cases = build_cases()
    if categories:
        cases = [case for case in cases if case_meta(case).category in categories]
    if tags:
        cases = [case for case in cases if tags & set(case_meta(case).tags)]
    if case_ids:
        cases = [case for case in cases if case.inputs.case_id in case_ids]
    if per_category is not None:
        kept: list[Case[EvalInput, EvalOutput, EvalMeta]] = []
        counts: dict[str, int] = {}
        for case in cases:
            category = case_meta(case).category
            if counts.get(category, 0) >= per_category:
                continue
            counts[category] = counts.get(category, 0) + 1
            kept.append(case)
        cases = kept
    if limit is not None:
        cases = cases[:limit]
    meta_by_id = {case.inputs.case_id: case_meta(case) for case in cases}
    dataset = Dataset(
        name=f"arno-{_slug(model_name)}",
        cases=cases,
        evaluators=[OutcomeEvaluator()],
    )
    return dataset, meta_by_id


async def evaluate_model(
    *,
    model: Model | None = None,
    model_factory: Callable[[], Model] | None = None,
    model_name: str,
    admin_dsn: str,
    template: str = DEFAULT_TEMPLATE,
    repeat: int = 3,
    max_concurrency: int = 2,
    categories: set[str] | None = None,
    tags: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
    task_timeout: float = 300,
) -> EvaluationReport[EvalInput, EvalOutput, EvalMeta]:
    dataset, meta_by_id = build_dataset(
        model_name=model_name,
        categories=categories,
        tags=tags,
        case_ids=case_ids,
        limit=limit,
        per_category=per_category,
    )

    async def task(inp: EvalInput) -> EvalOutput:
        return await run_turn(
            inp,
            meta=meta_by_id[inp.case_id],
            model=model,
            model_factory=model_factory,
            model_name=model_name,
        )

    return await dataset.evaluate(
        task,
        name=f"arno-{_slug(model_name)}",
        repeat=repeat,
        lifecycle=lifecycle_factory(admin_dsn, template=template),
        max_concurrency=max_concurrency,
        task_timeout=task_timeout,
        progress=True,
        metadata={"model": model_name},
    )


# ---------------------------------------------------------------------------
# Aggregation


@dataclass
class CategoryScore:
    category: str
    cases: int = 0
    passed: int = 0
    trials: int = 0

    @property
    def rate(self) -> float:
        return self.passed / self.cases if self.cases else 0.0


@dataclass
class Scorecard:
    model: str
    repeat: int
    started_at: str
    finished_at: str
    categories: dict[str, CategoryScore] = field(default_factory=dict)
    number_flags: int = 0
    derived_flags: int = 0
    untraced_flags: int = 0
    number_trials: int = 0
    number_gate_failures: int = 0
    cases_with_flags: int = 0
    median_cost_usd: float | None = None
    median_latency_s: float | None = None
    total_cases: int = 0
    total_trials: int = 0
    failures: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def number_gate_passed(self) -> bool:
        return self.number_trials > 0 and self.number_gate_failures == 0

    @property
    def overall_rate(self) -> float:
        cases = sum(cat.cases for cat in self.categories.values())
        passed = sum(cat.passed for cat in self.categories.values())
        return passed / cases if cases else 0.0


def _run_passed(case: ReportCase[EvalInput, EvalOutput, EvalMeta]) -> bool:
    return not case.evaluator_failures and all(
        bool(result.value) for result in case.assertions.values()
    )


def _report_groups(
    report: EvaluationReport[EvalInput, EvalOutput, EvalMeta],
) -> list[Any]:
    """Case groups for `pass^k`, and a one-case-per-group fallback for `repeat=1`."""
    groups = report.case_groups()
    if groups is not None:
        return groups
    by_name: dict[str, Any] = {}

    def add(name: str, *, metadata: Any, run: Any = None, failure: Any = None) -> None:
        from types import SimpleNamespace

        group = by_name.setdefault(
            name,
            SimpleNamespace(name=name, metadata=metadata, runs=[], failures=[]),
        )
        if run is not None:
            group.runs.append(run)
        if failure is not None:
            group.failures.append(failure)

    for case in report.cases:
        add(case.name, metadata=case.metadata, run=case)
    for failure in report.failures:
        add(
            failure.source_case_name or failure.name,
            metadata=failure.metadata,
            failure=failure,
        )
    return list(by_name.values())


def aggregate(
    report: EvaluationReport[EvalInput, EvalOutput, EvalMeta],
    *,
    model: str,
    repeat: int,
    started_at: str,
    finished_at: str,
) -> Scorecard:
    card = Scorecard(
        model=model,
        repeat=repeat,
        started_at=started_at,
        finished_at=finished_at,
    )
    costs: list[float] = []
    latencies: list[float] = []
    groups = _report_groups(report)
    for group in groups:
        meta = group.metadata
        category = meta.category if meta is not None else "answers"
        bucket = card.categories.setdefault(category, CategoryScore(category=category))
        bucket.cases += 1
        bucket.trials += len(group.runs) + len(group.failures)
        passed = (
            len(group.runs) == repeat
            and not group.failures
            and all(_run_passed(case) for case in group.runs)
        )
        bucket.passed += int(passed)
        for failure in group.failures:
            card.failures.append(f"{group.name}: {failure.error_message}")
        for case in group.runs:
            if category == "answers":
                card.number_trials += 1
                number = case.assertions.get("number_check")
                if number is None or not bool(number.value):
                    card.number_gate_failures += 1
            output = case.output
            if isinstance(output, EvalOutput):
                if output.number_flags:
                    card.number_flags += len(output.number_flags)
                    card.derived_flags += len(output.derived_flags)
                    card.untraced_flags += len(output.number_flags) - len(output.derived_flags)
                    card.cases_with_flags += 1
                if output.cost_usd is not None:
                    costs.append(output.cost_usd)
                if output.latency_s:
                    latencies.append(output.latency_s)
            if not _run_passed(case):
                reasons = [
                    f"{name}: {result.reason or 'failed'}"
                    for name, result in case.assertions.items()
                    if not bool(result.value)
                ]
                card.failures.append(f"{case.name}: {'; '.join(reasons) or 'failed'}")
    card.total_cases = len(groups)
    card.total_trials = sum(bucket.trials for bucket in card.categories.values())
    card.median_cost_usd = statistics.median(costs) if costs else None
    card.median_latency_s = statistics.median(latencies) if latencies else None
    return card


# ---------------------------------------------------------------------------
# Scorecard formatting


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def format_scorecard(card: Scorecard) -> str:
    lines = [
        f"### {card.model}",
        "",
        f"`pass^{card.repeat}`, {card.total_cases} cases, {card.total_trials} trials.",
        "",
        f"| Category | Cases | pass^{card.repeat} | Trials |",
        "|---|---:|---:|---:|",
    ]
    for category, label in CATEGORY_LABELS.items():
        bucket = card.categories.get(category)
        if bucket is None:
            continue
        lines.append(
            f"| {label} | {bucket.cases} | {_pct(bucket.rate)} "
            f"({bucket.passed}/{bucket.cases}) | {bucket.trials} |"
        )
    lines.extend(
        [
            "",
            f"Number flags: **{card.number_flags}** "
            f"across {card.cases_with_flags} case(s): "
            f"**{card.derived_flags}** derived prose arithmetic, "
            f"**{card.untraced_flags}** untraced.",
            (
                f"Number gate: **{'PASS' if card.number_gate_passed else 'FAIL'}** "
                f"({card.number_trials - card.number_gate_failures}/{card.number_trials} "
                "answer trials traced to a tool return)."
                if card.number_trials
                else "Number gate: **not run** (no answer trials)."
            ),
        ]
    )
    if card.median_cost_usd is not None:
        lines.append(f"Median cost: **${card.median_cost_usd:.4f}** a trial.")
    else:
        lines.append("Median cost: not reported by the endpoint (unpriced model).")
    if card.median_latency_s is not None:
        lines.append(f"Median latency: **{card.median_latency_s:.1f} s** a trial.")
    lines.append(f"Run: {card.finished_at}.")
    if card.notes:
        lines.append(f"Note: {card.notes}")
    if card.failures:
        lines.append(
            f"Failures: {len(card.failures)} (transcripts written to the results directory)."
        )
    return "\n".join(lines)


def _mask_dsn_password(dsn: str) -> str:
    """Redact any password in a Postgres DSN while keeping host, port, and database name."""
    if "://" in dsn:
        parsed = urlsplit(dsn)
        auth, sep, hostport = parsed.netloc.rpartition("@")
        if sep and ":" in auth:
            user, _, _ = auth.partition(":")
            return urlunsplit(parsed._replace(netloc=f"{user}:***@{hostport}"))
        return dsn
    from arno.db.conn import conninfo_to_dict

    info = conninfo_to_dict(dsn)
    if "password" in info:
        return " ".join(f"{k}={'***' if k == 'password' else v}" for k, v in info.items())
    return dsn


def flag_transcript(
    report: EvaluationReport[EvalInput, EvalOutput, EvalMeta],
) -> str:
    """Every passing or failing reply that stated a number with no tool trace."""
    lines = ["# Number flags", ""]
    for group in _report_groups(report):
        for case in group.runs:
            if not isinstance(case.output, EvalOutput) or not case.output.number_flags:
                continue
            lines.extend([f"## {case.name}", ""])
            lines.append(f"- input: `{case.inputs.messages}`")
            lines.append(f"- reply: {case.output.reply or '(empty)'}")
            lines.append(f"- number flags: {case.output.number_flags}")
            if case.output.trial_db:
                lines.append(f"- trial db: `{_mask_dsn_password(case.output.trial_db)}`")
            if case.output.attempts:
                lines.append("- attempts:")
                for attempt in case.output.attempts:
                    lines.append(
                        f"  - `{attempt.tool_name}` {json.dumps(attempt.args, ensure_ascii=False)}"
                    )
            lines.append("")
    return "\n".join(lines)


def failure_transcript(
    report: EvaluationReport[EvalInput, EvalOutput, EvalMeta],
) -> str:
    lines = ["# Eval failures", ""]
    groups = _report_groups(report)
    for group in groups:
        failed = group.failures or [case for case in group.runs if not _run_passed(case)]
        if not failed:
            continue
        lines.extend([f"## {group.name}", ""])
        if group.failures:
            for failure in group.failures:
                lines.append(f"- setup/task failure: `{failure.error_message}`")
            lines.append("")
        for case in group.runs:
            if _run_passed(case):
                continue
            lines.append(f"### {case.name}")
            lines.append("")
            lines.append(f"- input: `{case.inputs.messages}`")
            if isinstance(case.output, EvalOutput):
                lines.append(f"- reply: {case.output.reply or '(empty)'}")
                lines.append(f"- ending: {case.output.ending}")
                if case.output.error:
                    lines.append(f"- error: `{case.output.error}`")
                lines.append(f"- number flags: {case.output.number_flags}")
                if case.output.trial_db:
                    lines.append(f"- trial db: `{_mask_dsn_password(case.output.trial_db)}`")
                if case.output.attempts:
                    lines.append("- attempts:")
                    for attempt in case.output.attempts:
                        lines.append(
                            f"  - `{attempt.tool_name}` "
                            f"{json.dumps(attempt.args, ensure_ascii=False)}"
                        )
                if case.output.approvals:
                    lines.append("- approval cards:")
                    for attempt in case.output.approvals:
                        lines.append(
                            f"  - `{attempt.tool_name}` "
                            f"{json.dumps(attempt.args, ensure_ascii=False)}"
                        )
            lines.append("- checks:")
            for name, result in case.assertions.items():
                mark = "PASS" if result.value else "FAIL"
                lines.append(f"  - {mark} `{name}`: {result.reason or ''}")
            for name, result in case.scores.items():
                lines.append(f"  - score `{name}`: {result.value} ({result.reason or ''})")
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The command entry point


def run_eval(
    *,
    model_names: list[str],
    repeat: int = 3,
    template: str = DEFAULT_TEMPLATE,
    categories: set[str] | None = None,
    tags: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
    max_concurrency: int = 2,
    results_dir: Path = RESULTS_DIR,
    note: str = "",
) -> list[Scorecard]:
    """Run the suite under the one process-wide Phoenix project."""
    bootstrap_observability()
    return asyncio.run(
        _run_eval(
            model_names=model_names,
            repeat=repeat,
            template=template,
            categories=categories,
            tags=tags,
            case_ids=case_ids,
            limit=limit,
            per_category=per_category,
            max_concurrency=max_concurrency,
            results_dir=results_dir,
            note=note,
        )
    )


async def _run_eval(
    *,
    model_names: list[str],
    repeat: int = 3,
    template: str = DEFAULT_TEMPLATE,
    categories: set[str] | None = None,
    tags: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
    max_concurrency: int = 2,
    results_dir: Path = RESULTS_DIR,
    note: str = "",
) -> list[Scorecard]:
    """Run the suite for each model. The only path that spends money."""
    pydantic_models.ALLOW_MODEL_REQUESTS = True
    cards: list[Scorecard] = []
    run_template = ""
    admin_dsn = ""
    try:
        settings = get_settings(refresh=True)
        admin_dsn = settings.database_url
        # A template name unique to this run. A fixed name lets a concurrent eval run rebuild or
        # write the template mid-run, which changes a trial's ground truth and grade.
        run_template = f"{template}_{uuid.uuid4().hex[:8]}"
        await build_eval_template(admin_dsn, template=run_template, anchor=date.today())
        for model_name in model_names:
            started = datetime.now(UTC).isoformat()
            name = model_name

            def fresh_model(model_name: str = name) -> Model:
                # Each trial gets its own model object, so the pass^3 trials stay independent.
                return model_for(model_name)

            report = await evaluate_model(
                model_factory=fresh_model,
                model_name=model_name,
                admin_dsn=admin_dsn,
                template=run_template,
                repeat=repeat,
                max_concurrency=max_concurrency,
                categories=categories,
                tags=tags,
                case_ids=case_ids,
                limit=limit,
                per_category=per_category,
            )
            finished = datetime.now(UTC).isoformat()
            card = aggregate(
                report,
                model=model_name,
                repeat=repeat,
                started_at=started,
                finished_at=finished,
            )
            card.notes = note
            cards.append(card)
            print(format_scorecard(card))
            print()
            transcripts = failure_transcript(report)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            if card.failures:
                path = results_dir / f"failures_{_slug(model_name)}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(transcripts, encoding="utf-8")
                stamped = results_dir / f"failures_{_slug(model_name)}_{stamp}.md"
                stamped.write_text(transcripts, encoding="utf-8")
                print(f"failure transcripts: {path} (also {stamped.name})")
            if card.number_flags:
                path = results_dir / f"flags_{_slug(model_name)}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                flags = flag_transcript(report)
                path.write_text(flags, encoding="utf-8")
                stamped = results_dir / f"flags_{_slug(model_name)}_{stamp}.md"
                stamped.write_text(flags, encoding="utf-8")
                print(f"number-flag transcripts: {path} (also {stamped.name})")
        return cards
    finally:
        if admin_dsn and run_template:
            try:
                await drop_database(admin_dsn, run_template)
            except Exception:  # noqa: BLE001 - cleanup must not hide the run result
                pass
        # The library's flag is a plain writable global, but pyrefly infers Literal[True] from it.
        pydantic_models.ALLOW_MODEL_REQUESTS = False  # pyrefly: ignore[bad-assignment]


def dry_run(
    *,
    categories: set[str] | None = None,
    tags: set[str] | None = None,
    case_ids: set[str] | None = None,
    limit: int | None = None,
    per_category: int | None = None,
) -> dict[str, int]:
    cases = build_cases()
    if categories:
        cases = [case for case in cases if case_meta(case).category in categories]
    if tags:
        cases = [case for case in cases if tags & set(case_meta(case).tags)]
    if case_ids:
        cases = [case for case in cases if case.inputs.case_id in case_ids]
    if per_category is not None:
        kept = []
        counts: dict[str, int] = {}
        for case in cases:
            category = case_meta(case).category
            if counts.get(category, 0) >= per_category:
                continue
            counts[category] = counts.get(category, 0) + 1
            kept.append(case)
        cases = kept
    if limit is not None:
        cases = cases[:limit]
    return summarize(cases)


__all__ = [
    "CategoryScore",
    "OutcomeEvaluator",
    "Scorecard",
    "aggregate",
    "build_dataset",
    "dry_run",
    "evaluate_model",
    "failure_transcript",
    "format_scorecard",
    "run_eval",
    "run_turn",
]
