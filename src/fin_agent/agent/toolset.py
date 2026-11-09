"""The agent's tools: eight read tools behind `run_code`, six write calls in front of it.

Descriptions are the model-facing contract. Read tools return `ToolReturn` with a data card; a
card from a tool called inside a script is picked up from the `run_code` return after the run
(system-design step 5).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.presentation import (
    PresentationError,
    TableInput,
    build_answer,
)
from fin_agent.agent.write_models import Movement, MovementChanges, SetupData
from fin_agent.db.conn import Connection, connect, quote_ident
from fin_agent.engine.money import amortize as amortize_fn
from fin_agent.engine.money import compound as compound_fn
from fin_agent.ledger.accounts import LoanTermsError, loan_terms
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import projection as projection_tools
from fin_agent.tools import read as read_tools
from fin_agent.tools import recaps as recaps_tools
from fin_agent.tools import write as write_tools
from fin_agent.tools.mark import clear_mark, flag_outside_text

logger = logging.getLogger("fin_agent.agent.toolset")


# Typed read arguments, so a script sees the keys and a misspelled one fails instead of being
# silently ignored (a dropped `categories` filter once returned the whole month as "food").
class SpendFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account: str | None = Field(default=None, description="Account name.")
    category: str | None = Field(default=None, description="One exact category name.")
    payee: str | None = Field(default=None, description="Payee name or UPI ID.")
    text: str | None = Field(default=None, description="Searches descriptions and notes.")


class MovementFilters(SpendFilters):
    period: str | None = Field(
        default=None,
        description=(
            'this_week, last_week, this_month, last_month, last_30_days, this_year, "2026-09", or '
            'a range "2026-09-01..2026-09-15". Weeks start Monday; an open period ends today.'
        ),
    )
    kind: str | None = Field(default=None, description="expense, income, transfer, split, ...")
    min_paise: int | None = Field(default=None, description="Smallest transfer amount, paise.")
    max_paise: int | None = Field(default=None, description="Largest transfer amount, paise.")
    include_reversed: bool = Field(
        default=False, description="Also list corrected-away entries and their reversals."
    )


class Prepay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: int = Field(gt=0, description="The lump sum, in paise.")
    at_emi: int = Field(
        default=1,
        ge=1,
        description="Paid before this EMI, counting from the next one: 1 is now, 13 is after "
        "12 more EMIs.",
    )
    reduce: Literal["tenure", "emi"] = Field(
        default="tenure", description="tenure: same EMI, finish sooner. emi: lower EMI."
    )


class ProjectHypothetical(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: int = Field(gt=0, description="The purchase amount, in paise.")
    date: str = Field(description="The purchase date, YYYY-MM-DD.")
    method: Literal["account", "card", "emi"] = Field(
        default="account",
        description="account: pay from a bank account now. card: put it on a card. "
        "emi: split it into card instalments.",
    )
    emi_months: int | None = Field(
        default=None, ge=2, le=48, description="Instalments for method=emi; 3 if omitted."
    )
    account: str | None = Field(
        default=None, description="A card name for card or emi; the best card if omitted."
    )


def _card_data(card: dict[str, Any]) -> list[DataChunk]:
    return [DataChunk(type="data-card", data=card)]


def _cards_data(cards: list[dict[str, Any]]) -> list[DataChunk] | None:
    return [DataChunk(type="data-card", data=card) for card in cards] or None


async def _read_call(
    fn: Callable[[Connection], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with connect() as conn:
        return await fn(conn)


async def _finish_read(
    ctx: RunContext[AgentDeps],
    payload: dict[str, Any],
    *,
    kind: str | None = None,
    context: dict[str, Any] | None = None,
) -> ToolReturn:
    if payload.get("outside_text"):
        async with connect() as conn:
            await flag_outside_text(conn, ctx.deps.conversation_id)
    card = payload.pop("_card", None)
    cards: list[dict[str, Any]] = []
    if kind is not None:
        registration = ctx.deps.presentation.register_read(kind, payload, context=context)
        payload["ui_results"] = registration.descriptors
        if card:
            card = {**card, "result_id": registration.primary_id}
        if card:
            cards.append(card)
        if registration.fallback:
            cards.append(registration.fallback)
    elif card:
        cards.append(card)
    return ToolReturn(return_value=payload, metadata=_cards_data(cards))


async def get_financial_context(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """The user's whole picture: today's date, settings, accounts with balances and ages, the
    schedules behind the next 30 days, goals, who owes what, remembered preferences, what setup
    is missing, and data warnings. The sandbox has no clock, so get today's date here."""
    payload = await _read_call(read_tools.get_financial_context)
    return await _finish_read(ctx, payload, kind="context")


