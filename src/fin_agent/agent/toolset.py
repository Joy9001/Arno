"""The agent's tools: eight read tools behind `run_code`, six write calls in front of it.

Descriptions are the model-facing contract. Read tools return `ToolReturn` with a data card; a
card from a tool called inside a script is picked up from the `run_code` return after the run
(system-design step 5).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import date
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.write_models import Movement, MovementChanges, SetupData
from fin_agent.db.conn import connect
from fin_agent.engine.money import amortize as amortize_fn
from fin_agent.engine.money import compound as compound_fn
from fin_agent.ledger.accounts import LoanTermsError, loan_terms
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import projection as projection_tools
from fin_agent.tools import read as read_tools
from fin_agent.tools import write as write_tools
from fin_agent.tools.mark import clear_mark, flag_outside_text


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
        description='"this_month", "last_month", "2026-09", or a range "2026-09-01..2026-09-15".',
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


def _read_call(fn: Callable[[psycopg.Connection], dict[str, Any]]) -> dict[str, Any]:
    with closing(connect()) as conn:
        return fn(conn)


def _finish_read(ctx: RunContext[AgentDeps], payload: dict[str, Any]) -> ToolReturn:
    if payload.get("outside_text"):
        with closing(connect()) as conn:
            flag_outside_text(conn, ctx.deps.conversation_id)
            conn.commit()
    card = payload.pop("_card", None)
    metadata = _card_data(card) if card else None
    return ToolReturn(return_value=payload, metadata=metadata)


async def get_financial_context(ctx: RunContext[AgentDeps]) -> ToolReturn:
    """The user's whole picture: today's date, settings, accounts with balances and ages, the
    schedules behind the next 30 days, goals, who owes what, remembered preferences, what setup
    is missing, and data warnings. The sandbox has no clock, so get today's date here."""
    payload = _read_call(read_tools.get_financial_context)
    return _finish_read(ctx, payload)


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
        payload = _read_call(
            lambda conn: read_tools.query_movements(conn, filters=plain, limit=limit, cursor=cursor)
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return _finish_read(ctx, payload)


async def summarize_spending(
    ctx: RunContext[AgentDeps],
    group_by: Literal["category", "payee", "account", "month", "week", "none"],
    period: str,
    filters: SpendFilters | None = None,
) -> ToolReturn:
    """Totals of money spent, net of refunds, grouped however you need. Loan principal, card
    bills, investments, and money others owe never count as spending. To compare periods, call it
    twice. period is "last_month", "2026-09", or a range "2026-09-01..2026-09-15". count is the
    number of entries, with a corrected entry and its reversal left out."""
    plain = filters.model_dump(exclude_none=True) if filters else None
    try:
        payload = _read_call(
            lambda conn: read_tools.summarize_spending(
                conn, group_by=group_by, period=period, filters=plain
            )
        )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return _finish_read(ctx, payload)


async def run_query(ctx: RunContext[AgentDeps], sql: str) -> ToolReturn:
    """Read-only SQL for questions the typed tools can't answer, including conversation search.
    Try query_movements and summarize_spending first. Prefer the views v_spending, v_cashflow,
    v_balances, and v_owed. Tables: accounts, movements, transfer_labels, captures, imports,
    statement_lines, schedules, goals, payees, categories, category_rules, baselines, preferences,
    messages, conversations, settings. The role cannot write and has a 2-second timeout."""
    try:
        payload = read_tools.run_query(sql)
    except psycopg.errors.QueryCanceled as exc:
        raise ModelRetry("The query hit the 2-second statement timeout.") from exc
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    except psycopg.Error as exc:
        raise ModelRetry(str(exc).strip()) from exc
    payload["_card"] = {
        "card": "query",
        "sql": payload["sql"],
        "row_count": payload["row_count"],
        "truncated": payload["truncated"],
    }
    return _finish_read(ctx, payload)


async def project(
    ctx: RunContext[AgentDeps],
    horizon_days: int | None = None,
    hypothetical: ProjectHypothetical | None = None,
) -> ToolReturn:
    """Forecast the user's cash against their floor (the minimum balance they want to keep).
    Returns the median path, the P10-P90 band, the floor probability, goal completion, and the
    data-quality notes. With a hypothetical purchase, also returns the yes, yes-but, or no verdict
    and the tradeoffs. Never give your own verdict: the engine decides. State the data_quality
    notes in your answer. horizon_days is a minimum; the engine always looks at least 90 days and
    as far as a goal deadline."""
    if horizon_days is not None and not 1 <= horizon_days <= 3650:
        raise ModelRetry("horizon_days is 1 to 3650")
    try:
        with closing(connect()) as conn:
            result = projection_tools.project(
                conn,
                horizon_days=horizon_days,
                hypothetical=(hypothetical.model_dump(exclude_none=True) if hypothetical else None),
            )
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    payload = projection_tools.summary(result)
    payload["_card"] = projection_tools.card(result)
    return _finish_read(ctx, payload)


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
    del ctx
    start: date | None = ledger_today()
    if loan:
        with closing(connect()) as conn:
            try:
                terms = loan_terms(conn, loan)
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
    return ToolReturn(
        return_value=result,
        metadata=_card_data({"card": "amortize", "title": "Loan schedule", **result}),
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
    del ctx
    try:
        result = compound_fn(monthly_paise, annual_rate, months, start_paise=start_paise)
    except ValueError as exc:
        raise ModelRetry(str(exc)) from exc
    return ToolReturn(
        return_value=result,
        metadata=_card_data({"card": "compound", "title": "Growth at an assumed rate", **result}),
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
    del ctx
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
            if isinstance(point["y"], bool) or not isinstance(point["y"], (int, float)):
                raise ModelRetry("chart values must be numbers")
            cleaned.append({"x": str(point["x"]), "y": int(point["y"])})
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
    return ToolReturn(
        return_value={"chart_id": chart_id, "shown": True},
        metadata=_card_data({"card": "chart", **spec}),
    )


def build_read_toolset() -> FunctionToolset[AgentDeps]:
    return FunctionToolset(
        tools=[
            get_financial_context,
            query_movements,
            summarize_spending,
            project,
            run_query,
            amortize,
            compound,
            show_chart,
        ],
        timeout=60,
    ).with_metadata(code_mode=True)


# Writes --------------------------------------------------------------------------------------


def _conversation_flagged(ctx: RunContext[AgentDeps]) -> bool:
    if ctx.deps.flagged:
        return True
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT outside_text_at FROM conversations WHERE id = %s",
            (ctx.deps.conversation_id,),
        ).fetchone()
    return bool(row and row["outside_text_at"] is not None)


def _gate(ctx: RunContext[AgentDeps], change: str) -> None:
    """Lasting or wide writes ask first once the thread has read outside text.

    The flag can be set by a read earlier in the same turn, so it is read from the conversation
    row, not only from the snapshot taken when the turn started.
    """
    if _conversation_flagged(ctx) and not ctx.tool_call_approved:
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


def _call_write(fn: Callable[[psycopg.Connection], dict[str, Any]]) -> dict[str, Any]:
    with closing(connect()) as conn:
        try:
            return fn(conn)
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


def _clear_confirmed_marks(names: list[str]) -> None:
    if not names:
        return
    with closing(connect()) as conn:
        for name in names:
            clean = name.replace("<outside>", "").replace("</outside>", "").strip()
            if not clean:
                continue
            for table, column in (("payees", "display_name"), ("schedules", "name")):
                rows = conn.execute(
                    f"SELECT id FROM {table} WHERE lower({column}) = lower(%s)",  # noqa: S608
                    (clean,),
                ).fetchall()
                for row in rows:
                    clear_mark(conn, table, row["id"])


async def record_movement(
    ctx: RunContext[AgentDeps],
    movement: Movement,
    not_duplicate: bool = False,
) -> ToolReturn:
    """Log money that moved: expense, income, transfer (card bill, SIP, saving to a goal), split,
    loan EMI, refund, or settling money owed. Say what happened; code works out the transfers.
    Ask first if the amount is missing or a split isn't clear. Pass category only if the user named
    one; otherwise pass category_guess. Amounts are rupees in `amount_rupees`, never paise.
    Example: {"kind": "expense", "amount_rupees": 450, "payee": "Swiggy",
    "category": "Food & Dining"}."""
    payload_movement = movement.model_dump(exclude_none=True, mode="python")
    if ctx.tool_call_id:
        payload_movement["tool_call_id"] = ctx.tool_call_id
    payload = _call_write(
        lambda conn: write_tools.record_movement(
            conn,
            movement=payload_movement,
            message_id=ctx.deps.message_id,
            conversation_id=ctx.deps.conversation_id,
            flagged=_conversation_flagged(ctx),
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
    payload = _call_write(
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
    """Change the user's setup: accounts, stated balances, schedules (income, EMIs, rent,
    subscriptions), goals, categories, payees, settings such as the floor, scheduled questions, and
    estimated baselines. Not for logging money that moved. Amounts are rupees in `*_rupees`
    fields, e.g. {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}}."""
    payload_data = data.model_dump(exclude_none=True, mode="python")
    summary = payload_data.get("name") or payload_data.get("account") or payload_data
    _gate(ctx, f"{action} {entity}: {summary}")
    payload = _call_write(
        lambda conn: write_tools.update_setup_item(
            conn,
            entity=entity,
            action=action,
            data=payload_data,
            message_id=ctx.deps.message_id,
        )
    )
    if ctx.tool_call_approved:
        _clear_confirmed_marks(
            [str(value) for value in payload_data.values() if isinstance(value, str)]
        )
    return _write_return(payload)


async def preference(
    ctx: RunContext[AgentDeps],
    action: Literal["remember", "forget"],
    text: str | None = None,
    id: str | None = None,
) -> ToolReturn:
    """Remember a lasting fact or rule about the user, or forget one. Call it unprompted when you
    notice one, such as "Mom's monthly transfer isn't discretionary." Never store a number the
    ledger owns."""
    _gate(ctx, f"{action} preference: {text or id}")
    payload = _call_write(
        lambda conn: write_tools.preference(
            conn,
            action=action,
            text=text,
            preference_id=id,
            message_id=ctx.deps.message_id,
        )
    )
    return _write_return(payload)


async def import_document(ctx: RunContext[AgentDeps], attachment_id: str) -> ToolReturn:
    """Start importing an uploaded statement. Returns a status and a review link; the user
    approves on the review screen, and you can't commit an import. If the file needs a password,
    tell the user to enter it on the import card. Never ask for it in chat."""
    payload = _call_write(
        lambda conn: write_tools.import_document(
            conn,
            attachment_id=attachment_id,
            message_id=ctx.deps.message_id,
        )
    )
    return _write_return(payload)


async def undo(ctx: RunContext[AgentDeps], undo_id: str | None = None) -> ToolReturn:
    """Undo a write. With no ID, it undoes the last write in this conversation. Use the undo_id
    from a write's result for an earlier one."""
    payload = _call_write(
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
            preference,
            import_document,
            undo,
        ]
    )


__all__ = [
    "AgentDeps",
    "build_read_toolset",
    "build_write_toolset",
]
