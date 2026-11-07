"""WebAuthn ceremonies, kept free of FastAPI types."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from fin_agent.auth.setup import ADD_DEVICE, FIRST_RUN, consume_setup_token, setup_required
from fin_agent.db.conn import Connection

CHALLENGE_TTL = timedelta(minutes=5)
VALID_TRANSPORTS = {"usb", "nfc", "ble", "internal", "hybrid", "smart-card"}


class PasskeyError(Exception):
    """A ceremony failed. The message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class Challenge:
    id: str
    challenge: bytes
    user_handle: bytes | None
    user_id: str | None
    setup_token_id: str | None


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


async def _insert_challenge(
    conn: Connection,
    *,
    kind: str,
    challenge: bytes,
    user_handle: bytes | None,
    user_id: str | None,
    setup_token_id: str | None = None,
) -> str:
    await conn.execute("DELETE FROM webauthn_challenges WHERE expires_at < now()")
    row = await conn.fetchrow(
        """
        INSERT INTO webauthn_challenges (
            kind, challenge, user_handle, user_id, setup_token_id, expires_at
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        kind,
        challenge,
        user_handle,
        user_id,
        setup_token_id,
        datetime.now(UTC) + CHALLENGE_TTL,
    )
    return row["id"]


async def _pop_challenge(conn: Connection, challenge_id: str, kind: str) -> Challenge:
    """Consume a challenge exactly once. A failed verification burns it too."""
    row = await conn.fetchrow(
        """
        UPDATE webauthn_challenges
        SET consumed_at = now()
        WHERE id = $1 AND kind = $2 AND consumed_at IS NULL AND expires_at > now()
        RETURNING id, challenge, user_handle, user_id, setup_token_id
        """,
        challenge_id,
        kind,
    )
    if row is None:
        raise PasskeyError("This sign-in attempt expired. Start again.")
    return Challenge(
        id=row["id"],
        challenge=row["challenge"],
        user_handle=row["user_handle"],
        user_id=row["user_id"],
        setup_token_id=row["setup_token_id"],
    )


async def begin_registration(
    conn: Connection,
    *,
    rp_id: str,
    rp_name: str,
    user_handle: bytes,
    user_id: str | None,
    exclude_credential_ids: list[bytes],
    setup_token_id: str,
) -> tuple[str, dict[str, Any]]:
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name="owner",
        user_display_name="You",
        user_id=user_handle,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=credential_id)
            for credential_id in exclude_credential_ids
        ],
    )
    challenge_id = await _insert_challenge(
        conn,
        kind="setup",
        challenge=options.challenge,
        user_handle=user_handle,
        user_id=user_id,
        setup_token_id=setup_token_id,
    )
    return challenge_id, json.loads(options_to_json(options))


async def finish_registration(
    conn: Connection,
    *,
    rp_id: str,
    rp_origin: str,
    challenge_id: str,
    credential: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    challenge = await _pop_challenge(conn, challenge_id, "setup")
    if challenge.user_handle is None or challenge.setup_token_id is None:
        raise PasskeyError("This enrollment link expired. Start again.")

    token = await conn.fetchrow(
        """
        SELECT id, kind, user_id
        FROM setup_tokens
        WHERE id = $1 AND consumed_at IS NULL AND expires_at > now()
        """,
        challenge.setup_token_id,
    )
    if token is None:
        raise PasskeyError("This enrollment link expired or was already used.")

    setup_open = await setup_required(conn)
    if token["kind"] == FIRST_RUN:
        if not setup_open:
            raise PasskeyError(
                "Setup is already complete. Open Settings → Passkeys on a signed-in device "
                "to add this one."
            )
        if token["user_id"] is not None:
            raise PasskeyError("This enrollment link is not valid for first-run setup.")
    elif token["kind"] == ADD_DEVICE:
        if setup_open:
            raise PasskeyError("First-run setup is not complete yet.")
        if token["user_id"] is None or challenge.user_id != token["user_id"]:
            raise PasskeyError("This enrollment link is not valid for this user.")
    else:
        raise PasskeyError("This enrollment link is not valid.")

    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=rp_id,
            expected_origin=rp_origin,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - the library raises several types
        raise PasskeyError("That passkey could not be verified. Try again.") from exc

    transports = credential.get("response", {}).get("transports") or []
    transports = [t for t in transports if t in VALID_TRANSPORTS]
    aaguid = str(verified.aaguid) if verified.aaguid else None

    async with conn.transaction():
        if challenge.user_id is None:
            user = await conn.fetchrow(
                """
                INSERT INTO users (webauthn_user_handle, display_name)
                VALUES ($1, $2)
                RETURNING id, display_name
                """,
                challenge.user_handle,
                "You",
            )
        else:
            user = await conn.fetchrow(
                "SELECT id, display_name FROM users WHERE id = $1",
                challenge.user_id,
            )
            if user is None:
                raise PasskeyError("The user for this passkey no longer exists.")

        await conn.execute(
            """
            INSERT INTO webauthn_credentials (
                user_id, credential_id, public_key, sign_count, transports, aaguid, name
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            user["id"],
            verified.credential_id,
            verified.credential_public_key,
            verified.sign_count,
            transports,
            aaguid,
            "Passkey",
        )
        await consume_setup_token(conn, token["id"])
    return user, token["kind"]


async def begin_authentication(
    conn: Connection,
    *,
    rp_id: str,
    user_id: str,
    credential_ids: list[bytes],
    kind: str = "login",
) -> tuple[str, dict[str, Any]]:
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=credential_id) for credential_id in credential_ids
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = await _insert_challenge(
        conn,
        kind=kind,
        challenge=options.challenge,
        user_handle=None,
        user_id=user_id,
    )
    return challenge_id, json.loads(options_to_json(options))


