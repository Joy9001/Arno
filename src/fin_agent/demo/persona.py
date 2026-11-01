"""A seeded synthetic Indian persona.

The same seed is the demo instance, the fixture every L1 test can lean on, and the source of the
template database that eval trials clone at L4. It is deterministic for a given `today`.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

import psycopg

from fin_agent.india.workdays import last_working_day
from fin_agent.ledger.accounts import create_account, list_accounts, set_statement_balance
from fin_agent.ledger.categories import ensure_default_categories, upsert_rule
from fin_agent.ledger.goals import create_goal, update_goal
from fin_agent.ledger.movements import (
    correct_movement,
    record_expense,
    record_income,
    record_lending,
    record_loan_payment,
    record_refund,
    record_settlement,
    record_split,
    record_transfer,
)
from fin_agent.ledger.payees import create_payee
from fin_agent.ledger.schedules import create_schedule
from fin_agent.ledger.settings import update_settings

DEMO_SETTINGS = {
    "floor_paise": 2_500_000,
    "base_currency": "INR",
    "time_zone": "Asia/Kolkata",
    "default_horizon_days": 90,
}

_RESET_TABLES = """
TRUNCATE
    pgledger_idempotency_keys, pgledger_entries, pgledger_transfers, pgledger_accounts,
    accounts, payees, categories, movements, transfer_labels, captures,
    imports, statement_lines, statement_layouts, attachments,
    schedules, goals, scheduled_questions,
    category_rules, baselines, audit_log, backup_runs
