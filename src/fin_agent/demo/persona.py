"""A seeded synthetic Indian persona.

The same seed is the demo instance, the fixture every L1 test can lean on, and the source of the
template database that eval trials clone at L4. It is deterministic for a given `today`.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.india.workdays import last_working_day
from fin_agent.ledger.accounts import create_account, list_accounts, set_statement_balance
from fin_agent.ledger.categories import ensure_default_categories, upsert_rule
from fin_agent.ledger.common import today as ledger_today
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
    imports, statement_lines, statement_layouts, parser_versions, attachments,
    schedules, goals, scheduled_questions,
    category_rules, baselines, audit_log, backup_runs
RESTART IDENTITY CASCADE
"""


class DemoSeedError(RuntimeError):
    pass


async def _category(conn: Connection, name: str) -> str:
    row = await conn.fetchrow("SELECT id FROM categories WHERE name = $1", name)
    if row is None:
        raise DemoSeedError(f"Category {name} is missing")
    return str(row["id"])


async def _seed_baselines(conn: Connection, amounts: dict[str, int]) -> None:
    """Estimated daily spend, so the demo projection has variable spend before real history."""
    for category_id, amount in amounts.items():
        await conn.execute(
            """
            INSERT INTO baselines (category_id, amount_per_day_paise, source, window_days)
            VALUES ($1, $2, 'estimated', NULL)
            ON CONFLICT (category_id) DO UPDATE
            SET amount_per_day_paise = EXCLUDED.amount_per_day_paise,
                source = 'estimated',
                window_days = NULL,
                updated_at = now()
            """,
            category_id,
            amount,
        )


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


def _recent_complete_months(today: date, count: int = 3) -> list[tuple[date, date, str]]:
    """The last `count` complete calendar months before `today`, oldest first."""
    months: list[tuple[date, date, str]] = []
    for months_ago in range(count, 0, -1):
        start = _month_start(today, months_ago)
        end = date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        months.append((start, end, f"{start.year:04d}-{start.month:02d}"))
    return months


async def _seed_statement_coverage(
    conn: Connection,
    accounts: list[dict[str, Any]],
    today: date,
    *,
    leave_gap: bool,
) -> dict[str, Any]:
    """Insert synthetic committed imports so coverage reflects the last three complete months.

    These are coverage rows only: no statement files, no statement lines, and no real data. They
    make the demo template exercise "answered from covered data" as well as the older-month
    "no data" path. `leave_gap` drops the oldest of the three months for the honesty cases.
    """
    months = _recent_complete_months(today)
    gap_month = months.pop(0)[2] if leave_gap and months else None
    inserted = 0
    for account in accounts:
        for start, end, key in months:
            await conn.execute(
                """
                INSERT INTO imports (
                    file_hash, parser, account_id, period_start, period_end,
                    check_result, status, committed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, 'committed', now())
                """,
                f"demo-statement:{account['id']}:{key}",
                "synthetic-demo",
                account["id"],
                start,
                end,
                {
                    "status": "VERIFIED",
                    "synthetic": True,
                    "source": "demo persona coverage",
                },
            )
            inserted += 1
    return {
        "months": [key for _, _, key in months],
        "gap": gap_month,
        "imports": inserted,
    }


