"""The L1 REST surface: accounts, movements, schedules, goals, people, settings, export, undo."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from fin_agent.api.deps import Database, current_session
from fin_agent.ledger import (
    accounts,
    categories,
    export,
    goals,
    movements,
    payees,
    schedules,
    settings,
)
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import projection as projection_tools

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
    merchant_pattern: str = Field(min_length=1, max_length=200)
    category_id: str
    source: Literal["user", "llm"] = "user"


class SettingsUpdate(ApiModel):
    floor_paise: int | None = Field(default=None, ge=0)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    time_zone: str | None = None
    default_horizon_days: int | None = Field(default=None, ge=1, le=3650)
    notification_time: str | None = None
    weekly_briefing_dow: int | None = Field(default=None, ge=0, le=6)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


def _named(items: list[dict[str, Any]], key: str = "items") -> dict[str, Any]:
    return {key: items, "as_of": ledger_today().isoformat()}


# Routes ------------------------------------------------------------------------------------


@router.get("/accounts")
def get_accounts(conn: Database) -> dict[str, Any]:
    return accounts.account_summary(conn)


@router.post("/accounts", status_code=201)
def post_account(body: AccountCreate, conn: Database) -> dict[str, Any]:
    return accounts.create_account(conn, **body.model_dump())


@router.get("/accounts/{account_id}")
def get_account(account_id: str, conn: Database) -> dict[str, Any]:
    return accounts.get_account(conn, account_id)


@router.patch("/accounts/{account_id}")
def patch_account(account_id: str, body: AccountUpdate, conn: Database) -> dict[str, Any]:
    return accounts.update_account(conn, account_id, **body.model_dump(exclude_unset=True))


@router.post("/accounts/{account_id}/statement-balance")
def post_statement_balance(
    account_id: str, body: StatementBalance, conn: Database
) -> dict[str, Any]:
    return accounts.set_statement_balance(conn, account_id, **body.model_dump())


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str, conn: Database) -> dict[str, Any]:
    return accounts.archive_account(conn, account_id)


@router.get("/movements")
def get_movements(
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
    return movements.list_movements(
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
def post_movement(body: MovementIn, conn: Database) -> dict[str, Any]:
    data = body.model_dump()
    kind = data.pop("kind")
    if kind == "expense":
        return movements.record_expense(conn, **data)
    if kind == "income":
        return movements.record_income(conn, **data)
    if kind == "transfer":
        return movements.record_transfer(conn, **data)
    if kind == "split":
        return movements.record_split(conn, **data)
    if kind == "loan":
        return movements.record_loan_payment(conn, **data)
    if kind == "refund":
        return movements.record_refund(conn, **data)
    if kind == "settlement":
        return movements.record_settlement(conn, **data)
    if kind == "lending":
        return movements.record_lending(conn, **data)
    raise AssertionError(f"Unhandled movement kind {kind}")


@router.get("/movements/{movement_id}")
def get_movement(movement_id: str, conn: Database) -> dict[str, Any]:
    return movements.get_movement(conn, movement_id)


@router.post("/movements/{movement_id}/correct")
def post_correction(movement_id: str, body: Correction, conn: Database) -> dict[str, Any]:
    return movements.correct_movement(
        conn, movement_id, changes=body.model_dump(exclude_unset=True), scope=body.scope
    )


@router.post("/undo/{undo_id}")
def post_undo(undo_id: str, conn: Database) -> dict[str, Any]:
    from fin_agent.ledger.audit import undo_group

    return undo_group(conn, undo_id)


@router.post("/undo")
def post_undo_latest(conn: Database) -> dict[str, Any]:
    from fin_agent.ledger.audit import undo_latest

    return undo_latest(conn)


@router.get("/categories")
def get_categories(conn: Database) -> dict[str, Any]:
    return categories.category_tree(conn)


@router.post("/categories", status_code=201)
def post_category(body: CategoryCreate, conn: Database) -> dict[str, Any]:
    return categories.create_category(conn, **body.model_dump())


@router.patch("/categories/{category_id}")
def patch_category(category_id: str, body: CategoryUpdate, conn: Database) -> dict[str, Any]:
    return categories.update_category(conn, category_id, **body.model_dump(exclude_unset=True))


@router.delete("/categories/{category_id}")
def delete_category(category_id: str, conn: Database) -> dict[str, Any]:
    return categories.archive_category(conn, category_id)


@router.get("/category-rules")
def get_rules(conn: Database) -> dict[str, Any]:
    return _named([dict(row) for row in categories.list_rules(conn)])


@router.post("/category-rules", status_code=201)
def post_rule(body: RuleCreate, conn: Database) -> dict[str, Any]:
    return categories.upsert_rule(conn, **body.model_dump())


@router.delete("/category-rules/{rule_id}")
def delete_rule(rule_id: str, conn: Database) -> dict[str, Any]:
    return categories.delete_rule(conn, rule_id)


@router.get("/schedules/upcoming")
def get_upcoming(conn: Database, days: int = Query(default=30, ge=1, le=730)) -> dict[str, Any]:
    return schedules.upcoming(conn, days=days)


@router.get("/schedules")
def get_schedules(conn: Database) -> dict[str, Any]:
    return _named(schedules.list_schedules(conn))


@router.post("/schedules", status_code=201)
def post_schedule(body: ScheduleCreate, conn: Database) -> dict[str, Any]:
    return schedules.create_schedule(conn, **body.model_dump())


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str, conn: Database) -> dict[str, Any]:
    return schedules.get_schedule(conn, schedule_id)


@router.patch("/schedules/{schedule_id}")
def patch_schedule(schedule_id: str, body: ScheduleUpdate, conn: Database) -> dict[str, Any]:
    return schedules.update_schedule(conn, schedule_id, **body.model_dump(exclude_unset=True))


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str, conn: Database) -> dict[str, Any]:
    return schedules.archive_schedule(conn, schedule_id)


@router.get("/goals")
def get_goals(conn: Database) -> dict[str, Any]:
    return goals.goals_summary(conn)


@router.post("/goals", status_code=201)
def post_goal(body: GoalCreate, conn: Database) -> dict[str, Any]:
    return goals.create_goal(conn, **body.model_dump())


@router.get("/goals/{goal_id}")
def get_goal(goal_id: str, conn: Database) -> dict[str, Any]:
    return goals.get_goal(conn, goal_id)


@router.patch("/goals/{goal_id}")
def patch_goal(goal_id: str, body: GoalUpdate, conn: Database) -> dict[str, Any]:
    return goals.update_goal(conn, goal_id, **body.model_dump(exclude_unset=True))


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: str, conn: Database) -> dict[str, Any]:
    return goals.archive_goal(conn, goal_id)


@router.get("/projection")
def get_projection(
    conn: Database,
    horizon_days: int | None = Query(default=None, ge=1, le=3650),
    amount_paise: int | None = Query(default=None, gt=0),
    date: Date | None = None,
    method: Literal["account", "card", "emi"] | None = None,
    emi_months: int | None = Query(default=None, ge=2, le=48),
    account: str | None = None,
) -> dict[str, Any]:
    """The read-only projection panel and the inline projection card share this payload."""
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
        }
    try:
        return projection_tools.project(conn, horizon_days=horizon_days, hypothetical=hypothetical)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/people")
def get_people(conn: Database) -> dict[str, Any]:
    return payees.people(conn)


@router.get("/payees")
def get_payees(conn: Database) -> dict[str, Any]:
    return _named([dict(row) for row in payees.list_payees(conn)])


@router.post("/payees", status_code=201)
def post_payee(body: PayeeCreate, conn: Database) -> dict[str, Any]:
    return payees.create_payee(conn, **body.model_dump())


@router.patch("/payees/{payee_id}")
def patch_payee(payee_id: str, body: PayeeUpdate, conn: Database) -> dict[str, Any]:
    return payees.update_payee(conn, payee_id, **body.model_dump(exclude_unset=True))


@router.get("/settings")
def get_settings(conn: Database) -> dict[str, Any]:
    return settings.read_settings(conn)


@router.patch("/settings")
def patch_settings(body: SettingsUpdate, conn: Database) -> dict[str, Any]:
    return settings.update_settings(conn, **body.model_dump(exclude_unset=True))


@router.get("/backup/status")
def get_backup_status(conn: Database) -> dict[str, Any]:
    return settings.backup_status(conn)


@router.get("/export/json")
def get_export_json(conn: Database) -> JSONResponse:
    stamp = datetime.now().strftime("%Y%m%d")
    return JSONResponse(
        export.export_json(conn),
        headers={"Content-Disposition": f'attachment; filename="fin-agent-{stamp}.json"'},
    )


@router.get("/export/csv")
def get_export_csv(conn: Database) -> Response:
    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        export.export_csv_zip(conn),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="fin-agent-{stamp}.zip"'},
    )


@router.get("/export/pg_dump")
def get_export_pg_dump(conn: Database) -> Response:
    del conn
    from fin_agent.config import get_settings

    stamp = datetime.now().strftime("%Y%m%d")
    payload = export.export_pg_dump(get_settings().database_url)
    return Response(
        payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="fin-agent-{stamp}.sql"'},
    )
