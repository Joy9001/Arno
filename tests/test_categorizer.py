"""Rules, the one-call categorizer, and the Uncategorized list."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from arno.agent.usage import DailyBudgetExceeded
from arno.importers import rules as rules_mod
from arno.importers.categorize import run_categorize
from arno.importers.commit import commit_import
from arno.importers.reconcile import insert_lines, reconcile
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id, undo_group
from tests.fixtures.categorizer_model import answering

CATEGORY = "Food & Dining"


async def _account(conn, *, name: str = "SBI savings", last4: str = "6789") -> dict:
    return await create_account(
        conn,
        kind="bank",
        name=name,
        institution="SBI",
        last4=last4,
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _committed(conn, account: dict, rows: list[dict]) -> str:
    """One committed import holding these rows, with no labels from suggestions."""
    raw_account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", account["id"])
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, period_start, period_end,
                             commit_groups)
        VALUES ($1, $2, 'review', $3, '2026-04-01', '2026-04-30', '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
    )
    await insert_lines(
        conn, import_id=import_id, account=raw_account, parsed_account={"rows": rows}
    )
    await reconcile(
        conn,
        import_id=import_id,
        account=raw_account,
        period=(date(2026, 4, 1), date(2026, 4, 30)),
    )
    await conn.execute(
        "UPDATE imports SET check_result = $1, check_status = 'VERIFIED', review = $2 "
        "WHERE id = $3",
        {"opening_source": "manual", "opening_paise": 0},
        {"manual_opening_paise": 0},
        import_id,
    )
    await commit_import(conn, import_id)
    return import_id


def _row(
    description: str,
    *,
    debit: int = 0,
    credit: int = 0,
    day: str = "2026-04-01",
    counterparty: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "date": day,
        "description": description,
        "debit": debit,
        "credit": credit,
        "balance": 24567890,
    }
    if counterparty:
        row["counterparty"] = counterparty
    return row


async def _category_id(conn, name: str = CATEGORY) -> str:
    return str(await conn.fetchval("SELECT id FROM categories WHERE name = $1", name))


async def _matching_rule(
    conn,
    query: str,
    category_id: str,
    *,
    source: str = "user",
    match: str = "contains",
    priority: int = 0,
) -> str:
    return str(
        await conn.fetchval(
            """
            INSERT INTO category_rules (match, pattern, category_id, source, priority)
            VALUES ($1, $2, $3, $4, $5) RETURNING id
            """,
            match,
            query,
            category_id,
            source,
            priority,
        )
    )


def _movement_ids(conn, import_id: str):
    return conn.fetch(
        """
        SELECT m.id, sl.description_raw, sl.match_state
        FROM statement_lines sl JOIN movements m ON m.id = sl.movement_id
        WHERE sl.import_id = $1
        """,
        import_id,
    )


# Rules engine -----------------------------------------------------------------------------


def test_rail_tokens_and_handles_are_stripped():
    assert rules_mod.payee_key("UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis") == (
        "swiggy bangalore"
    )
    assert rules_mod.payee_key("NEFT/IMPS/998877665544/RAMESH K/lunch", "Ramesh K") == "ramesh k"


def test_each_match_type():
    equals = {"match": "equals", "pattern": "swiggy", "source": "user", "priority": 0}
    contains = {"match": "contains", "pattern": "zomato", "source": "user", "priority": 0}
    regex = {"match": "regex", "pattern": r"\bswiggy\b", "source": "user", "priority": 0}
    assert rules_mod.match_rule("swiggy", [equals]) is equals
    assert rules_mod.match_rule("swiggy bengaluru", [equals]) is None
    assert rules_mod.match_rule("zomato87", [contains]) is contains
    assert rules_mod.match_rule("UPI SWIGGY 12", [regex]) is regex
    assert rules_mod.match_rule("swiggyish", [regex]) is None


def test_precedence_across_sources_and_priority():
    rules = [
        {"match": "contains", "pattern": "swiggy", "source": "llm", "priority": 99},
        {"match": "equals", "pattern": "swiggy bengaluru", "source": "user", "priority": 0},
    ]
    chosen = rules_mod.match_rule("swiggy bengaluru", rules)
    assert chosen is not None and chosen["source"] == "user"

    same_source = [
        {"match": "contains", "pattern": "swiggy", "source": "user", "priority": 0},
        {"match": "contains", "pattern": "swiggy b", "source": "user", "priority": 10},
    ]
    assert rules_mod.match_rule("swiggy bengaluru", same_source) is same_source[1]

    tie = [
        {"match": "contains", "pattern": "swiggy", "source": "user", "priority": 0},
        {"match": "contains", "pattern": "swiggy bengaluru", "source": "user", "priority": 0},
    ]
    assert rules_mod.match_rule("swiggy bengaluru", tie) is tie[1]


async def test_rules_label_rows_record_the_rule_and_increment_hits(ledger):
    account = await _account(ledger)
    rule_id = await _matching_rule(ledger, "swiggy", await _category_id(ledger))
    import_id = await _committed(
        ledger,
        account,
        [
            _row(
                "UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis",
                debit=45000,
                counterparty="Swiggy",
            ),
            _row(
                "UPI/DR/123456789013/SWIGGY BANGALORE/ramesh@okaxis",
                debit=30000,
                day="2026-04-02",
                counterparty="Swiggy",
            ),
        ],
    )

    result = await rules_mod.apply_rules(ledger, import_id)

    assert result == {"labeled": 2, "rows": 2}
    movements = await ledger.fetch(
        "SELECT category_rule_id FROM movements WHERE id IN "
        "(SELECT movement_id FROM statement_lines WHERE import_id = $1)",
        import_id,
    )
    assert [row["category_rule_id"] for row in movements] == [rule_id, rule_id]
    assert await ledger.fetchval("SELECT hits FROM category_rules WHERE id = $1", rule_id) == 2


async def test_a_refund_takes_the_purchase_category(ledger):
    account = await _account(ledger)
    rule_id = await _matching_rule(ledger, "swiggy", await _category_id(ledger))
    import_id = await _committed(
        ledger,
        account,
        [
            _row("UPI/DR/123456789012/SWIGGY/ramesh@okaxis", debit=45000, counterparty="Swiggy"),
            _row(
                "UPI/CR/123456789013/SWIGGY/ramesh@okaxis",
                credit=30000,
                day="2026-04-10",
                counterparty="Swiggy",
            ),
        ],
    )

    await rules_mod.apply_rules(ledger, import_id)

    credit = await ledger.fetchrow(
        """
        SELECT label.category_id, m.category_rule_id
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        JOIN LATERAL (
            SELECT tl.category_id FROM movement_legs tl
            WHERE tl.movement_id = m.id AND tl.category_id IS NOT NULL LIMIT 1
        ) label ON true
        WHERE sl.import_id = $1 AND sl.amount_paise > 0
        """,
        import_id,
    )
    assert credit["category_id"] == await _category_id(ledger)
    assert credit["category_rule_id"] == rule_id


async def test_deleting_a_rule_keeps_the_label_it_put_on_a_row(ledger):
    from arno.ledger import categories

    account = await _account(ledger)
    rule_id = await _matching_rule(ledger, "swiggy", await _category_id(ledger))
    import_id = await _committed(
        ledger,
        account,
        [_row("UPI/DR/123456789012/SWIGGY", debit=45000, counterparty="Swiggy")],
    )
    await rules_mod.apply_rules(ledger, import_id)

    await categories.delete_rule(ledger, rule_id)

    movement = await ledger.fetchrow(
        """
        SELECT m.category_rule_id FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id WHERE sl.import_id = $1
        """,
        import_id,
    )
    assert movement["category_rule_id"] is None
    assert (
        await ledger.fetchval("SELECT count(*) FROM movement_legs WHERE category_id IS NOT NULL")
        == 1
    )


async def test_commit_labels_nothing_and_queues_the_categorizer(ledger):
    account = await _account(ledger)
    await _matching_rule(ledger, "swiggy", await _category_id(ledger))
    import_id = await _committed(
        ledger, account, [_row("UPI/DR/123456789012/SWIGGY", debit=45000, counterparty="Swiggy")]
    )

    labels = await ledger.fetch(
        """
        SELECT tl.category_id
        FROM statement_lines sl JOIN movement_legs tl ON tl.movement_id = sl.movement_id
        WHERE sl.import_id = $1
        """,
        import_id,
    )
    assert [row["category_id"] for row in labels] == [None]
    assert (
        await ledger.fetchval("SELECT categorize ->> 'state' FROM imports WHERE id = $1", import_id)
        == "queued"
    )


# The categorizer ---------------------------------------------------------------------------


def test_payment_apps_are_never_name_rules():
    for key in ("paytm", "one97 communications", "phonepe", "razorpay pvt", "cred", "gpay"):
        assert rules_mod.is_payment_app(key), key
    assert not rules_mod.is_payment_app("swiggy")


async def _states(conn, import_id: str) -> dict[str, tuple[str | None, dict | None]]:
    rows = await conn.fetch(
        """
        SELECT sl.description_raw, m.category_hint, c.name AS category
        FROM statement_lines sl
        JOIN movements m ON m.id = sl.movement_id
        LEFT JOIN movement_legs ml ON ml.movement_id = m.id AND ml.category_id IS NOT NULL
        LEFT JOIN categories c ON c.id = ml.category_id
        WHERE sl.import_id = $1
        """,
        import_id,
    )
    return {row["description_raw"]: (row["category"], row["category_hint"]) for row in rows}


def _shops(count: int) -> list[dict[str, Any]]:
    return [
        _row(f"UPI/DR/1/SHOP{i:02d} STORE", debit=1000 + i, counterparty=f"Shop {i:02d}")
        for i in range(count)
    ]


async def test_rules_label_first_and_the_model_gets_only_the_rest(ledger):
    account = await _account(ledger)
    await _matching_rule(ledger, "swiggy", await _category_id(ledger))
    import_id = await _committed(
        ledger,
        account,
        [
            _row("UPI/DR/1/SWIGGY/a@okaxis", debit=45000, counterparty="Swiggy"),
            _row("UPI/DR/2/ALPHA STORE", debit=20000, day="2026-04-02", counterparty="Alpha Store"),
        ],
    )
    chunks: list[int] = []

    result = await run_categorize(ledger, import_id, model=answering("Shopping", chunks=chunks))

    assert result["status"] == "done" and chunks == [1]
    assert (result["labeled"], result["uncategorized"]) == (2, 0)
    states = await _states(ledger, import_id)
    assert states["UPI/DR/1/SWIGGY/a@okaxis"][0] == CATEGORY
    assert states["UPI/DR/2/ALPHA STORE"][0] == "Shopping"


async def test_payees_go_out_in_parallel_chunks_of_the_chunk_size(ledger):
    import_id = await _committed(ledger, await _account(ledger), _shops(75))
    chunks: list[int] = []

    result = await run_categorize(ledger, import_id, model=answering("Shopping", chunks=chunks))

    assert sorted(chunks) == [5, 35, 35]
    assert result["status"] == "done" and result["labeled"] == 75


async def test_high_labels_and_learns_a_rule_while_low_and_unknown_stay_uncategorized(ledger):
    account = await _account(ledger)
    import_id = await _committed(
        ledger,
        account,
        [
            _row("UPI/DR/1/ALPHA STORE", debit=20000, counterparty="Alpha Store"),
            _row("UPI/DR/2/BETA STORE", debit=30000, day="2026-04-02", counterparty="Beta Store"),
            _row("UPI/DR/3/GAMMA STORE", debit=40000, day="2026-04-03", counterparty="Gamma"),
            _row("UPI/DR/4/DELTA STORE", debit=50000, day="2026-04-04", counterparty="Delta"),
            _row("UPI/DR/5/PAYTM", debit=60000, day="2026-04-05", counterparty="Paytm"),
        ],
    )
    model = answering(
        per_key={
            "alpha store": ("Shopping", "high"),
            "beta store": ("Shopping", "low"),
            "gamma": ("Not A Category", "high"),
            "delta": ("Other", "high"),
            "paytm": ("Shopping", "high"),
        }
    )

    result = await run_categorize(ledger, import_id, model=model)

    states = await _states(ledger, import_id)
    assert states["UPI/DR/1/ALPHA STORE"][0] == "Shopping"
    for narration in ("2/BETA STORE", "3/GAMMA STORE", "4/DELTA STORE", "5/PAYTM"):
        category, hint = states["UPI/DR/" + narration]
        assert category is None and hint is not None, narration
    beta_hint = states["UPI/DR/2/BETA STORE"][1] or {}
    gamma_hint = states["UPI/DR/3/GAMMA STORE"][1] or {}
    assert beta_hint["category_id"] == await _category_id(ledger, "Shopping")
    assert gamma_hint["category_id"] is None
    rules = await ledger.fetch("SELECT pattern, source FROM category_rules")
    assert [(r["pattern"], r["source"]) for r in rules] == [("alpha store", "llm")]
    assert (result["labeled"], result["uncategorized"]) == (1, 4)


async def test_a_failed_chunk_makes_the_run_partial_and_keeps_the_other_answers(ledger):
    import_id = await _committed(ledger, await _account(ledger), _shops(40))
    model = answering("Shopping", fail_when=lambda keys: len(keys) < 35)

    result = await run_categorize(ledger, import_id, model=model)

    assert result["status"] == "partial" and result["failed_payees"] == 5
    assert (result["labeled"], result["uncategorized"]) == (35, 5)
    body = await ledger.fetchval("SELECT body FROM notifications WHERE kind = 'categorize_done'")
    assert "5 payees" in body and "shop" not in body.lower()


async def test_a_spend_stop_fails_the_run_and_writes_nothing(ledger):
    import_id = await _committed(
        ledger,
        await _account(ledger),
        [_row("UPI/DR/1/ALPHA STORE", debit=20000, counterparty="Alpha Store")],
    )

    def stop(keys: list[str]) -> bool:
        raise DailyBudgetExceeded("limit")

    with pytest.raises(DailyBudgetExceeded):
        await run_categorize(ledger, import_id, model=answering(fail_when=stop))
    assert await ledger.fetchval("SELECT count(*) FROM category_rules") == 0


async def test_undoing_the_categorize_group_removes_its_labels_and_rules(ledger):
    import_id = await _committed(
        ledger,
        await _account(ledger),
        [_row("UPI/DR/1/ALPHA STORE", debit=20000, counterparty="Alpha Store")],
    )
    await run_categorize(ledger, import_id, model=answering("Shopping"))
    groups = await ledger.fetchval("SELECT categorize_groups FROM imports WHERE id = $1", import_id)
    assert len(groups) == 1

    await undo_group(ledger, groups[0])

    assert await ledger.fetchval("SELECT count(*) FROM category_rules") == 0
    assert (await _states(ledger, import_id))["UPI/DR/1/ALPHA STORE"][0] is None


# The Uncategorized list -------------------------------------------------------------------


async def test_uncategorized_groups_merchants_and_upi_people(ledger):
    account = await _account(ledger)
    await _committed(
        ledger,
        account,
        [
            _row("UPI/DR/1/SWIGGY BANGALORE/merchant@okaxis", debit=45000, counterparty="Swiggy"),
            _row(
                "UPI/DR/2/SWIGGY BANGALORE/merchant@okaxis",
                debit=30000,
                day="2026-04-02",
                counterparty="Swiggy",
            ),
            _row(
                "UPI/DR/3/RAMESH K/ramesh@okaxis/lunch",
                debit=1840000,
                day="2026-04-03",
                counterparty="Ramesh K",
            ),
        ],
    )

    groups = await uncategorized_groups(ledger)
    by_key = {group["key"]: group for group in groups["groups"]}
    assert groups["total"] == 3
    assert by_key["swiggy"]["kind"] == "merchant"
    assert by_key["swiggy"]["count"] == 2
    assert by_key["ramesh k"]["kind"] == "person"
    assert by_key["ramesh k"]["total_paise"] == 1840000


async def uncategorized_groups(conn):
    from arno.ledger import uncategorized

    return await uncategorized.groups(conn)


async def test_answering_a_group_writes_a_user_rule_and_one_undo_reverts_it(ledger):
    from arno.ledger import uncategorized

    account = await _account(ledger)
    await _committed(
        ledger,
        account,
        [
            _row("UPI/DR/1/SWIGGY BANGALORE/merchant@okaxis", debit=45000, counterparty="Swiggy"),
            _row(
                "UPI/DR/2/SWIGGY BANGALORE/merchant@okaxis",
                debit=30000,
                day="2026-04-02",
                counterparty="Swiggy",
            ),
        ],
    )
    category_id = await _category_id(ledger)

    result = await uncategorized.answer(ledger, key="swiggy", category_id=category_id)

    assert result["labeled"] == 2
    rule = await ledger.fetchrow("SELECT * FROM category_rules WHERE pattern = 'swiggy'")
    assert rule["source"] == "user" and rule["match"] == "equals"
    labels = await ledger.fetch(
        """
        SELECT tl.category_id FROM statement_lines sl
        JOIN movement_legs tl ON tl.movement_id = sl.movement_id
        WHERE sl.import_id IN (SELECT id FROM imports) ORDER BY sl.date
        """
    )
    assert [row["category_id"] for row in labels] == [category_id, category_id]

    await undo_group(ledger, result["undo_id"])
    assert await ledger.fetchval("SELECT count(*) FROM category_rules") == 0
    labels = await ledger.fetch(
        """
        SELECT tl.category_id
        FROM statement_lines sl JOIN movement_legs tl ON tl.movement_id = sl.movement_id
        ORDER BY sl.date
        """
    )
    assert [row["category_id"] for row in labels] == [None, None]


def test_uncategorized_routes_refuse_a_request_without_a_session(client):
    assert client.get("/api/uncategorized").status_code == 401
    assert (
        client.post(
            "/api/uncategorized/answer", json={"key": "swiggy", "category_id": "x"}
        ).status_code
        == 401
    )


async def test_uncategorized_routes_answer_with_a_session(session_client, ledger):
    payload = session_client.get("/api/uncategorized")
    assert payload.status_code == 200
    assert payload.json()["groups"] == []
