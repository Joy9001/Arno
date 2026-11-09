"""Typed write arguments the model sees.

The ledger functions still take plain dicts, but the tool schema is the contract the model reads.
Field names follow `docs/fin-agent/tools.md`: write tools take rupees in `*_rupees`, read tools
return paise. `extra="forbid"` keeps a misspelled field from silently doing nothing.
"""

from __future__ import annotations

from datetime import date as Date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class WriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _MovementBase(WriteModel):
    date: Date | None = None
    note: str | None = None
    description: str | None = None
    upi_id: str | None = None


class Expense(_MovementBase):
    kind: Literal["expense"]
    amount_rupees: Decimal
    payee: str
    account: str | None = None
    category: str | None = Field(default=None, description="Only if the user named one.")
    category_guess: str | None = Field(default=None, description="Best guess otherwise.")


class Income(_MovementBase):
    kind: Literal["income"]
    amount_rupees: Decimal
    payee: str | None = None
    account: str | None = None


class Transfer(_MovementBase):
    kind: Literal["transfer"]
    amount_rupees: Decimal
    from_account: str
    to_account: str


class SplitPerson(WriteModel):
    name: str
    amount_rupees: Decimal
    expected_date: Date | None = Field(default=None, description="When they will pay, if known.")


class Split(_MovementBase):
    kind: Literal["split"]
    total_rupees: Decimal
    my_share_rupees: Decimal
    people: list[SplitPerson] = Field(min_length=1)
    payee: str | None = None
    account: str | None = None
    category: str | None = Field(default=None, description="Only if the user named one.")
    category_guess: str | None = Field(default=None, description="Best guess otherwise.")


class LoanPayment(_MovementBase):
    kind: Literal["loan"]
    amount_rupees: Decimal
    loan: str
    account: str | None = None


class Refund(_MovementBase):
    kind: Literal["refund"]
    amount_rupees: Decimal
    payee: str
    account: str | None = None
    category: str | None = None


class Settlement(_MovementBase):
    kind: Literal["settlement"]
    amount_rupees: Decimal
    person: str
    direction: Literal["they_paid", "i_paid"] = Field(
        default="they_paid", description="Who paid whom."
    )
    account: str | None = None
    expected_date: Date | None = None


Movement = Annotated[
    Expense | Income | Transfer | Split | LoanPayment | Refund | Settlement,
    Field(discriminator="kind"),
]


class MovementChanges(WriteModel):
    """Changes for `correct_movement`. Omit a field to leave it alone."""

    category: str | None = Field(default=None, description="New category name.")
    payee: str | None = Field(default=None, description="New payee name.")
    note: str | None = Field(default=None, description="New note.")
    expected_date: Date | None = Field(default=None, description="New expected date.")
    date: Date | None = Field(default=None, description="New movement date.")
    amount_rupees: Decimal | None = Field(default=None, description="New amount in rupees.")
    account: str | None = Field(default=None, description="New account name.")
    delete: bool = Field(default=False, description="Delete by appending a reversal.")


class AccountSetup(WriteModel):
    """An account add or a partial update.

    On add, `name` is required. A card also needs `credit_limit_rupees`, `statement_day`, and
    `due_day`. On update, pass only the fields that change.
    """

    name: str | None = Field(
        default=None, description="Required to add; on update, the existing account's name."
    )
    new_name: str | None = None
    kind: Literal["bank", "card", "cash", "wallet", "loan", "asset"] | None = Field(
        default=None, description="Required to add; defaults to bank."
    )
    institution: str | None = None
    last4: str | None = Field(default=None, description="Optional; last four digits only.")
    opening_balance_rupees: Decimal | None = Field(
        default=None, description="Only a balance the user states, on add; omit it if unknown."
    )
    as_of: Date | None = None
    liquid: bool | None = None
    credit_limit_rupees: Decimal | None = Field(
        default=None,
        description="Card only, required on add: the positive limit in rupees. Never guess it.",
    )
    statement_day: int | None = Field(
        default=None, description="Card only, required on add: statement day, 1-31."
    )
    due_day: int | None = Field(
        default=None, description="Card only, required on add: due day, 1-31."
    )
    rate_percent: Decimal | None = None
    tenure_months: int | None = None
    active: bool | None = None


class BalanceSetup(WriteModel):
    account: str
    balance_rupees: Decimal = Field(
        description="The stated balance in rupees. For a card or loan, the amount owed."
    )
    as_of: Date | None = None


class ScheduleSetup(WriteModel):
    name: str | None = None
    new_name: str | None = None
    kind: (
        Literal[
            "income_fixed",
            "income_variable",
            "income_oneoff",
            "obligation",
            "instalment",
            "goal_contribution",
        ]
        | None
    ) = None
    subtype: (
        Literal["rent", "emi", "subscription", "insurance", "family_transfer", "other"] | None
    ) = None
    amount_rupees: Decimal | None = None
    account: str | None = None
    category: str | None = None
    rule: str | None = Field(
        default=None, description="iCalendar RRULE, e.g. FREQ=MONTHLY;BYMONTHDAY=5."
    )
    starts: Date | None = None
    ends: Date | None = None
    confirmed: bool | None = None
    holiday_adjust: Literal["none", "next", "previous"] | None = None
    active: bool | None = None


class GoalSetup(WriteModel):
    name: str | None = None
    new_name: str | None = None
    target_rupees: Decimal | None = None
    deadline: Date | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: Literal["active", "achieved", "paused", "abandoned"] | None = None


class CategorySetup(WriteModel):
    name: str | None = None
    new_name: str | None = None
    reducible: bool | None = None


class PayeeSetup(WriteModel):
    name: str | None = None
    new_name: str | None = None
    kind: Literal["person", "merchant", "organization"] | None = None
    upi_id: str | None = None
    default_category: str | None = None
    create_person_account: bool | None = None
    active: bool | None = None


class SettingsSetup(WriteModel):
    floor_rupees: Decimal | None = Field(default=None, description="Minimum balance to keep.")
    base_currency: str | None = None
    time_zone: str | None = None
    default_horizon_days: int | None = None
    notification_time: str | None = None
    weekly_briefing_dow: int | None = Field(default=None, ge=0, le=6)
    reminder_lead_days: int | None = Field(default=None, ge=0, le=30)
    display_name: str | None = None


class ScheduledQuestionSetup(WriteModel):
    prompt: str | None = None
    rule: str | None = None
    spend_limit_usd: Decimal | None = None
    id: str | None = None


class BaselineSetup(WriteModel):
    category: str
    amount_per_day_rupees: Decimal


SetupData = Annotated[
    AccountSetup
    | BalanceSetup
    | ScheduleSetup
    | GoalSetup
    | CategorySetup
    | PayeeSetup
    | SettingsSetup
    | ScheduledQuestionSetup
    | BaselineSetup,
    Field(union_mode="left_to_right"),
]


__all__ = [
    "AccountSetup",
    "BalanceSetup",
    "BaselineSetup",
    "CategorySetup",
    "GoalSetup",
    "Income",
    "LoanPayment",
    "Movement",
    "MovementChanges",
    "PayeeSetup",
    "Refund",
    "ScheduleSetup",
    "ScheduledQuestionSetup",
    "SettingsSetup",
    "Settlement",
    "SetupData",
    "Split",
    "SplitPerson",
    "Transfer",
]