async def seed_demo(
    conn: Connection,
    *,
    today: date | None = None,
    reset: bool = True,
    statement_coverage: bool = True,
    coverage_gap: bool = False,
) -> dict[str, Any]:
    """Seed the persona.

    `reset=True` clears the L1 tables first, never auth or conversations. The persona gives every
    active non-system account synthetic committed imports for the last three complete months, so
    coverage reports a covered range rather than a gap everywhere. `coverage_gap=True` leaves the
    oldest of those months uncovered for the honesty eval cases; `statement_coverage=False` turns
    the coverage rows off entirely.
    """
    today = today or date.today()
    ledger_day = ledger_today()
    if today > ledger_day:
        raise DemoSeedError(
            f"The demo anchor {today.isoformat()} is in the future. The ledger refuses movements "
            f"dated after today ({ledger_day.isoformat()}); pass today or an earlier date."
        )
    if not reset:
        existing = (
            await conn.fetchrow(
                "SELECT count(*) AS n FROM accounts "
                "WHERE kind NOT IN ('spending', 'income', 'equity')"
            )
        )["n"]
        if existing:
            raise DemoSeedError("The ledger already has accounts; pass reset=True")
    else:
        await conn.execute(_RESET_TABLES)

    await update_settings(conn, actor="system", **DEMO_SETTINGS)
    await ensure_default_categories(conn)
    food = await _category(conn, "Food & Dining")
    groceries = await _category(conn, "Groceries")
    transport = await _category(conn, "Transport")
    fuel = await _category(conn, "Fuel")
    shopping = await _category(conn, "Shopping")
    subscriptions = await _category(conn, "Subscriptions")
    utilities = await _category(conn, "Utilities")
    rent = await _category(conn, "Rent")
    insurance = await _category(conn, "Insurance")
    family = await _category(conn, "Family Transfer")
    savings = await _category(conn, "Savings & Investments")
    interest = await _category(conn, "Loan Interest")
    await _seed_baselines(
        conn,
        {
            food: 5_000,
            groceries: 14_000,
            transport: 2_000,
            fuel: 10_000,
            shopping: 5_000,
            utilities: 7_000,
        },
    )

    hdfc = (
        await create_account(
            conn,
            kind="bank",
            name="HDFC Savings",
            institution="HDFC Bank",
            last4="4821",
            opening_balance_paise=80_000_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]
    sbi = (
        await create_account(
            conn,
            kind="bank",
            name="SBI Salary",
            institution="State Bank of India",
            last4="7710",
            opening_balance_paise=4_200_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]
    slice_card = (
        await create_account(
            conn,
            kind="card",
            name="Slice card",
            institution="Slice",
            last4="2207",
            credit_limit_paise=10_000_000,
            statement_day=5,
            due_day=25,
            actor="system",
        )
    )["id"]
    cash = (
        await create_account(
            conn,
            kind="cash",
            name="Cash",
            opening_balance_paise=800_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]
    wallet = (
        await create_account(
            conn,
            kind="wallet",
            name="PhonePe wallet",
            institution="PhonePe",
            opening_balance_paise=350_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]
    loan = (
        await create_account(
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
        )
    )["id"]
    epf = (
        await create_account(
            conn,
            kind="asset",
            name="EPF",
            institution="EPFO",
            opening_balance_paise=42_000_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]
    mutual_funds = (
        await create_account(
            conn,
            kind="asset",
            name="Mutual funds",
            institution="Zerodha Coin",
            opening_balance_paise=15_000_000,
            opening_balance_as_of=_day(today, 3, 1),
            actor="system",
        )
    )["id"]

    swiggy = (await create_payee(conn, display_name="Swiggy", kind="merchant", actor="system"))[
        "id"
    ]
    bigbasket = (
        await create_payee(
            conn,
            display_name="BigBasket",
            kind="merchant",
            default_category_id=groceries,
            actor="system",
        )
    )["id"]
    netflix = (
        await create_payee(
            conn,
            display_name="Netflix",
            kind="merchant",
            default_category_id=subscriptions,
            actor="system",
        )
    )["id"]
    uber = (
        await create_payee(
            conn,
            display_name="Uber",
            kind="merchant",
            default_category_id=transport,
            actor="system",
        )
    )["id"]
    landlord = (
        await create_payee(
            conn,
            display_name="Ramesh (landlord)",
            kind="person",
            upi_id="ramesh@okaxis",
            default_category_id=rent,
            actor="system",
        )
    )["id"]
    ravi = (await create_payee(conn, display_name="Ravi", kind="person", actor="system"))["id"]
    amit = (await create_payee(conn, display_name="Amit", kind="person", actor="system"))["id"]
    mom = (
        await create_payee(
            conn, display_name="Mom", kind="person", default_category_id=family, actor="system"
        )
    )["id"]
    employer = (
        await create_payee(conn, display_name="Acme Corp", kind="organization", actor="system")
    )["id"]

    salary_rule = "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1"
    salary = (
        await create_schedule(
            conn,
            kind="income_fixed",
            name="Salary",
            amount_paise=15_000_000,
            account_id=sbi,
            rule=salary_rule,
            starts=_month_start(today, 11),
            holiday_adjust="previous",
            actor="system",
        )
    )["id"]
    await create_schedule(
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
    await create_schedule(
        conn,
        kind="income_oneoff",
        name="Diwali bonus",
        amount_paise=5_000_000,
        account_id=sbi,
        rule="FREQ=DAILY;COUNT=1",
        starts=today + timedelta(days=20),
        actor="system",
    )
    await create_schedule(
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
    emi_schedule = (
        await create_schedule(
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
        )
    )["id"]
    await create_schedule(
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
    await create_schedule(
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
    await create_schedule(
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
    await create_schedule(
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

    goa = await create_goal(
        conn,
        name="Goa trip",
        target_paise=12_000_000,
        deadline=today + timedelta(days=210),
        priority=2,
        actor="system",
    )
    goal_account = goa["account_id"]
    goal_schedule = (
        await create_schedule(
            conn,
            kind="goal_contribution",
            name="Goa trip contribution",
            amount_paise=1_000_000,
            account_id=hdfc,
            category_id=savings,
            rule="FREQ=MONTHLY;BYMONTHDAY=7",
            starts=_month_start(today, 5),
            actor="system",
        )
    )["id"]
    await update_goal(conn, goa["id"], contribution_schedule_id=goal_schedule, actor="system")
    await update_account_emi(conn, loan, emi_schedule)

    await upsert_rule(
        conn, pattern="BigBasket", category_id=groceries, source="llm", actor="system"
    )
    await upsert_rule(
        conn, pattern="BigBasket", category_id=shopping, source="user", actor="system"
    )
    await upsert_rule(conn, pattern="Swiggy", category_id=food, source="user", actor="system")

    for months_ago in (3, 2, 1, 0):
        salary_day = last_working_day(
            _month_start(today, months_ago).year, _month_start(today, months_ago).month
        )
        if salary_day <= today:
            await record_income(
                conn,
                account_id=sbi,
                amount_paise=15_000_000,
                description="Monthly salary",
                payee_id=employer,
                date=salary_day,
                idempotency_key=f"demo:salary:{salary_day.isoformat()}",
                actor="system",
            )
            await record_transfer(
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
            await record_expense(
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
                await record_expense(
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
            await record_expense(
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
            await record_expense(
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
            await record_expense(
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
            await record_loan_payment(
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
            await record_transfer(
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
            await record_expense(
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
            await record_transfer(
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
    await record_split(
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
    await record_settlement(
        conn,
        account_id=hdfc,
        payee_id=ravi,
        amount_paise=20_000,
        direction="they_paid",
        description="Ravi paid his share",
        date=today - timedelta(days=10),
        actor="system",
    )
    await record_lending(
        conn,
        account_id=hdfc,
        payee_id=ravi,
        amount_paise=200_000,
        expected_date=today + timedelta(days=30),
        description="Short loan to Ravi",
        date=today - timedelta(days=40),
        actor="system",
    )
    await record_lending(
        conn,
        account_id=hdfc,
        payee_id=employer,
        amount_paise=150_000,
        expected_date=today - timedelta(days=5),
        description="Internet bill paid for Acme",
        date=today - timedelta(days=6),
        actor="system",
    )
    await record_settlement(
        conn,
        account_id=hdfc,
        payee_id=employer,
        amount_paise=150_000,
        direction="they_paid",
        description="Internet reimbursement",
        date=today - timedelta(days=5),
        actor="system",
    )
    await record_refund(
        conn,
        account_id=hdfc,
        amount_paise=30_000,
        description="Swiggy refund",
        payee_id=swiggy,
        category_id=food,
        date=today - timedelta(days=4),
        actor="system",
    )

    dinner = await record_expense(
        conn,
        account_id=hdfc,
        amount_paise=250_000,
        description="Groceries, entered wrong",
        payee_id=bigbasket,
        category_id=groceries,
        date=today - timedelta(days=2),
        actor="system",
    )
    await correct_movement(
        conn,
        dinner["id"],
        changes={"amount_paise": 225_000},
        actor="system",
    )

    cash_balance = next(
        account["balance_paise"] for account in await list_accounts(conn) if account["id"] == cash
    )
    await set_statement_balance(
        conn,
        hdfc,
        balance_paise=next(
            account["balance_paise"]
            for account in await list_accounts(conn)
            if account["id"] == hdfc
        ),
        as_of=today,
        actor="system",
    )

    coverage = {"months": [], "gap": None, "imports": 0}
    if statement_coverage:
        coverage = await _seed_statement_coverage(
            conn,
            await list_accounts(conn),
            today,
            leave_gap=coverage_gap,
        )

    return {
        "today": today.isoformat(),
        "accounts": len(await list_accounts(conn, include_system=True)),
        "categories": len(await ensure_default_categories(conn)),
        "schedules": (await conn.fetchrow("SELECT count(*) AS n FROM schedules"))["n"],
        "goals": (await conn.fetchrow("SELECT count(*) AS n FROM goals"))["n"],
        "movements": (await conn.fetchrow("SELECT count(*) AS n FROM movements"))["n"],
        "imports": coverage["imports"],
        "statement_coverage": coverage,
        "people": (
            await conn.fetchrow(
                "SELECT count(*) AS n FROM payees WHERE kind IN ('person', 'organization')"
            )
        )["n"],
        "cash_balance_paise": cash_balance,
        "salary_schedule_id": salary,
        "epf_account_id": epf,
        "wallet_account_id": wallet,
        "hdfc_account_id": hdfc,
        "sbi_account_id": sbi,
        "loan_account_id": loan,
        "goal_account_id": goal_account,
    }


async def update_account_emi(conn: Connection, loan_account_id: str, schedule_id: str) -> None:
    """Point the loan at its EMI schedule. A small helper so the demo stays readable."""
    from fin_agent.ledger.accounts import update_account

    await update_account(conn, loan_account_id, emi_schedule_id=schedule_id, actor="system")


__all__ = ["DEMO_SETTINGS", "DemoSeedError", "seed_demo"]