RESTART IDENTITY CASCADE
"""


class DemoSeedError(RuntimeError):
    pass


def _category(conn: psycopg.Connection, name: str) -> str:
    row = conn.execute("SELECT id FROM categories WHERE name = %s", (name,)).fetchone()
    if row is None:
        raise DemoSeedError(f"Category {name} is missing")
    return str(row["id"])


def _month_start(today: date, months_ago: int) -> date:
    year = today.year
    month = today.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _day(today: date, months_ago: int, day: int) -> date:
    first = _month_start(today, months_ago)
    last = calendar.monthrange(first.year, first.month)[1]
    return date(first.year, first.month, min(day, last))


def seed_demo(
    conn: psycopg.Connection, *, today: date | None = None, reset: bool = True
) -> dict[str, Any]:
    """Seed the persona. `reset=True` clears the L1 tables first, never auth or conversations."""
    today = today or date.today()
    if not reset:
        existing = conn.execute(
            "SELECT count(*) AS n FROM accounts WHERE kind NOT IN ('spending', 'income', 'equity')"
        ).fetchone()["n"]
        if existing:
            raise DemoSeedError("The ledger already has accounts; pass reset=True")
    else:
        conn.execute(_RESET_TABLES)
        conn.commit()

    update_settings(conn, actor="system", **DEMO_SETTINGS)
    ensure_default_categories(conn)
    food = _category(conn, "Food & Dining")
    groceries = _category(conn, "Groceries")
    transport = _category(conn, "Transport")
    fuel = _category(conn, "Fuel")
    shopping = _category(conn, "Shopping")
    subscriptions = _category(conn, "Subscriptions")
    utilities = _category(conn, "Utilities")
    rent = _category(conn, "Rent")
    insurance = _category(conn, "Insurance")
    family = _category(conn, "Family Transfer")
    savings = _category(conn, "Savings & Investments")
    interest = _category(conn, "Loan Interest")

    hdfc = create_account(
        conn,
        kind="bank",
        name="HDFC Savings",
        institution="HDFC Bank",
        last4="4821",
        opening_balance_paise=80_000_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]
    sbi = create_account(
        conn,
        kind="bank",
        name="SBI Salary",
        institution="State Bank of India",
        last4="7710",
        opening_balance_paise=4_200_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]
    slice_card = create_account(
        conn,
        kind="card",
        name="Slice card",
        institution="Slice",
        last4="2207",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        actor="system",
    )["id"]
    cash = create_account(
        conn,
        kind="cash",
        name="Cash",
        opening_balance_paise=800_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]
    wallet = create_account(
        conn,
        kind="wallet",
        name="PhonePe wallet",
        institution="PhonePe",
        opening_balance_paise=350_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]
    loan = create_account(
        conn,
        kind="loan",
        name="Home loan",
        institution="HDFC Bank",
        last4="9004",
        opening_balance_paise=-350_000_000,
        opening_balance_as_of=_day(today, 18, 1),
        rate_bps=850,
        tenure_months=240,
        actor="system",
    )["id"]
    epf = create_account(
        conn,
        kind="asset",
        name="EPF",
        institution="EPFO",
        opening_balance_paise=42_000_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]
    mutual_funds = create_account(
        conn,
        kind="asset",
        name="Mutual funds",
        institution="Zerodha Coin",
        opening_balance_paise=15_000_000,
        opening_balance_as_of=_day(today, 3, 1),
        actor="system",
    )["id"]

    swiggy = create_payee(conn, display_name="Swiggy", kind="merchant", actor="system")["id"]
    bigbasket = create_payee(
        conn,
        display_name="BigBasket",
        kind="merchant",
        default_category_id=groceries,
        actor="system",
    )["id"]
    netflix = create_payee(
        conn,
        display_name="Netflix",
        kind="merchant",
        default_category_id=subscriptions,
        actor="system",
    )["id"]
    uber = create_payee(
        conn,
        display_name="Uber",
        kind="merchant",
        default_category_id=transport,
        actor="system",
    )["id"]
    landlord = create_payee(
        conn,
        display_name="Ramesh (landlord)",
        kind="person",
        upi_id="ramesh@okaxis",
        default_category_id=rent,
        actor="system",
    )["id"]
    ravi = create_payee(conn, display_name="Ravi", kind="person", actor="system")["id"]
    amit = create_payee(conn, display_name="Amit", kind="person", actor="system")["id"]
    mom = create_payee(
        conn, display_name="Mom", kind="person", default_category_id=family, actor="system"
    )["id"]
    employer = create_payee(conn, display_name="Acme Corp", kind="organization", actor="system")[
        "id"
    ]

    salary_rule = "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"
    salary = create_schedule(
        conn,
        kind="income_fixed",
        name="Salary",
        amount_paise=15_000_000,
        account_id=sbi,
        rule=salary_rule,
        starts=_month_start(today, 11),
        holiday_adjust="previous",
        actor="system",
    )["id"]
    create_schedule(
        conn,
        kind="income_variable",
        name="Freelance work",
        amount_paise=2_500_000,
        account_id=hdfc,
        rule="FREQ=MONTHLY;BYMONTHDAY=15",
        starts=_month_start(today, 11),
        confirmed=False,
        actor="system",
    )
    create_schedule(
        conn,
        kind="income_oneoff",
        name="Diwali bonus",
        amount_paise=5_000_000,
        account_id=sbi,
        rule="FREQ=DAILY;COUNT=1",
        starts=today + timedelta(days=20),
        actor="system",
    )
    create_schedule(
        conn,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_800_000,
        account_id=hdfc,
        category_id=rent,
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=_month_start(today, 11),
        holiday_adjust="next",
        actor="system",
    )
    emi_schedule = create_schedule(
        conn,
        kind="obligation",
        subtype="emi",
        name="Home loan EMI",
        amount_paise=3_150_000,
        account_id=hdfc,
        category_id=interest,
        rule="FREQ=MONTHLY;BYMONTHDAY=10",
        starts=_month_start(today, 11),
        holiday_adjust="next",
        actor="system",
    )["id"]
    create_schedule(
        conn,
        kind="obligation",
        subtype="subscription",
        name="Netflix",
        amount_paise=64_900,
        account_id=slice_card,
        category_id=subscriptions,
        rule="FREQ=MONTHLY;BYMONTHDAY=12",
        starts=_month_start(today, 11),
        holiday_adjust="next",
        actor="system",
    )
    create_schedule(
        conn,
        kind="obligation",
        subtype="insurance",
        name="Health insurance",
        amount_paise=1_200_000,
        account_id=hdfc,
        category_id=insurance,
        rule="FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=14",
        starts=date(today.year, 3, 14),
        holiday_adjust="next",
        actor="system",
    )
    create_schedule(
        conn,
        kind="obligation",
        subtype="family_transfer",
        name="Monthly transfer to Mom",
        amount_paise=1_500_000,
        account_id=hdfc,
        category_id=family,
        rule="FREQ=MONTHLY;BYMONTHDAY=3",
        starts=_month_start(today, 11),
        holiday_adjust="next",
        actor="system",
    )
    create_schedule(
        conn,
        kind="instalment",
        name="Phone on card",
        amount_paise=833_300,
        account_id=slice_card,
        category_id=shopping,
        rule="FREQ=MONTHLY;BYMONTHDAY=5;COUNT=6",
        starts=_month_start(today, 5),
        actor="system",
    )

    goa = create_goal(
        conn,
        name="Goa trip",
        target_paise=12_000_000,
        deadline=today + timedelta(days=210),
        priority=2,
        actor="system",
    )
    goal_account = goa["account_id"]
    goal_schedule = create_schedule(
        conn,
        kind="goal_contribution",
        name="Goa trip contribution",
        amount_paise=1_000_000,
        account_id=hdfc,
        category_id=savings,
        rule="FREQ=MONTHLY;BYMONTHDAY=7",
        starts=_month_start(today, 5),
        actor="system",
    )["id"]
    update_goal(conn, goa["id"], contribution_schedule_id=goal_schedule, actor="system")
    update_account_emi(conn, loan, emi_schedule)

    upsert_rule(
        conn, merchant_pattern="BigBasket", category_id=groceries, source="llm", actor="system"
    )
    upsert_rule(
        conn, merchant_pattern="BigBasket", category_id=shopping, source="user", actor="system"
    )
    upsert_rule(conn, merchant_pattern="Swiggy", category_id=food, source="user", actor="system")

    for months_ago in (3, 2, 1, 0):
        salary_day = last_working_day(
            _month_start(today, months_ago).year, _month_start(today, months_ago).month
        )
        if salary_day <= today:
            record_income(
                conn,
                account_id=sbi,
                amount_paise=15_000_000,
                description="Monthly salary",
                payee_id=employer,
                date=salary_day,
                idempotency_key=f"demo:salary:{salary_day.isoformat()}",
                actor="system",
            )
            record_transfer(
                conn,
                from_account_id=sbi,
                to_account_id=hdfc,
                amount_paise=12_500_000,
                description="Monthly transfer to spending account",
                date=salary_day,
                actor="system",
            )
        rent_day = _day(today, months_ago, 5)
        if rent_day <= today:
            record_expense(
                conn,
                account_id=hdfc,
                amount_paise=2_800_000,
                description="House rent",
                payee_id=landlord,
                category_id=rent,
                date=rent_day,
                actor="system",
            )
        for day_number, amount, merchant, category in (
            (8, 185_000, bigbasket, groceries),
            (18, 240_000, bigbasket, groceries),
            (9, 62_000, swiggy, food),
            (16, 48_000, swiggy, food),
            (22, 35_000, swiggy, food),
            (11, 32_000, uber, transport),
            (24, 28_000, uber, transport),
        ):
            when = _day(today, months_ago, day_number)
            if when <= today:
                record_expense(
                    conn,
                    account_id=hdfc,
                    amount_paise=amount,
                    description="Everyday spend",
                    payee_id=merchant,
                    category_id=category,
                    date=when,
                    actor="system",
                )
        netflix_day = _day(today, months_ago, 12)
        if netflix_day <= today:
            record_expense(
                conn,
                account_id=slice_card,
                amount_paise=64_900,
                description="Netflix subscription",
                payee_id=netflix,
                category_id=subscriptions,
                date=netflix_day,
                actor="system",
            )
        utility_day = _day(today, months_ago, 14)
        if utility_day <= today:
            record_expense(
                conn,
                account_id=hdfc,
                amount_paise=220_000,
                description="Electricity and internet",
                category_id=utilities,
                date=utility_day,
                actor="system",
            )
        fuel_day = _day(today, months_ago, 21)
        if fuel_day <= today:
            record_expense(
                conn,
                account_id=hdfc,
                amount_paise=300_000,
                description="Fuel",
                category_id=fuel,
                date=fuel_day,
                actor="system",
            )
        emi_day = _day(today, months_ago, 10)
        if emi_day <= today:
            record_loan_payment(
                conn,
                account_id=hdfc,
                loan_account_id=loan,
                amount_paise=3_150_000,
                principal_paise=2_800_000,
                interest_paise=350_000,
                interest_category_id=interest,
                date=emi_day,
                actor="system",
            )
        sip_day = _day(today, months_ago, 8)
        if sip_day <= today:
            record_transfer(
                conn,
                from_account_id=hdfc,
                to_account_id=mutual_funds,
                amount_paise=500_000,
                description="SIP",
                category_id=savings,
                date=sip_day,
                actor="system",
            )
        family_day = _day(today, months_ago, 3)
        if family_day <= today:
            record_expense(
                conn,
                account_id=hdfc,
                amount_paise=1_500_000,
                description="Transfer to Mom",
                payee_id=mom,
                category_id=family,
                date=family_day,
                actor="system",
            )
        goal_day = _day(today, months_ago, 7)
        if goal_day <= today:
            record_transfer(
                conn,
                from_account_id=hdfc,
                to_account_id=goal_account,
                amount_paise=1_000_000,
                description="Goa trip saving",
                category_id=savings,
                date=goal_day,
                actor="system",
            )

    dinner_day = today - timedelta(days=18)
    record_split(
        conn,
        account_id=hdfc,
        my_share_paise=40_000,
        people=[
            {"payee_id": ravi, "amount_paise": 20_000, "expected_date": today + timedelta(days=7)},
            {"payee_id": amit, "amount_paise": 20_000, "expected_date": today + timedelta(days=14)},
        ],
        total_paise=80_000,
        description="Team dinner",
        payee_id=swiggy,
        category_id=food,
        date=dinner_day,
        actor="system",
    )
    record_settlement(
        conn,
        account_id=hdfc,
        payee_id=ravi,
        amount_paise=20_000,
        direction="they_paid",
        description="Ravi paid his share",
        date=today - timedelta(days=10),
        actor="system",
    )
    record_lending(
        conn,
        account_id=hdfc,
        payee_id=ravi,
        amount_paise=200_000,
        expected_date=today + timedelta(days=30),
        description="Short loan to Ravi",
        date=today - timedelta(days=40),
        actor="system",
    )
    record_lending(
        conn,
        account_id=hdfc,
        payee_id=employer,
        amount_paise=150_000,
        expected_date=today - timedelta(days=5),
        description="Internet bill paid for Acme",
        date=today - timedelta(days=6),
        actor="system",
    )
    record_settlement(
        conn,
        account_id=hdfc,
        payee_id=employer,
        amount_paise=150_000,
        direction="they_paid",
        description="Internet reimbursement",
        date=today - timedelta(days=5),
        actor="system",
    )
    record_refund(
        conn,
        account_id=hdfc,
        amount_paise=30_000,
        description="Swiggy refund",
        payee_id=swiggy,
        category_id=food,
        date=today - timedelta(days=4),
        actor="system",
    )

    dinner = record_expense(
        conn,
        account_id=hdfc,
        amount_paise=250_000,
        description="Groceries, entered wrong",
        payee_id=bigbasket,
        category_id=groceries,
        date=today - timedelta(days=2),
        actor="system",
    )
    correct_movement(
        conn,
        dinner["id"],
        changes={"amount_paise": 225_000},
        actor="system",
    )

    cash_balance = next(
        account["balance_paise"] for account in list_accounts(conn) if account["id"] == cash
    )
    set_statement_balance(
        conn,
        hdfc,
        balance_paise=next(
            account["balance_paise"] for account in list_accounts(conn) if account["id"] == hdfc
        ),
        as_of=today,
        actor="system",
    )

    return {
        "today": today.isoformat(),
        "accounts": len(list_accounts(conn, include_system=True)),
        "categories": len(ensure_default_categories(conn)),
        "schedules": conn.execute("SELECT count(*) AS n FROM schedules").fetchone()["n"],
        "goals": conn.execute("SELECT count(*) AS n FROM goals").fetchone()["n"],
        "movements": conn.execute("SELECT count(*) AS n FROM movements").fetchone()["n"],
        "people": conn.execute(
            "SELECT count(*) AS n FROM payees WHERE kind IN ('person', 'organization')"
        ).fetchone()["n"],
        "cash_balance_paise": cash_balance,
        "salary_schedule_id": salary,
        "epf_account_id": epf,
        "wallet_account_id": wallet,
        "hdfc_account_id": hdfc,
        "sbi_account_id": sbi,
        "loan_account_id": loan,
        "goal_account_id": goal_account,
    }


def update_account_emi(conn: psycopg.Connection, loan_account_id: str, schedule_id: str) -> None:
    """Point the loan at its EMI schedule. A small helper so the demo stays readable."""
    from fin_agent.ledger.accounts import update_account

    update_account(conn, loan_account_id, emi_schedule_id=schedule_id, actor="system")


__all__ = ["DEMO_SETTINGS", "DemoSeedError", "seed_demo"]
