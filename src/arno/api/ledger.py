"""The L1 REST surface: accounts, movements, schedules, goals, people, settings, export, undo."""

from __future__ import annotations

import json
import os
import re
from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from arno.api.deps import Database, FreshSession, current_session
from arno.engine.money import MAX_PAISE
from arno.engine.projection import Projection, ProjectionInput
from arno.ledger import (
    accounts,
    categories,
    export,
    goals,
    movements,
    payees,
    schedule_payments,
    schedules,
    settings,
    uncategorized,
    workbook,
)
from arno.ledger.common import today as ledger_today
from arno.tools import projection as projection_tools
from arno.tools import write as write_tools

RUNTIME_ROLES = ("CHAT", "CHAT_FALLBACK", "VISION", "STT")
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}")


def _url_parts(url: str) -> tuple[str | None, SplitResult | None]:
    """The hostname and parts of an http(s) URL, or nothing for anything else."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - reading it raises on a bad port
    except ValueError:
        return None, None
    ok = parts.scheme in {"http", "https"} and parts.hostname
    return (parts.hostname, parts) if ok else (None, None)


router = APIRouter(
    prefix="/api",
    tags=["ledger"],
    dependencies=[Depends(current_session)],
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Accounts ----------------------------------------------------------------------------------


class AccountCreate(ApiModel):
    kind: Literal["bank", "card", "cash", "wallet", "loan", "asset"]
    name: str = Field(min_length=1, max_length=200)
    institution: str | None = None
    last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    opening_balance_paise: int | None = None
    opening_balance_as_of: Date | None = None
    liquid: bool | None = None
    credit_limit_paise: int | None = Field(default=None, gt=0)
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    emi_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    emi_fee_paise: int | None = Field(default=None, ge=0)
    finance_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    min_due_bps: int | None = Field(default=None, ge=0, le=10_000)
    min_due_floor_paise: int | None = Field(default=None, ge=0)
    rate_bps: int | None = Field(default=None, ge=0)
    tenure_months: int | None = Field(default=None, gt=0)


class AccountUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    institution: str | None = None
    last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    liquid: bool | None = None
    credit_limit_paise: int | None = Field(default=None, gt=0)
    statement_day: int | None = Field(default=None, ge=1, le=31)
    due_day: int | None = Field(default=None, ge=1, le=31)
    emi_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    emi_fee_paise: int | None = Field(default=None, ge=0)
    finance_rate_bps: int | None = Field(default=None, ge=0, le=10_000)
    min_due_bps: int | None = Field(default=None, ge=0, le=10_000)
    min_due_floor_paise: int | None = Field(default=None, ge=0)
    rate_bps: int | None = Field(default=None, ge=0)
    tenure_months: int | None = Field(default=None, gt=0)
    emi_schedule_id: str | None = None
    active: bool | None = None


class StatementBalance(ApiModel):
    balance_paise: int
    as_of: Date | None = None


# Movements ---------------------------------------------------------------------------------


class MovementBase(ApiModel):
    description: str | None = None
    date: Date | None = None
    note: str | None = None
    idempotency_key: str | None = None


class ExpenseIn(MovementBase):
    kind: Literal["expense"]
    account_id: str
    amount_paise: int = Field(gt=0)
    payee_id: str | None = None
    category_id: str | None = None
    category_guess_id: str | None = None


class IncomeIn(MovementBase):
    kind: Literal["income"]
    account_id: str
    amount_paise: int = Field(gt=0)
    payee_id: str | None = None


class TransferIn(MovementBase):
    kind: Literal["transfer"]
    from_account_id: str
    to_account_id: str
    amount_paise: int = Field(gt=0)
    category_id: str | None = None
    payee_id: str | None = None


class SplitPerson(ApiModel):
    payee_id: str
    amount_paise: int = Field(gt=0)
    expected_date: Date | None = None


class SplitIn(MovementBase):
    kind: Literal["split"]
    account_id: str
    my_share_paise: int = Field(ge=0)
    people: list[SplitPerson] = Field(min_length=1)
    total_paise: int | None = None
    payee_id: str | None = None
    category_id: str | None = None
    category_guess_id: str | None = None


class LoanIn(MovementBase):
    kind: Literal["loan"]
    account_id: str
    loan_account_id: str
    amount_paise: int = Field(gt=0)
    principal_paise: int | None = Field(default=None, ge=0)
    interest_paise: int | None = Field(default=None, ge=0)
    interest_category_id: str | None = None


class RefundIn(MovementBase):
    kind: Literal["refund"]
    account_id: str
    amount_paise: int = Field(gt=0)
    payee_id: str | None = None
    category_id: str | None = None


class SettlementIn(MovementBase):
    kind: Literal["settlement"]
    account_id: str
    payee_id: str
    amount_paise: int = Field(gt=0)
    direction: Literal["they_paid", "i_paid"] = "they_paid"
    expected_date: Date | None = None


class LendingIn(MovementBase):
    kind: Literal["lending"]
    account_id: str
    payee_id: str
    amount_paise: int = Field(gt=0)
    direction: Literal["i_lent", "i_borrowed"] = "i_lent"
    expected_date: Date | None = None


MovementIn = Annotated[
    ExpenseIn | IncomeIn | TransferIn | SplitIn | LoanIn | RefundIn | SettlementIn | LendingIn,
    Field(discriminator="kind"),
]


class Correction(ApiModel):
    category_id: str | None = None
    payee_id: str | None = None
    note: str | None = None
    expected_date: Date | None = None
    one_off: bool | None = None
    date: Date | None = None
    amount_paise: int | None = Field(default=None, gt=0)
    account_id: str | None = None
    delete: bool = False
    scope: Literal["this", "future", "all"] = "this"


# Schedules ---------------------------------------------------------------------------------


class ScheduleCreate(ApiModel):
    kind: Literal[
        "income_fixed",
        "income_variable",
        "income_oneoff",
        "obligation",
        "instalment",
        "goal_contribution",
    ]
    name: str = Field(min_length=1, max_length=200)
    rule: str = Field(min_length=1)
    starts: Date
    amount_paise: int | None = Field(default=None, gt=0)
    subtype: (
        Literal["rent", "emi", "subscription", "insurance", "family_transfer", "other"] | None
    ) = None
    account_id: str | None = None
    category_id: str | None = None
    confirmed: bool = True
    ends: Date | None = None
    holiday_adjust: Literal["none", "next", "previous"] = "none"
    outside_text: bool = False


class ScheduleUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    rule: str | None = None
    starts: Date | None = None
    amount_paise: int | None = Field(default=None, gt=0)
    subtype: (
        Literal["rent", "emi", "subscription", "insurance", "family_transfer", "other"] | None
    ) = None
    account_id: str | None = None
    category_id: str | None = None
    confirmed: bool | None = None
    ends: Date | None = None
    holiday_adjust: Literal["none", "next", "previous"] | None = None
    active: bool | None = None


class SchedulePaymentCreate(ApiModel):
    movement_id: str = Field(min_length=1)
    amount_paise: int | None = Field(default=None, gt=0)


# Goals -------------------------------------------------------------------------------------


class GoalCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    target_paise: int = Field(gt=0)
    deadline: Date | None = None
    priority: int = Field(default=3, ge=1, le=5)
    contribution_schedule_id: str | None = None


class GoalUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_paise: int | None = Field(default=None, gt=0)
    deadline: Date | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["active", "achieved", "paused", "abandoned"] | None = None
    contribution_schedule_id: str | None = None


# People and categories ---------------------------------------------------------------------


class PayeeCreate(ApiModel):
    display_name: str = Field(min_length=1, max_length=200)
    kind: Literal["person", "merchant", "organization"] = "merchant"
    upi_id: str | None = None
    default_category_id: str | None = None
    create_person_account: bool = False
    outside_text: bool = False


class PayeeUpdate(ApiModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["person", "merchant", "organization"] | None = None
    upi_id: str | None = None
    default_category_id: str | None = None
    active: bool | None = None


class CategoryCreate(ApiModel):
    name: str = Field(min_length=1, max_length=100)
    reducible: bool = False


class CategoryUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    reducible: bool | None = None
    active: bool | None = None


class RuleCreate(ApiModel):
    pattern: str = Field(min_length=1, max_length=200)
    category_id: str
    source: Literal["user", "llm"] = "user"
    match: Literal["equals", "contains", "regex"] = "equals"
    priority: int = 0


class UncategorizedAnswer(ApiModel):
    key: str = Field(min_length=1, max_length=200)
    category_id: str


class SettingsUpdate(ApiModel):
    floor_paise: int | None = Field(default=None, ge=0)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    time_zone: str | None = None
    default_horizon_days: int | None = Field(default=None, ge=1, le=3650)
    notification_time: str | None = None
    weekly_briefing_dow: int | None = Field(default=None, ge=0, le=6)
    reminder_lead_days: int | None = Field(default=None, ge=0, le=30)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


class PreferenceContentUpdate(ApiModel):
    body: str = Field(
        min_length=1,
        max_length=write_tools.MAX_PREFERENCE_BODY,
        pattern=r"\S",
    )


def _named(items: list[dict[str, Any]], key: str = "items") -> dict[str, Any]:
    return {key: items, "as_of": ledger_today().isoformat()}


# Routes ------------------------------------------------------------------------------------


@router.get("/accounts")
async def get_accounts(conn: Database) -> dict[str, Any]:
    return await accounts.account_summary(conn)


@router.post("/accounts", status_code=201)
async def post_account(body: AccountCreate, conn: Database) -> dict[str, Any]:
    return await accounts.create_account(conn, **body.model_dump())


@router.get("/accounts/{account_id}")
async def get_account(account_id: str, conn: Database) -> dict[str, Any]:
    return await accounts.get_account(conn, account_id)


@router.patch("/accounts/{account_id}")
async def patch_account(account_id: str, body: AccountUpdate, conn: Database) -> dict[str, Any]:
    return await accounts.update_account(conn, account_id, **body.model_dump(exclude_unset=True))


@router.post("/accounts/{account_id}/statement-balance")
async def post_statement_balance(
    account_id: str, body: StatementBalance, conn: Database
) -> dict[str, Any]:
    return await accounts.set_statement_balance(conn, account_id, **body.model_dump())


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str, conn: Database) -> dict[str, Any]:
    return await accounts.archive_account(conn, account_id)


@router.get("/ledger/span")
async def get_ledger_span(conn: Database) -> dict[str, Any]:
    return await movements.ledger_span(conn)


@router.get("/movements")
async def get_movements(
    conn: Database,
    start: Date | None = None,
    end: Date | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    payee_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    include_reversed: bool = False,
) -> dict[str, Any]:
    return await movements.list_movements(
        conn,
        start=start,
        end=end,
        account_id=account_id,
        category_id=category_id,
        payee_id=payee_id,
        kind=kind,
        limit=limit,
        cursor=cursor,
        include_reversed=include_reversed,
    )


@router.post("/movements", status_code=201)
async def post_movement(body: MovementIn, conn: Database) -> dict[str, Any]:
    data = body.model_dump()
    kind = data.pop("kind")
    if kind == "expense":
        return await movements.record_expense(conn, **data)
    if kind == "income":
        return await movements.record_income(conn, **data)
    if kind == "transfer":
        return await movements.record_transfer(conn, **data)
    if kind == "split":
        return await movements.record_split(conn, **data)
    if kind == "loan":
        return await movements.record_loan_payment(conn, **data)
    if kind == "refund":
        return await movements.record_refund(conn, **data)
    if kind == "settlement":
        return await movements.record_settlement(conn, **data)
    if kind == "lending":
        return await movements.record_lending(conn, **data)
    raise AssertionError(f"Unhandled movement kind {kind}")


@router.get("/movements/{movement_id}")
async def get_movement(movement_id: str, conn: Database) -> dict[str, Any]:
    movement = await movements.get_movement(conn, movement_id)
    # Only real capture rows: a source field stays null when the captured message is gone.
    rows = await conn.fetch(
        """
        SELECT c.kind, c.ref, m.id AS message_id, m.conversation_id
        FROM captures c
        LEFT JOIN messages m ON c.kind = 'chat' AND m.id = c.ref
        WHERE c.movement_id = $1
        ORDER BY c.id
        """,
        movement_id,
    )
    movement["sources"] = [dict(row) for row in rows]
    return movement


@router.post("/movements/{movement_id}/correct")
async def post_correction(movement_id: str, body: Correction, conn: Database) -> dict[str, Any]:
    return await movements.correct_movement(
        conn, movement_id, changes=body.model_dump(exclude_unset=True), scope=body.scope
    )


@router.post("/undo/{undo_id}")
async def post_undo(undo_id: str, conn: Database) -> dict[str, Any]:
    from arno.ledger.audit import undo_group

    return await undo_group(conn, undo_id)


@router.post("/undo")
async def post_undo_latest(conn: Database) -> dict[str, Any]:
    from arno.ledger.audit import undo_latest

    return await undo_latest(conn)


@router.get("/categories")
async def get_categories(conn: Database) -> dict[str, Any]:
    return await categories.category_tree(conn)


@router.post("/categories", status_code=201)
async def post_category(body: CategoryCreate, conn: Database) -> dict[str, Any]:
    return await categories.create_category(conn, **body.model_dump())


@router.patch("/categories/{category_id}")
async def patch_category(category_id: str, body: CategoryUpdate, conn: Database) -> dict[str, Any]:
    return await categories.update_category(
        conn, category_id, **body.model_dump(exclude_unset=True)
    )


@router.delete("/categories/{category_id}")
async def delete_category(category_id: str, conn: Database) -> dict[str, Any]:
    return await categories.archive_category(conn, category_id)


@router.get("/category-rules")
async def get_rules(conn: Database) -> dict[str, Any]:
    return _named(await categories.list_rules(conn))


@router.post("/category-rules", status_code=201)
async def post_rule(body: RuleCreate, conn: Database) -> dict[str, Any]:
    return await categories.upsert_rule(conn, **body.model_dump())


@router.delete("/category-rules/{rule_id}")
async def delete_rule(rule_id: str, conn: Database) -> dict[str, Any]:
    return await categories.delete_rule(conn, rule_id)


@router.get("/uncategorized")
async def get_uncategorized(conn: Database) -> dict[str, Any]:
    return await uncategorized.groups(conn)


@router.post("/uncategorized/answer")
async def post_uncategorized_answer(body: UncategorizedAnswer, conn: Database) -> dict[str, Any]:
    return await uncategorized.answer(conn, key=body.key, category_id=body.category_id)


@router.get("/schedules/upcoming")
async def get_upcoming(
    conn: Database,
    days: int = Query(default=30, ge=1, le=730),
    past_days: int = Query(default=0, ge=0, le=366),
) -> dict[str, Any]:
    return await schedules.upcoming(conn, days=days, past_days=past_days)


@router.get("/schedules")
async def get_schedules(conn: Database) -> dict[str, Any]:
    return _named(await schedules.list_schedules(conn))


@router.post("/schedules", status_code=201)
async def post_schedule(body: ScheduleCreate, conn: Database) -> dict[str, Any]:
    return await schedules.create_schedule(conn, **body.model_dump())


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, conn: Database) -> dict[str, Any]:
    return await schedules.get_schedule(conn, schedule_id)


@router.get("/schedules/{schedule_id}/occurrences/{due_date}")
async def get_schedule_occurrence(
    schedule_id: str,
    due_date: Date,
    conn: Database,
) -> dict[str, Any]:
    return await schedules.get_occurrence(conn, schedule_id, due_date)


@router.post("/schedules/{schedule_id}/occurrences/{due_date}/payments", status_code=201)
async def post_schedule_payment(
    schedule_id: str,
    due_date: Date,
    body: SchedulePaymentCreate,
    conn: Database,
) -> dict[str, Any]:
    result = await schedule_payments.link_payment(
        conn,
        schedule_id=schedule_id,
        due_date=due_date,
        **body.model_dump(),
    )
    return result | {"occurrence": await schedules.get_occurrence(conn, schedule_id, due_date)}


@router.delete("/schedules/{schedule_id}/occurrences/{due_date}/payments/{movement_id}")
async def delete_schedule_payment(
    schedule_id: str,
    due_date: Date,
    movement_id: str,
    conn: Database,
) -> dict[str, Any]:
    return await schedule_payments.unlink_payment(
        conn,
        schedule_id=schedule_id,
        due_date=due_date,
        movement_id=movement_id,
    )


@router.patch("/schedules/{schedule_id}")
async def patch_schedule(schedule_id: str, body: ScheduleUpdate, conn: Database) -> dict[str, Any]:
    return await schedules.update_schedule(conn, schedule_id, **body.model_dump(exclude_unset=True))


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, conn: Database) -> dict[str, Any]:
    return await schedules.archive_schedule(conn, schedule_id)


@router.get("/goals")
async def get_goals(conn: Database) -> dict[str, Any]:
    return await goals.goals_summary(conn)


@router.post("/goals", status_code=201)
async def post_goal(body: GoalCreate, conn: Database) -> dict[str, Any]:
    return await goals.create_goal(conn, **body.model_dump())


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str, conn: Database) -> dict[str, Any]:
    return await goals.get_goal(conn, goal_id)


@router.patch("/goals/{goal_id}")
async def patch_goal(goal_id: str, body: GoalUpdate, conn: Database) -> dict[str, Any]:
    return await goals.update_goal(conn, goal_id, **body.model_dump(exclude_unset=True))


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str, conn: Database) -> dict[str, Any]:
    return await goals.archive_goal(conn, goal_id)


# The payload is typed for the checker, not re-validated: the engine owns its shape.
async def _projection_request(
    conn: Database,
    horizon_days: int | None = Query(default=None, ge=1, le=3650),
    amount_paise: int | None = Query(default=None, gt=0, le=MAX_PAISE),
    date: Date | None = None,
    method: Literal["account", "card", "emi"] | None = None,
    emi_months: int | None = Query(default=None, ge=2, le=48),
    account: str | None = None,
    emi_rate_percent: float | None = Query(default=None, ge=0, le=100),
    emi_fee_paise: int | None = Query(default=None, ge=0, le=MAX_PAISE),
    no_cost: bool = False,
    changes: str | None = Query(default=None),
) -> tuple[ProjectionInput, Projection, dict[str, Any]]:
    """Load and calculate the projection shared by both response formats."""
    hypothetical = None
    if amount_paise is not None:
        if date is None:
            raise HTTPException(status_code=422, detail="A hypothetical needs a date.")
        hypothetical = {
            "amount_paise": amount_paise,
            "date": date,
            "method": method or "account",
            "emi_months": emi_months,
            "account": account,
            "emi_rate_percent": emi_rate_percent,
            "emi_fee_paise": emi_fee_paise,
            "no_cost": no_cost,
        }
    parsed_changes: list[dict[str, Any]] | None = None
    if changes is not None:
        try:
            loaded = json.loads(changes)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid changes JSON: {exc}") from exc
        if not isinstance(loaded, list):
            raise HTTPException(status_code=422, detail="changes must be a JSON array")
        parsed_changes = loaded
    try:
        inputs, result = await projection_tools.project_with_inputs(
            conn,
            horizon_days=horizon_days,
            hypothetical=hypothetical,
            changes=parsed_changes,
        )
        return inputs, result, {"hypothetical": hypothetical, "changes": parsed_changes}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projection", response_model=None)
async def get_projection(
    computed: Annotated[
        tuple[ProjectionInput, Projection, dict[str, Any]], Depends(_projection_request)
    ],
) -> Projection:
    """The read-only projection panel and the inline projection card share this payload."""
    return computed[1]


@router.get("/projection/workbook")
async def get_projection_workbook(
    computed: Annotated[
        tuple[ProjectionInput, Projection, dict[str, Any]], Depends(_projection_request)
    ],
) -> Response:
    inputs, result, request = computed
    return Response(
        await projection_tools.run_compute(workbook.projection_workbook, inputs, result, request),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="arno-projection-{result["as_of"]}.xlsx"'
        },
    )


@router.get("/people")
async def get_people(conn: Database) -> dict[str, Any]:
    return await payees.people(conn)


@router.get("/payees")
async def get_payees(conn: Database) -> dict[str, Any]:
    return _named(await payees.list_payees(conn))


@router.post("/payees", status_code=201)
async def post_payee(body: PayeeCreate, conn: Database) -> dict[str, Any]:
    return await payees.create_payee(conn, **body.model_dump())


@router.patch("/payees/{payee_id}")
async def patch_payee(payee_id: str, body: PayeeUpdate, conn: Database) -> dict[str, Any]:
    return await payees.update_payee(conn, payee_id, **body.model_dump(exclude_unset=True))


@router.get("/settings")
async def get_settings(conn: Database) -> dict[str, Any]:
    return await settings.read_settings(conn)


@router.patch("/settings")
async def patch_settings(body: SettingsUpdate, conn: Database) -> dict[str, Any]:
    return await settings.update_settings(conn, **body.model_dump(exclude_unset=True))


@router.patch("/preferences/{preference_id}/content")
async def patch_preference_content(
    preference_id: str, body: PreferenceContentUpdate, conn: Database
) -> dict[str, Any]:
    after, _undo_id = await write_tools.edit_preference(
        conn, preference_id, actor="user", body=body.body
    )
    return after


@router.get("/backup/status")
async def get_backup_status(conn: Database) -> dict[str, Any]:
    return await settings.backup_status(conn)


@router.get("/runtime-info")
def get_runtime_info() -> dict[str, Any]:
    """Configured model hosts and an explicitly configured Phoenix browser URL, nothing more.

    Endpoints are reduced to hostnames, never the configured URL: a base URL can carry a password
    in its userinfo, a path, or a query string with a key.
    """
    roles = []
    for prefix in RUNTIME_ROLES:
        base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        # Both are needed for a usable role; a bare base URL is not "configured" to the reader.
        configured = bool(base_url and model)
        roles.append(
            {
                "role": prefix,
                "configured": configured,
                "host": _url_parts(base_url)[0] if configured else None,
                # Anything that does not look like a model ID is omitted, not echoed.
                "model": model if configured and _SAFE_MODEL.fullmatch(model) else None,
            }
        )
    phoenix = None
    host, parts = _url_parts(os.environ.get("ARNO_PHOENIX_UI_URL", "").strip())
    if host and parts is not None:
        netloc = f"[{host}]" if ":" in host else host
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        # Keep the operator's path, but never userinfo, a query, or a fragment.
        phoenix = urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))
    return {"roles": roles, "phoenix_ui_url": phoenix}


@router.get("/export/scope")
def get_export_scope() -> dict[str, list[str]]:
    return {"excluded_tables": sorted(export.SECRET_TABLES)}


@router.get("/export/json")
async def get_export_json(conn: Database, fresh: FreshSession) -> JSONResponse:
    del fresh
    stamp = datetime.now().strftime("%Y%m%d")
    return JSONResponse(
        await export.export_json(conn),
        headers={"Content-Disposition": f'attachment; filename="arno-{stamp}.json"'},
    )


@router.get("/export/csv")
async def get_export_csv(conn: Database, fresh: FreshSession) -> Response:
    del fresh
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        await export.export_csv_zip(conn),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="arno-{stamp}.zip"'},
    )


@router.get("/export/pg_dump")
def get_export_pg_dump(conn: Database, fresh: FreshSession) -> Response:
    del conn, fresh
    from arno.config import get_settings

    stamp = datetime.now().strftime("%Y%m%d")
    payload = export.export_pg_dump(get_settings().database_url)
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="arno-{stamp}.sql"'},
    )
