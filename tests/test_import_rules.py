"""Import rules and categorization: a user's label and rule always win."""

from __future__ import annotations

from arno.importers import categorize as categorize_mod
from arno.importers import rules as rules_mod
from arno.importers.commit import commit_import
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
    food = await ledger.fetchrow("SELECT id, name FROM categories WHERE name = 'Food & Dining'")

    await categorize_mod._save(
        ledger,
        import_id,
        [categorize_mod.Answer(key="swiggy", category=food["name"], confidence="high")],
        {"food & dining": (food["id"], food["name"])},
    )

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
