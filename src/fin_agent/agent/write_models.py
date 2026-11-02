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
    date: Date | None = Field(
        default=None,
        description="The day the money moved, ISO date. Omit for today. Never a future date.",
    )
    note: str | None = Field(default=None, description="A short note for the ledger.")
    description: str | None = Field(
        default=None, description="A short human summary, if the user gave one."
    )
    upi_id: str | None = Field(default=None, description="The payee's UPI ID, if known.")


class Expense(_MovementBase):
    kind: Literal["expense"] = Field(description="An expense.")
    amount_rupees: Decimal = Field(
        description="Positive rupees as the user said them, e.g. 450 or 450.50. Not paise."
    )
    payee: str = Field(description="Merchant or person name, e.g. Swiggy or Auto bhaiya.")
    account: str | None = Field(
        default=None, description="Account name; omit to use the usual account for this payee."
    )
    category: str | None = Field(
        default=None, description="Exact category only when the user named one."
    )
    category_guess: str | None = Field(
        default=None, description="A best-guess category when the user did not name one."
    )


class Income(_MovementBase):
    kind: Literal["income"] = Field(description="Money received.")
    amount_rupees: Decimal = Field(description="Positive rupees, e.g. 120000.")
    payee: str | None = Field(default=None, description="Who paid, e.g. Acme Corp.")
    account: str | None = Field(default=None, description="Account name; omit for the default.")


class Transfer(_MovementBase):
    kind: Literal["transfer"] = Field(description="A move between the user's own accounts.")
    amount_rupees: Decimal = Field(description="Positive rupees.")
    from_account: str = Field(description="Source account name.")
    to_account: str = Field(description="Destination account name, e.g. Slice card.")


class SplitPerson(WriteModel):
    name: str = Field(description="Person's name.")
    amount_rupees: Decimal = Field(description="That person's share in rupees.")
    expected_date: Date | None = Field(
        default=None, description="When they are expected to pay, if known."
    )


class Split(_MovementBase):
    kind: Literal["split"] = Field(description="One expense split with other people.")
    total_rupees: Decimal = Field(description="The whole bill in rupees.")
    my_share_rupees: Decimal = Field(description="The user's own share in rupees.")
    people: list[SplitPerson] = Field(
        min_length=1, description="Each other person and their share."
    )
    payee: str | None = Field(default=None, description="Where the money was spent.")
    account: str | None = Field(default=None, description="Account name; omit for the default.")
    category: str | None = Field(default=None, description="Exact category if named.")
    category_guess: str | None = Field(default=None, description="Best-guess category.")


class LoanPayment(_MovementBase):
    kind: Literal["loan"] = Field(description="A loan EMI or payment.")
    amount_rupees: Decimal = Field(description="Total EMI paid in rupees.")
    loan: str = Field(description="Loan account name, e.g. Home loan.")
    account: str | None = Field(default=None, description="Paying account; omit for the default.")


class Refund(_MovementBase):
    kind: Literal["refund"] = Field(description="A refund back to the user.")
    amount_rupees: Decimal = Field(description="Positive rupees.")
    payee: str = Field(description="Who refunded.")
    account: str | None = Field(default=None, description="Account receiving the refund.")
    category: str | None = Field(default=None, description="Category the purchase was in.")


class Settlement(_MovementBase):
    kind: Literal["settlement"] = Field(description="Settling money owed with a person.")
    amount_rupees: Decimal = Field(description="Positive rupees.")
    person: str = Field(description="The person's name.")
    direction: Literal["they_paid", "i_paid"] = Field(
        default="they_paid", description="Who paid whom."
    )
    account: str | None = Field(default=None, description="The user's account.")
    expected_date: Date | None = Field(default=None, description="Expected date, if any.")


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
    name: str | None = Field(default=None, description="Account name.")
    new_name: str | None = Field(default=None, description="Rename to this.")
    kind: Literal["bank", "card", "cash", "wallet", "loan", "asset"] | None = None
    institution: str | None = None
    last4: str | None = Field(default=None, description="Last four digits only.")
    opening_balance_rupees: Decimal | None = None
    as_of: Date | None = None
    liquid: bool | None = None
    credit_limit_rupees: Decimal | None = None
    statement_day: int | None = None
    due_day: int | None = None
    rate_percent: Decimal | None = Field(default=None, description="Annual rate, e.g. 8.5.")
    tenure_months: int | None = None
    active: bool | None = None


class BalanceSetup(WriteModel):
    account: str = Field(description="Account name.")
    balance_rupees: Decimal = Field(description="The stated balance in rupees.")
    as_of: Date | None = Field(default=None, description="When the balance was true.")


class ScheduleSetup(WriteModel):
    name: str | None = Field(default=None, description="Schedule name, e.g. Rent.")
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
    floor_rupees: Decimal | None = Field(default=None, description="The floor in rupees.")
    base_currency: str | None = None
    time_zone: str | None = None
    default_horizon_days: int | None = None
    notification_time: str | None = None
    weekly_briefing_dow: int | None = Field(default=None, ge=0, le=6)
    display_name: str | None = None


class ScheduledQuestionSetup(WriteModel):
    prompt: str | None = None
    rule: str | None = None
    spend_limit_usd: Decimal | None = None
    id: str | None = None


class BaselineSetup(WriteModel):
    category: str = Field(description="Category name.")
    amount_per_day_rupees: Decimal = Field(description="Typical spend per day in rupees.")


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