async def finish_authentication(
    conn: Connection,
    *,
    rp_id: str,
    rp_origin: str,
    challenge_id: str,
    credential: dict[str, Any],
    kind: str = "login",
) -> dict[str, Any]:
    challenge = await _pop_challenge(conn, challenge_id, kind)
    if challenge.user_id is None:
        raise PasskeyError("This sign-in attempt expired. Start again.")

    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        raise PasskeyError("That passkey response was incomplete.")
    try:
        credential_id = b64url_decode(raw_id)
    except ValueError as exc:
        raise PasskeyError("That passkey response was incomplete.") from exc

    stored = await conn.fetchrow(
        """
        SELECT id, public_key, sign_count
        FROM webauthn_credentials
        WHERE user_id = $1 AND credential_id = $2
        """,
        challenge.user_id,
        credential_id,
    )
    if stored is None:
        raise PasskeyError("That passkey is not registered on this instance.")

    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=rp_id,
            expected_origin=rp_origin,
            credential_public_key=stored["public_key"],
            credential_current_sign_count=stored["sign_count"],
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - the library raises several types
        raise PasskeyError("That passkey could not be verified. Try again.") from exc

    # A counter that goes backwards means a cloned authenticator. Passkeys that always report
    # zero (many synced ones) are exempt.
    if (
        stored["sign_count"] > 0
        and verified.new_sign_count > 0
        and verified.new_sign_count <= stored["sign_count"]
    ):
        raise PasskeyError("That passkey's counter went backwards; it may be cloned.")

    await conn.execute(
        """
        UPDATE webauthn_credentials
        SET sign_count = $1, last_used_at = now()
        WHERE id = $2
        """,
        verified.new_sign_count,
        stored["id"],
    )
    user = await conn.fetchrow(
        "SELECT id, display_name FROM users WHERE id = $1",
        challenge.user_id,
    )
    if user is None:
        raise PasskeyError("The user for this passkey no longer exists.")
    return user


def new_user_handle() -> bytes:
    return secrets.token_bytes(16)
