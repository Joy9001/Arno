"""Contracts behind the Settings editing surfaces.

These cover the additive API fields and existing write endpoints the panel now uses, so the
UI's scheduled-question pause/edit, category rename/archive/restore, rule edit, and current
session summary stay honest.
"""

from __future__ import annotations


def test_session_exposes_only_safe_current_session_fields(authed_client):
    response = authed_client.get("/api/auth/session")
    assert response.status_code == 200
    payload = response.json()

    assert payload["authenticated"] is True
    assert payload["user"]["display_name"] == "You"
    assert set(payload) == {"authenticated", "user", "session"}
    assert set(payload["session"]) == {"id", "expires_at", "reverified_at", "fresh"}
    assert payload["session"]["expires_at"]
    assert payload["session"]["fresh"] is True


def test_scheduled_questions_include_inactive_is_opt_in_and_pause_is_reversible(authed_client):
    created = authed_client.post(
        "/api/scheduled-questions",
        json={
            "prompt": "Every Sunday, what did I spend on food?",
            "rule": "FREQ=WEEKLY;BYDAY=SU",
            "spend_limit_usd": 2,
        },
    )
    assert created.status_code == 201, created.text
    question = created.json()

    paused = authed_client.patch(
        f"/api/scheduled-questions/{question['id']}", json={"active": False}
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["active"] is False
    assert paused.json()["next_run_at"] is None

    default_list = authed_client.get("/api/scheduled-questions")
    assert all(item["id"] != question["id"] for item in default_list.json()["questions"])

    with_inactive = authed_client.get("/api/scheduled-questions?include_inactive=true")
    assert with_inactive.status_code == 200
    paused_row = next(
        item for item in with_inactive.json()["questions"] if item["id"] == question["id"]
    )
    assert paused_row["active"] is False

    edited = authed_client.patch(
        f"/api/scheduled-questions/{question['id']}",
        json={
            "prompt": "Every Sunday, what did I spend on groceries?",
            "rule": "FREQ=WEEKLY;BYDAY=MO",
            "spend_limit_usd": 3.25,
            "active": True,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["prompt"] == "Every Sunday, what did I spend on groceries?"
    assert edited.json()["rule"] == "FREQ=WEEKLY;BYDAY=MO"
    assert edited.json()["spend_limit_usd"] == "3.25"
    assert edited.json()["active"] is True
    assert edited.json()["next_run_at"] is not None


def test_category_rename_archive_and_restore_use_existing_patch_delete(authed_client):
    tree = authed_client.get("/api/categories").json()
    category = next(item for item in tree["categories"] if item["active"])
    renamed_name = f"{category['name']} (renamed)"

    renamed = authed_client.patch(f"/api/categories/{category['id']}", json={"name": renamed_name})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == renamed_name

    archived = authed_client.delete(f"/api/categories/{category['id']}")
    assert archived.status_code == 200, archived.text
    assert archived.json()["active"] is False

    tree = authed_client.get("/api/categories").json()
    archived_row = next(item for item in tree["categories"] if item["id"] == category["id"])
    assert archived_row["active"] is False
    assert archived_row["name"] == renamed_name

    restored = authed_client.patch(f"/api/categories/{category['id']}", json={"active": True})
    assert restored.status_code == 200, restored.text
    assert restored.json()["active"] is True


def test_rule_edit_upserts_one_rule_and_becomes_a_user_rule(authed_client):
    categories = [
        item for item in authed_client.get("/api/categories").json()["categories"] if item["active"]
    ][:2]
    assert len(categories) == 2

    created = authed_client.post(
        "/api/category-rules",
        json={
            "merchant_pattern": "Settings Test Merchant",
            "category_id": categories[0]["id"],
            "source": "llm",
        },
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["source"] == "llm"

    edited = authed_client.post(
        "/api/category-rules",
        json={
            "merchant_pattern": rule["merchant_pattern"],
            "category_id": categories[1]["id"],
            "source": "user",
        },
    )
    assert edited.status_code == 201, edited.text
    assert edited.json()["id"] == rule["id"]
    assert edited.json()["category_id"] == categories[1]["id"]
    assert edited.json()["source"] == "user"

    rules = authed_client.get("/api/category-rules").json()["items"]
    matching = [item for item in rules if item["id"] == rule["id"]]
    assert len(matching) == 1
    assert matching[0]["category_id"] == categories[1]["id"]