async def query_movements(
    ctx: RunContext[AgentDeps],
    filters: MovementFilters | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> ToolReturn:
    """List individual movements with their transfers, category, payee, and evidence. Use it to
    find a row or see what's behind a number. For totals, use summarize_spending. A corrected
    entry and its reversal are hidden and not counted."""
    plain = filters.model_dump(exclude_none=True) if filters else None
    try:
        payload = await _read_call(
            lambda conn: read_tools.query_movements(conn, filters=plain, limit=limit, cursor=cursor)
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return await _finish_read(
        ctx,
        payload,
        kind="movements",
        context={
            "period": {"label": plain.get("period")} if plain and plain.get("period") else None,
            "filters": plain,
        },
    )


async def summarize_spending(
    ctx: RunContext[AgentDeps],
    group_by: Literal["category", "payee", "account", "month", "week", "none"],
    period: str,
    filters: SpendFilters | None = None,
) -> ToolReturn:
    """Totals of money spent, net of refunds, grouped however you need. Loan principal, card
    bills, investments, and money others owe never count as spending. To compare periods, call it
    twice. period is this_week, last_week, this_month, last_month, last_30_days, this_year,
    "2026-09", or a range "2026-09-01..2026-09-15" (weeks start Monday). count is the
    number of entries, with a corrected entry and its reversal left out."""
    plain = filters.model_dump(exclude_none=True) if filters else None
    try:
        payload = await _read_call(
            lambda conn: read_tools.summarize_spending(
                conn, group_by=group_by, period=period, filters=plain
            )
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return await _finish_read(
        ctx,
        payload,
        kind="spending",
        context={"group_by": group_by, "filters": plain},
    )


async def recap(ctx: RunContext[AgentDeps], period: str) -> ToolReturn:
    """The recap of a day, week, or month: what was spent against the period before, money in and
    out, where it went, the biggest change, the biggest and most frequent spends, and repeat
    charges with no schedule. The same stories the Recaps page shows. period is a recap ID
    ("month-2026-10", "week-2026-11-02", "day-2026-11-08") or yesterday, this_week, last_week,
    this_month, last_month, or "2026-10"."""
    try:
        payload = await _read_call(lambda conn: recaps_tools.recap(conn, period))
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return await _finish_read(ctx, payload)


async def run_query(ctx: RunContext[AgentDeps], sql: str) -> ToolReturn:
    """Read-only SQL for questions the typed tools can't answer, including conversation search.
    Try query_movements and summarize_spending first. Prefer the views v_spending, v_cashflow,
    v_balances, and v_owed. Tables: accounts, movements, transfer_labels, captures, imports,
    statement_lines, schedules, goals, payees, categories, category_rules, baselines, preferences,
    messages, conversations, settings. The database is PostgreSQL. For conversation search:
    messages(conversation_id, role, body_text, created_at) and
    conversations(id, title, created_at, updated_at). The role cannot write and has a 2-second
    timeout."""
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
    return await _finish_read(ctx, payload, kind="query")


async def project(
    ctx: RunContext[AgentDeps],
    horizon_days: int | None = None,
    hypothetical: ProjectHypothetical | None = None,
) -> ToolReturn:
    """Forecast the user's cash against their floor (the minimum balance they want to keep).
    Returns the median path, the P10-P90 band, the floor probability, goal completion, and the
    data-quality notes. With a hypothetical purchase, also returns the yes, yes-but, or no verdict
    and the tradeoffs. Never give your own verdict: the engine decides. With no verdict (no liquid
    account has a known balance), the curve starts from an unknown balance: say what is missing
    instead of quoting its probabilities or balances. State the data_quality
    notes in your answer. horizon_days is a minimum; the engine always looks at least 90 days and
    as far as a goal deadline."""
    if horizon_days is not None and not 1 <= horizon_days <= 3650:
        raise ModelRetry("horizon_days is 1 to 3650")
    try:
        async with connect() as conn:
            result = await projection_tools.project(
                conn,
                horizon_days=horizon_days,
                hypothetical=(hypothetical.model_dump(exclude_none=True) if hypothetical else None),
            )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    payload = projection_tools.summary(result)
    payload["_card"] = projection_tools.card(result)
    return await _finish_read(
        ctx,
        payload,
        kind="project",
        context={"card": payload["_card"]},
    )


async def amortize(
    ctx: RunContext[AgentDeps],
    loan: str | None = None,
    principal_paise: int | None = None,
    annual_rate: float | None = None,
    months: int | None = None,
    prepay: Prepay | None = None,
) -> ToolReturn:
    """Loan maths: EMI, total interest, payoff date, and the monthly schedule. Pass a loan's name
    to use its outstanding balance, rate, and real EMI, or pass the terms. Add prepay to compare
    a lump-sum prepayment, for example prepay={"amount_paise": 5000000, "at_emi": 13}. Use this
    instead of writing the formula."""
    start: date | None = ledger_today()
    if loan:
        async with connect() as conn:
            try:
                terms = await loan_terms(conn, loan)
            except LoanTermsError as exc:
                raise ModelRetry(exc.message) from exc
        principal_paise = terms["principal_paise"]
        annual_rate = terms["annual_rate"]
        months = terms["months"]
        emi_paise = terms["emi_paise"]
    else:
        emi_paise = None
        if principal_paise is None or annual_rate is None or months is None:
            raise ModelRetry("Pass a loan name, or principal_paise, annual_rate, and months")
    try:
        result = amortize_fn(
            int(principal_paise),
            float(annual_rate),
            int(months),
            emi_paise=emi_paise,
            start_date=start,
            prepay=prepay.model_dump() if prepay else None,
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    card = {"card": "amortize", "title": "Loan schedule", **result}
    registered = ctx.deps.presentation.register_calculation("amortize", result, card)
    card["result_id"] = registered.result_id
    return ToolReturn(
        return_value={**result, "ui_results": [registered.descriptor()]},
        metadata=_card_data(card),
    )


async def compound(
    ctx: RunContext[AgentDeps],
    monthly_paise: int,
    annual_rate: float,
    months: int,
    start_paise: int = 0,
) -> ToolReturn:
    """Growth of a monthly investment at an assumed annual rate. The rate is an assumption, not a
    prediction or advice: state it in your answer."""
    try:
        result = compound_fn(monthly_paise, annual_rate, months, start_paise=start_paise)
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    card = {"card": "compound", "title": "Growth at an assumed rate", **result}
    registered = ctx.deps.presentation.register_calculation("compound", result, card)
    card["result_id"] = registered.result_id
    return ToolReturn(
        return_value={**result, "ui_results": [registered.descriptor()]},
        metadata=_card_data(card),
    )


async def show_chart(
    ctx: RunContext[AgentDeps],
    type: Literal["line", "bar", "stacked_bar", "donut"],
    title: str,
    series: list[dict[str, Any]],
    unit: Literal["paise", "count", "percent"] = "paise",
    as_of: str | None = None,
) -> ToolReturn:
    """Show the user a chart: line, bar, stacked_bar, or donut. Take values from variables in your
    script, never typed by hand. Use one when a shape says more than a sentence."""
    if not series:
        raise ModelRetry("a chart needs at least one series")
    if len(series) > 8:
        raise ModelRetry("a chart takes at most 8 series")
    if type == "donut" and len(series) != 1:
        raise ModelRetry("a donut has exactly one series")
    checked: list[dict[str, Any]] = []
    for item in series:
        name = str(item.get("name") or "")
        points = item.get("points") or []
        if not name:
            raise ModelRetry("every series needs a name")
        if not isinstance(points, list) or not points:
            raise ModelRetry(f"series {name!r} has no points")
        if len(points) > 400:
            raise ModelRetry(f"series {name!r} has more than 400 points")
        cleaned: list[dict[str, Any]] = []
        for point in points:
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                raise ModelRetry("each point is {'x': str, 'y': int}")
            value = point["y"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ModelRetry("chart values must be numbers")
            if isinstance(value, float):
                if not value.is_integer():
                    raise ModelRetry(
                        "chart values must be whole numbers; round the value in your script "
                        "and pass the rounded number"
                    )
                value = int(value)
            cleaned.append({"x": str(point["x"]), "y": value})
        checked.append({"name": name, "points": cleaned})
    chart_id = f"cht_{abs(hash((title, type, str(checked)))):x}"
    spec = {
        "chart_id": chart_id,
        "type": type,
        "title": title,
        "series": checked,
        "unit": unit,
        "as_of": as_of,
    }
    card = {"card": "chart", **spec}
    registered = ctx.deps.presentation.register_calculation("chart", {"shown": True}, card)
    card["result_id"] = registered.result_id
    return ToolReturn(
        return_value={
            "chart_id": chart_id,
            "shown": True,
            "ui_results": [registered.descriptor()],
        },
        metadata=_card_data(card),
    )


async def show_table(ctx: RunContext[AgentDeps], table: TableInput) -> ToolReturn:
    """Show a table you computed in your script. Pass columns with an explicit type (money,
    number, text, or date), rows of cells, and optionally the ui_results ids it came from. Money
    cells are integer paise. At most 12 columns and 50 rows; a comparison is better as a table
    than as prose."""
    try:
        registered = ctx.deps.presentation.register_table(table)
    except PresentationError as exc:
        raise ModelRetry(str(exc)) from exc
    card = {
        "card": "result",
        "result_id": registered.result_id,
        "snapshot": registered.snapshot,
    }
    return ToolReturn(
        return_value={
            "table_id": registered.result_id,
            "shown": True,
            "ui_results": [registered.descriptor()],
        },
        metadata=_card_data(card),
    )


async def present(ctx: RunContext[AgentDeps], display: dict[str, Any]) -> ToolReturn:
    """Compose the answer from this turn's ui_results. display is a bounded tree:
    {"_type": "Stack" | "Comparison" | "Summary" | "Table" | "Chart" | "Receipt",
     "children": [...], "result_id": "res_1"}. Containers have children; leaves have a result_id
    and no other props. Use only a view the result offers. Comparison aligns sections and never
    computes a difference."""
    try:
        card, return_value = build_answer(ctx.deps.presentation, display)
    except PresentationError as exc:
        raise ModelRetry(str(exc)) from exc
    return ToolReturn(return_value=return_value, metadata=_card_data(card))


def build_read_toolset() -> FunctionToolset[AgentDeps]:
    return FunctionToolset(
        tools=[
            get_financial_context,
            query_movements,
            summarize_spending,
            recap,
            project,
            run_query,
            amortize,
            compound,
            show_chart,
            show_table,
            present,
        ],
        timeout=60,
    ).with_metadata(code_mode=True)


# Writes --------------------------------------------------------------------------------------


async def _conversation_flagged(ctx: RunContext[AgentDeps]) -> bool:
    if ctx.deps.flagged:
        return True
    async with connect() as conn:
        row = await conn.fetchrow(
            "SELECT outside_text_at FROM conversations WHERE id = $1",
            ctx.deps.conversation_id,
        )
    return bool(row and row["outside_text_at"] is not None)


async def _gate(ctx: RunContext[AgentDeps], change: str) -> None:
    """Lasting or wide writes ask first once the thread has read outside text.

    The flag can be set by a read earlier in the same turn, so it is read from the conversation
    row, not only from the snapshot taken when the turn started.
    """
    if await _conversation_flagged(ctx) and not ctx.tool_call_approved:
        ctx.deps.approvals_offered += 1
        raise ApprovalRequired(
            {
                "change": change,
                "reason": (
                    "This conversation has read statement or photo text, and this change lasts."
                ),
            }
        )
    if ctx.tool_call_approved:
        ctx.deps.approvals += 1


async def _call_write(
    fn: Callable[[Connection], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    async with connect() as conn:
        try:
            return await fn(conn)
        except write_tools.ToolInputError as exc:
            raise ModelRetry(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - ledger errors teach the model
            from fin_agent.ledger.errors import LedgerError

            if isinstance(exc, LedgerError):
                raise ModelRetry(exc.message) from exc
            raise


def _write_return(payload: dict[str, Any]) -> ToolReturn:
    card = payload.pop("card", None)
    return ToolReturn(return_value=payload, metadata=_card_data(card) if card else None)


async def _clear_confirmed_marks(names: list[str]) -> None:
    if not names:
        return
    async with connect() as conn:
        for name in names:
            clean = name.replace("<outside>", "").replace("</outside>", "").strip()
            if not clean:
                continue
            for table, column in (("payees", "display_name"), ("schedules", "name")):
                rows = await conn.fetch(
                    f"SELECT id FROM {quote_ident(table)} "
                    f"WHERE lower({quote_ident(column)}) = lower($1)",  # noqa: S608
                    clean,
                )
                for row in rows:
                    await clear_mark(conn, table, row["id"])


async def record_movement(
    ctx: RunContext[AgentDeps],
    movement: Movement,
    not_duplicate: bool = False,
) -> ToolReturn:
    """Log money that moved: expense, income, transfer between your own accounts (card bill, SIP,
    saving to a goal), split (whole bill and your share; each person and their share), loan EMI
    (total paid), refund, or settlement (direction says who paid whom). Say what happened; code
    works out the transfers. Ask first if the amount is missing or a split isn't clear. `date` is
    ISO, omit for today, never a future date. `account` is optional; code uses the payee's usual
    account, or the busiest/default one. Pass `category` only if the user named one; otherwise
    pass `category_guess`. Amounts are rupees in `amount_rupees`, never paise.
    Example: {"kind": "expense", "amount_rupees": 450, "payee": "Swiggy",
    "category": "Food & Dining"}."""
    payload_movement = movement.model_dump(exclude_none=True, mode="python")
    if ctx.tool_call_id:
        payload_movement["tool_call_id"] = ctx.tool_call_id
    flagged = await _conversation_flagged(ctx)
    payload = await _call_write(
        lambda conn: write_tools.record_movement(
            conn,
            movement=payload_movement,
            message_id=ctx.deps.message_id,
            conversation_id=ctx.deps.conversation_id,
            flagged=flagged,
            user_texts=ctx.deps.user_texts,
            not_duplicate=not_duplicate,
            capture_kind=ctx.deps.capture_kind,
            capture_ref=ctx.deps.capture_ref,
        )
    )
    return _write_return(payload)


async def correct_movement(
    ctx: RunContext[AgentDeps],
    movement_id: str,
    changes: MovementChanges,
    scope: Literal["this", "future", "all"] = "this",
) -> ToolReturn:
    """Fix a logged movement: category, payee, note, expected date, date, amount, account, or
    delete it. Omit fields that stay the same. Amounts are rupees in `amount_rupees`. For a
    category or payee, scope sets how far it goes: this row, this row and future ones, or all rows
    past and future ('ramesh@okaxis is my landlord')."""
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
    ],
    action: Literal["add", "update", "archive"],
    data: SetupData,
) -> ToolReturn:
    """Change setup: accounts, balances, schedules, goals, categories, payees, settings (the floor),
    scheduled questions, and baselines. Not for money that moved. Amounts are rupees in
    `*_rupees` fields. A schedule uses an iCalendar RRULE; `last4` is only the last four digits;
    the floor is the minimum balance to keep; `rate_percent` is annual; `as_of` dates a stated
    balance; a baseline is typical daily spend.
    For action "add" on a card, a positive credit limit, a statement day, and a due day are
    required. If the user does not know one, leave the card pending instead of guessing. A bank
    needs only a name, and its balance and last4 are optional. An update changes only the fields
    you pass.
    Example: {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}}."""
    payload_data = data.model_dump(exclude_none=True, mode="python")
    summary = payload_data.get("name") or payload_data.get("account") or payload_data
    await _gate(ctx, f"{action} {entity}: {summary}")
    payload = await _call_write(
        lambda conn: write_tools.update_setup_item(
            conn,
            entity=entity,
            action=action,
            data=payload_data,
            message_id=ctx.deps.message_id,
        )
    )
    if ctx.tool_call_approved:
        try:
            await _clear_confirmed_marks(
                [str(value) for value in payload_data.values() if isinstance(value, str)]
            )
        except Exception:  # noqa: BLE001 - the write landed; a stale mark is not a failed write
            logger.warning("could not clear a confirmed outside-text mark")
    return _write_return(payload)


async def schedule_payment(
    ctx: RunContext[AgentDeps],
    schedule: str,
    due_date: date,
    movement_id: str,
    action: Literal["link", "unlink"] = "link",
    amount_rupees: Decimal | None = None,
) -> ToolReturn:
    """Match a recorded movement to one schedule occurrence, or remove that match. This never
    records new money. Use the schedule name or ID and its holiday-adjusted ISO due date. Find
    the actual movement with query_movements first; do not invent a payment from an elapsed date
    or a confirmed schedule. A partial payment accepts amount_rupees; omit it to use the
    movement's remaining eligible amount. Existing matches are idempotent. Matching and
    unmatching each have undo; reversed movements never count as paid."""
    payload = await _call_write(
        lambda conn: write_tools.schedule_payment(
            conn,
            schedule=schedule,
            due_date=due_date,
            movement_id=movement_id,
            action=action,
            amount_rupees=amount_rupees,
            message_id=ctx.deps.message_id,
        )
    )
    if payload.pop("outside_text", False):
        async with connect() as conn:
            await flag_outside_text(conn, ctx.deps.conversation_id)
    return _write_return(payload)


async def preference(
    ctx: RunContext[AgentDeps],
    action: Literal["remember", "forget", "classify"],
    text: str | None = None,
    id: str | None = None,
    kind: Literal["preference", "fact", "rule"] | None = None,
) -> ToolReturn:
    """Remember a lasting preference, fact, or rule about the user, forget one, or reclassify one.
    A preference is how the user wants you to behave or talk, such as "Use Hinglish when I do." A
    fact is true about the user's world, such as "Mira is my flatmate." A rule is how the user
    wants their money data treated, such as "Family transfers are essential expenses." Call it
    unprompted when you notice one, and choose the kind. Use classify with the memory id and the
    new kind to change an existing memory's kind. Never store a number the ledger owns."""
    await _gate(ctx, f"{action} {kind or 'memory'}: {text or id}")
    payload = await _call_write(
        lambda conn: write_tools.preference(
            conn,
            action=action,
            text=text,
            preference_id=id,
            message_id=ctx.deps.message_id,
            kind=kind,
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
    return FunctionToolset(
        tools=[
            record_movement,
            correct_movement,
            update_setup,
            schedule_payment,
            preference,
            undo,
        ]
    )


__all__ = [
    "AgentDeps",
    "build_read_toolset",
    "build_write_toolset",
]
