"""The agent's tools: four reads and four writes, thin adapters over plain `tools/` functions.

Descriptions are the model-facing contract. Tools return `ToolReturn` with a data card. The three
lasting or account-wide writes ask for approval through PydanticAI's own `ApprovalRequired`."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import asyncpg
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from arno.agent.deps import AgentDeps
from arno.config import get_settings
from arno.db.conn import Connection, connect
from arno.tools import projection as projection_tools
from arno.tools import read as read_tools
from arno.tools import sandbox as sandbox_tools
from arno.tools import write as write_tools
from arno.tools.projection import ProjectChange, ProjectHypothetical
from arno.tools.write import Movement, MovementChanges, SetupData

logger = logging.getLogger("arno.agent.toolset")


def _card_data(card: dict[str, Any]) -> list[DataChunk]:
    return [DataChunk(type="data-card", data=card)]


async def run_query(ctx: RunContext[AgentDeps], sql: str) -> ToolReturn:
    """Read-only PostgreSQL (2-second timeout, at most 1,000 rows): the way to read the ledger.
    Balances, spending, movements, schedules, goals, who owes what, memories, imports, and
    conversation search are all here. Use the exact view columns below. For a table, read its
    columns from information_schema.columns before the first query instead of guessing. For a
    visual summary, select label and metric_paise (optionally baseline_paise); for a chart select
    label and amount_paise. For a citable movement select movement_id, payee_name or description,
    amount_paise, and date; cite its exact ID as [movement:mov_...].

    Views (prefer these):
    - v_legs: movement_id, transfer_id, date, movement_kind, description, from_account_id,
      from_account_name, from_account_kind, from_account_liquid, to_account_id, to_account_name,
      to_account_kind, to_account_liquid, amount_paise, category_id, category_name, payee_id,
      payee_name, note, expected_date, is_reversal, is_reversed
    - v_spending: movement_id, date, description, transfer_id, category_id, category_name,
      payee_id, payee_name, amount_paise
    - v_accounts: id, kind, ledger_account_id, name, institution, last4, statement_balance_paise,
      statement_balance_as_of, liquid, credit_limit_paise, statement_day, due_day, rate_bps,
      tenure_months, emi_schedule_id, active, created_at, updated_at, balance_paise,
      emi_rate_bps, emi_fee_paise, finance_rate_bps, min_due_bps, min_due_floor_paise
    - v_balances: account_id, name, kind, liquid, as_of, balance_paise (one row per day)
    - v_owed: payee_id, display_name, account_id, balance_paise, direction (owes_you, you_owe,
      settled), next_expected_date
    - v_movement_state: movement_id, is_reversal, is_reversed, is_effective

    Ledger tables:
    - movements: money events by IST date, kind, and reversal parent link
    - captures: chat, voice, photo, and statement-line evidence linked to movements

    Planning:
    - schedules: recurring income, obligation, installment, and goal-contribution rules
    - schedule_occurrences: materialized due dates with frozen expectations
    - schedule_payments: explicit allocations from movements to occurrences
    - series_detections: recurring spend and income series detected from payment history
    - goals: savings targets linked to each goal account
    - scheduled_questions: user-scheduled recurring agent prompts

    Import:
    - imports: statement upload batches, coverage periods, stated balances, and status
    - statement_lines: parsed statement rows with dedupe hash and match state

    Other:
    - payees: people, merchants, and organizations (person payees own a person account)
    - categories: spending categories and reducibility flags
    - category_rules: learned payee-to-category matching rules
    - baselines: typical daily spend per category (estimated or computed)
    - preferences: remembered user preferences, facts, and money rules
    - conversations: chat threads and snapshots
    - messages: chat thread turns with full-text search index (simple config)
    - settings: single-row instance config (currency, time zone, cash floor, horizon)

    Conventions:
    - Weekday: to_char(date, 'Dy') in SQL; never work one out yourself.
    - Account IDs are acc_* everywhere; all money amounts are integer paise.
    - Signs: v_legs is positive from -> to; v_spending is + into spending, - out;
      v_accounts.balance_paise and v_balances are cumulative net (+ asset or receivable, - card
      or loan debt); v_owed is + they owe you, - you owe them; statement_lines is signed credit
      minus debit (- debit, + credit).
    - Reversals: a reversal is its own movement with opposite legs, so raw sums net out; filter
      with is_reversed / is_reversal or v_movement_state when counting or listing."""
    try:
        payload = await read_tools.run_query(sql)
    except (asyncpg.QueryCanceledError, TimeoutError) as exc:
        raise ModelRetry("The query hit the 2-second statement timeout.") from exc
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    except asyncpg.PostgresError as exc:
        raise ModelRetry(str(exc).strip()) from exc
    payload["_card"] = {
        "card": "query",
        "sql": payload["sql"],
        "row_count": payload["row_count"],
        "truncated": payload["truncated"],
    }
    card = payload.pop("_card")
    return ToolReturn(return_value=payload, metadata=_card_data(card))


async def project(
    ctx: RunContext[AgentDeps],
    horizon_days: int | None = None,
    hypothetical: ProjectHypothetical | None = None,
    changes: list[ProjectChange] | None = None,
) -> ToolReturn:
    """Forecast the user's cash against their floor (the minimum balance they want to keep). Returns
    the median path, the P10-P90 band, the floor probability, goal completion, and the data-quality
    notes. Also returns safe_to_spend, the largest amount that can be spent today with the forecast
    still passing, or null when no balance is known. Pass changes, a list of 1 to 12 dated what-ifs
    (stop_income, pause_schedule, change_amount, add_recurring, or one_time), to model income, bill,
    schedule, or goal-contribution scenarios without saving anything; the result's scenario key
    holds changes (each change's label and occurrence count), assumptions, runway_days until the
    first floor crossing or null, holds and reason when no purchase is passed, and baseline, the
    same forecast without the changes, so the before figures need no second call. Combine changes
    with hypothetical to judge a purchase under those conditions. With a hypothetical purchase, also
    returns the yes, yes-but, or no verdict and the tradeoffs. Never give your own verdict: the
    engine decides. With no verdict (no liquid account has a known balance), the curve starts from
    an unknown balance: say what is missing instead of quoting its probabilities or balances. State
    the data_quality notes in your answer. A rare large day caused by a one-off payment can be
    marked one-off so the projection stops expecting it again. horizon_days is a minimum; the engine
    always looks at least 90 days, as far as a goal deadline, and at least 90 days past the latest
    scenario change."""
    if horizon_days is not None and not 1 <= horizon_days <= 3650:
        raise ModelRetry("horizon_days is 1 to 3650")
    purchase = hypothetical.model_dump(exclude_none=True, mode="json") if hypothetical else None
    raw_changes = (
        [change.model_dump(exclude_none=True, mode="json") for change in changes]
        if changes is not None
        else None
    )
    async with connect() as conn:
        try:
            result = await projection_tools.project(
                conn,
                horizon_days=horizon_days,
                hypothetical=hypothetical,
                changes=changes,
            )
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
    payload = projection_tools.summary(result)
    # The card echoes its own arguments so it can download a workbook of this same request.
    request: dict[str, Any] = {"horizon_days": horizon_days}
    if purchase is not None:
        request["hypothetical"] = {
            key: purchase[key]
            for key in (
                "amount_paise",
                "date",
                "method",
                "emi_months",
                "account",
                "emi_rate_percent",
                "emi_fee_paise",
                "no_cost",
            )
            if purchase.get(key) not in (None, False)
        }
    if raw_changes is not None:
        request["changes"] = raw_changes
    card = projection_tools.card(result, request)
    return ToolReturn(return_value=payload, metadata=_card_data(card))


async def run_python(
    ctx: RunContext[AgentDeps], code: str, attachment_ids: list[str] | None = None
) -> dict[str, Any]:
    """Run Python in an isolated sandbox for modelling the ledger cannot do: amortization, compound
    growth, tax or what-if arithmetic, and reading an uploaded file. It has no database, no
    network, and no clock; read the data first with run_query and paste what you need into the
    script. Print what you want to see; write a JSON-serializable result to result.json to get it
    back as result_json. For a scenario comparison, result_json.scenarios can contain computed
    rows with label, tenure_months, prepayment_paise, monthly_paise, interest_paise, and
    total_paise. attachment_ids lists uploaded files (att_...) to place in the script's
    working directory under their own filenames. Any figure you state must come from the output."""
    try:
        async with connect() as conn:
            return await sandbox_tools.run_python(
                conn,
                code=code,
                attachment_ids=attachment_ids or [],
                sandbox_url=get_settings().sandbox_url,
            )
    except sandbox_tools.SandboxInputError as exc:
        raise ModelRetry(str(exc)) from exc


def build_read_toolset() -> FunctionToolset[AgentDeps]:
    return FunctionToolset(tools=[run_query, project, run_python], timeout=60)


# Writes --------------------------------------------------------------------------------------


def _ask_first(ctx: RunContext[AgentDeps], needed: bool) -> None:
    """Stop for the user's tap when `needed`; count the offer and, on the resumed call, the yes."""
    if not needed:
        return
    if not ctx.tool_call_approved:
        ctx.deps.approvals_offered += 1
        raise ApprovalRequired
    ctx.deps.approvals += 1


async def _call_write(
    fn: Callable[[Connection], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    from arno.ledger.errors import CommitOutcomeUnknownError, LedgerError

    async with connect() as conn:
        try:
            return await fn(conn)
        except write_tools.ToolInputError as exc:
            raise ModelRetry(str(exc)) from exc
        except CommitOutcomeUnknownError as exc:
            return {
                "status": "outcome_unknown",
                "committed": "unknown",
                "message": (
                    f"{exc.message} Tell the user the outcome is unknown and to verify "
                    "whether the change was recorded; do not call this write tool again."
                ),
            }
        except Exception as exc:  # noqa: BLE001 - ledger errors teach the model
            if isinstance(exc, LedgerError):
                raise ModelRetry(exc.message) from exc
            raise


def _write_return(payload: dict[str, Any]) -> ToolReturn:
    card = payload.get("card")
    return ToolReturn(return_value=payload, metadata=_card_data(card) if card else None)


async def record_movements(
    ctx: RunContext[AgentDeps],
    movements: list[Movement],
    attachment_id: str | None = None,
    not_duplicate: bool = False,
) -> ToolReturn:
    """Log 1 to 50 movements of money that moved, in one step with one undo_id. Each movement is
    an expense, income, transfer between your own accounts (card bill, SIP, saving to a goal),
    split (whole bill and your share; each person and their share), loan EMI (total paid), refund,
    or settlement (direction says who paid whom). Say what happened; code works out the
    transfers. Ask first if an amount is missing or a split isn't clear. `date` is ISO, omit for
    today, never a future date. `account` is optional; code uses the payee's usual account, or the
    busiest one. Pass `category` only if the user named one; otherwise pass `category_guess`.
    Amounts are rupees in `amount_rupees`, never paise. Pass `payee` only when they name a
    merchant or a person, never a placeholder like "Unknown shop". For a multi-row screenshot or
    message, put every row in this one call; pass `attachment_id` when the rows came from one
    uploaded image, and `upi_ref` per row when visible. A possible duplicate of an existing entry
    comes back before anything is written; pass not_duplicate=true only after the user says it
    is a separate payment.
    Example: {"movements": [{"kind": "expense", "amount_rupees": 450, "payee": "Swiggy",
    "category": "Food"}]}."""
    payload = await _call_write(
        lambda conn: write_tools.record_movements(
            conn,
            movements=[m.model_dump(exclude_none=True, mode="python") for m in movements],
            message_id=ctx.deps.message_id,
            conversation_id=ctx.deps.conversation_id,
            not_duplicate=not_duplicate,
            capture_kind=ctx.deps.capture_kind,
            capture_ref=ctx.deps.capture_ref,
            attachment_id=attachment_id,
            tool_call_id=ctx.tool_call_id,
        )
    )
    return _write_return(payload)


async def correct_movement(
    ctx: RunContext[AgentDeps],
    movement_id: str,
    changes: MovementChanges,
    scope: Literal["this", "future", "all"] = "this",
) -> ToolReturn:
    """Fix a logged movement: category, payee, note, expected date, date, one-off, amount, account,
    or delete it. Omit fields that stay the same. Amounts are rupees in `amount_rupees`. For a
    category or payee, scope sets how far it goes: this row, this row and future ones, or all rows
    past and future ('ramesh@okaxis is my landlord'); a wider scope asks the user to confirm.
    To match a movement to a schedule occurrence, or remove that match, pass only
    `schedule_payment`; find the movement's ID with run_query first and never invent a payment."""
    _ask_first(ctx, scope != "this")
    payload = await _call_write(
        lambda conn: write_tools.correct_movement(
            conn,
            movement_id=movement_id,
            changes=changes.model_dump(exclude_none=True, mode="python"),
            scope=scope,
            message_id=ctx.deps.message_id,
        )
    )
    return _write_return(payload)


async def update_setup(
    ctx: RunContext[AgentDeps],
    entity: Literal[
        "account",
        "balance",
        "schedule",
        "goal",
        "category",
        "payee",
        "settings",
        "scheduled_question",
        "baseline",
        "preference",
    ],
    action: Literal["add", "update", "archive"],
    data: SetupData,
) -> ToolReturn:
    """Change setup: accounts, balances, schedules, goals, categories, payees, settings (the floor),
    scheduled questions, baselines, and remembered preferences. Not for money that moved. Archiving
    anything and changing settings ask the user to confirm. Amounts are rupees in `*_rupees`
    fields, except `*_paise` fields (`emi_fee_paise`, `min_due_floor_paise`). A schedule uses an
    iCalendar RRULE; `last4` is only the last four digits; the floor is the minimum balance to
    keep; `rate_percent` is annual; `as_of` dates a stated balance; a baseline is typical daily
    spend.
    A balance is signed: a card's or a loan's `balance_rupees` is the amount owed, so ₹500 in
    credit is -500 for that card, passed once and not retried with the other sign.
    For action "add" on a card, a positive credit limit, a statement day, and a due day are
    required. If the user does not know one, leave the card pending instead of guessing. Record a
    card's EMI rate and fee in `emi_rate_percent` and `emi_fee_paise`, and its finance terms in
    `finance_rate_percent` (percent a month), `min_due_percent`, and `min_due_floor_paise`, only
    when the issuer states them. An update changes only the fields you pass.
    A preference entity is a lasting memory. A preference is how the user wants you to behave or
    talk ("Use Hinglish when I do"), a fact is true about their world ("Mira is my flatmate"), and
    a rule is how they want their money data treated ("Family transfers are essential"). add saves
    one (`text`, `kind`, and a stable `subject` for the same topic); update changes a memory's
    `text` or `kind` by `id`; archive forgets one. When a newly stated fact contradicts the
    active value, name both values and the old one's date, then save the new value with the same
    kind and subject. Never store a number the ledger owns.
    Example: {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}}."""
    _ask_first(ctx, action == "archive" or entity == "settings")
    payload = await _call_write(
        lambda conn: write_tools.update_setup_item(
            conn,
            entity=entity,
            action=action,
            data=data.model_dump(exclude_none=True, mode="python"),
            message_id=ctx.deps.message_id,
        )
    )
    return _write_return(payload)


async def undo(ctx: RunContext[AgentDeps], undo_id: str | None = None) -> ToolReturn:
    """Undo a write. With no ID, it undoes the last write in this conversation. Use the undo_id
    from a write's result for an earlier one."""
    payload = await _call_write(
        lambda conn: write_tools.undo(
            conn,
            undo_id=undo_id,
            conversation_id=ctx.deps.conversation_id,
            message_id=ctx.deps.message_id,
        )
    )
    return _write_return(payload)


def build_write_toolset() -> FunctionToolset[AgentDeps]:
    return FunctionToolset(tools=[record_movements, correct_movement, update_setup, undo])


__all__ = ["AgentDeps", "build_read_toolset", "build_write_toolset"]
