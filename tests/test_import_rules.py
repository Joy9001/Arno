"""Import rules and categorization: a user's label and rule always win."""

from __future__ import annotations

from datetime import date

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from arno.agent.categorizer import CategorizerDeps, save_categories
from arno.importers import rules as rules_mod
from arno.importers.commit import commit_import
from arno.importers.rules import apply_rules, match_rule
from arno.importers.seed_rules import SEED_RULES, ensure_seed_rules
from arno.ledger import movements as movements_mod
from tests.fixtures import import_helpers as helpers


async def _committed_swiggy(ledger) -> tuple[str, str]:
    account = await helpers.bank(ledger)
    import_id = await helpers.review_import(
        ledger,
        account,
        [
            helpers.row(
                "UPI/DR/123456789012/SWIGGY/ramesh@okaxis",
                debit=45000,
                counterparty="Swiggy",
            )
        ],
    )
    await commit_import(ledger, import_id)
    movement_id = await ledger.fetchval(
        "SELECT movement_id FROM statement_lines WHERE import_id = $1", import_id
    )
    return import_id, str(movement_id)


async def test_a_user_category_survives_a_later_rule_run(ledger):
    import_id, movement_id = await _committed_swiggy(ledger)
    shopping = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Shopping'")
    await movements_mod.correct_movement(ledger, movement_id, changes={"category_id": shopping})
    food = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Food & Dining'")
    await ledger.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('contains', 'swiggy', $1, 'user')
        """,
        food,
    )

    await rules_mod.apply_rules(ledger, import_id)

    kept = await ledger.fetchval(
        """
        SELECT category_id FROM movement_legs
        WHERE movement_id = $1 AND category_id IS NOT NULL
        """,
        movement_id,
    )
    assert kept == shopping


async def test_a_user_rule_beats_a_later_llm_answer_for_the_same_payee(ledger):
    import_id, movement_id = await _committed_swiggy(ledger)
    # The user states the category while the categorizer job is still queued.
    shopping = await ledger.fetchval("SELECT id FROM categories WHERE name = 'Shopping'")
    await ledger.execute(
        """
        INSERT INTO category_rules (match, pattern, category_id, source)
        VALUES ('equals', 'swiggy', $1, 'user')
        """,
        shopping,
    )
    deps = CategorizerDeps(import_id=import_id, run_id="run")
    deps.answers["swiggy"] = ("Food & Dining", "model guess")
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    await save_categories(ctx, [])

    labeled = await ledger.fetchval(
        """
        SELECT category_id FROM movement_legs
        WHERE movement_id = $1 AND category_id IS NOT NULL
        """,
        movement_id,
    )
    assert labeled == shopping
    assert await ledger.fetchval("SELECT source FROM category_rules WHERE pattern = 'swiggy'") == (
        "user"
    )


def _seed_rule(pattern: str, match: str = "contains") -> dict:
    return {"match": match, "pattern": pattern, "source": "seed", "priority": 0}


def test_card_fee_narrations_reach_fees_and_charges():
    rules = [
        {"id": str(index), **_seed_rule(pattern, match), "category_id": "c", "category_name": name}
        for index, (name, match, pattern) in enumerate(SEED_RULES)
    ]
    for narration in (
        "ANNUAL FEE",
        "ANNUAL FEE CHARGES",
        "GST ON FEES",
        "IGST ON FEES",
        "SURCHARGE",
        "INTEREST CHARGES",
    ):
        rule = match_rule(narration.lower(), rules)
        assert rule is not None, narration
        assert rule["category_name"] == "Fees & Charges", narration


async def test_an_imported_card_fee_is_categorized_by_a_rule(ledger):
    await ensure_seed_rules(ledger)
    account = await helpers.card(ledger)
    import_id = await helpers.review_import(
        ledger,
        account,
        [helpers.row("ANNUAL FEE", debit=99900, day="2026-11-10")],
        opening=0,
        closing=-99900,
        period=(date(2026, 10, 25), date(2026, 11, 10)),
    )
    await commit_import(ledger, import_id)

    counts = await apply_rules(ledger, import_id)
    assert counts["labeled"] == 1
    fee = await ledger.fetchrow(
        """
        SELECT c.name
        FROM statement_lines sl
        JOIN movement_legs tl ON tl.movement_id = sl.movement_id
        JOIN categories c ON c.id = tl.category_id
        WHERE sl.import_id = $1
        """,
        import_id,
    )
    assert fee is not None and fee["name"] == "Fees & Charges"
