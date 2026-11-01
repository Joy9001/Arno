"""WebAuthn ceremonies, kept free of FastAPI types."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
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


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _insert_challenge(
    conn: psycopg.Connection,
    *,
    kind: str,
    challenge: bytes,
    user_handle: bytes | None,
    user_id: str | None,
) -> str:
    conn.execute("DELETE FROM webauthn_challenges WHERE expires_at < now()")
    row = conn.execute(
        """
        INSERT INTO webauthn_challenges (kind, challenge, user_handle, user_id, expires_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (kind, challenge, user_handle, user_id, datetime.now(UTC) + CHALLENGE_TTL),
    ).fetchone()
    conn.commit()
    return row["id"]


def _pop_challenge(conn: psycopg.Connection, challenge_id: str, kind: str) -> Challenge:
    """Consume a challenge exactly once. A failed verification burns it too."""
    row = conn.execute(
        """
        UPDATE webauthn_challenges
        SET consumed_at = now()
        WHERE id = %s AND kind = %s AND consumed_at IS NULL AND expires_at > now()
        RETURNING id, challenge, user_handle, user_id
        """,
        (challenge_id, kind),
    ).fetchone()
    conn.commit()
    if row is None:
        raise PasskeyError("This sign-in attempt expired. Start again.")
    return Challenge(
        id=row["id"],
        challenge=row["challenge"],
        user_handle=row["user_handle"],
        user_id=row["user_id"],
    )


def begin_registration(
    conn: psycopg.Connection,
    *,
    rp_id: str,
    rp_name: str,
    user_handle: bytes,
    user_id: str | None,
    exclude_credential_ids: list[bytes],
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
    challenge_id = _insert_challenge(
        conn,
        kind="setup",
        challenge=options.challenge,
        user_handle=user_handle,
        user_id=user_id,
    )
    return challenge_id, json.loads(options_to_json(options))


def finish_registration(
    conn: psycopg.Connection,
    *,
    rp_id: str,
    rp_origin: str,
    challenge_id: str,
    credential: dict[str, Any],
) -> dict[str, Any]:
    challenge = _pop_challenge(conn, challenge_id, "setup")
    if challenge.user_handle is None:
        raise PasskeyError("This sign-in attempt expired. Start again.")

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

    if challenge.user_id is None:
        user = conn.execute(
            """
            INSERT INTO users (webauthn_user_handle, display_name)
            VALUES (%s, %s)
            RETURNING id, display_name
            """,
            (challenge.user_handle, "You"),
        ).fetchone()
    else:
        user = conn.execute(
            "SELECT id, display_name FROM users WHERE id = %s",
            (challenge.user_id,),
        ).fetchone()
        if user is None:
            raise PasskeyError("The user for this passkey no longer exists.")

    transports = credential.get("response", {}).get("transports") or []
    transports = [t for t in transports if t in VALID_TRANSPORTS]
    aaguid = str(verified.aaguid) if verified.aaguid else None

    conn.execute(
        """
        INSERT INTO webauthn_credentials (
            user_id, credential_id, public_key, sign_count, transports, aaguid, name
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user["id"],
            verified.credential_id,
            verified.credential_public_key,
            verified.sign_count,
            transports,
            aaguid,
            "Passkey",
        ),
    )
    conn.execute("UPDATE setup_tokens SET consumed_at = now() WHERE consumed_at IS NULL")
    conn.commit()
    return dict(user)


def begin_authentication(
    conn: psycopg.Connection,
    *,
    rp_id: str,
    user_id: str,
    credential_ids: list[bytes],
) -> tuple[str, dict[str, Any]]:
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=credential_id) for credential_id in credential_ids
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge_id = _insert_challenge(
        conn,
        kind="login",
        challenge=options.challenge,
        user_handle=None,
        user_id=user_id,
    )
    return challenge_id, json.loads(options_to_json(options))


def finish_authentication(
    conn: psycopg.Connection,
    *,
    rp_id: str,
    rp_origin: str,
    challenge_id: str,
    credential: dict[str, Any],
) -> dict[str, Any]:
    challenge = _pop_challenge(conn, challenge_id, "login")
    if challenge.user_id is None:
        raise PasskeyError("This sign-in attempt expired. Start again.")

    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        raise PasskeyError("That passkey response was incomplete.")
    try:
        credential_id = b64url_decode(raw_id)
    except ValueError as exc:
        raise PasskeyError("That passkey response was incomplete.") from exc

    stored = conn.execute(
        """
        SELECT id, public_key, sign_count
        FROM webauthn_credentials
        WHERE user_id = %s AND credential_id = %s
        """,
        (challenge.user_id, credential_id),
    ).fetchone()
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

    conn.execute(
        """
        UPDATE webauthn_credentials
        SET sign_count = %s, last_used_at = now()
        WHERE id = %s
        """,
        (verified.new_sign_count, stored["id"]),
    )
    user = conn.execute(
        "SELECT id, display_name FROM users WHERE id = %s",
        (challenge.user_id,),
    ).fetchone()
    conn.commit()
    if user is None:
        raise PasskeyError("The user for this passkey no longer exists.")
    return dict(user)


def new_user_handle() -> bytes:
    return secrets.token_bytes(16)
