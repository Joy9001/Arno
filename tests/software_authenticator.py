"""A software WebAuthn authenticator for tests.

py_webauthn has no authenticator simulator, so this builds the same bytes a platform passkey
would: attested credential data with a P-256 COSE key, and an ECDSA assertion over
authenticator data plus the client-data hash. Nothing here touches the network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04
FLAG_ATTESTED_CREDENTIAL_DATA = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _client_data(kind: str, challenge: str, origin: str) -> bytes:
    return json.dumps(
        {
            "type": kind,
            "challenge": challenge,
            "origin": origin,
            "crossOrigin": False,
        },
        separators=(",", ":"),
    ).encode("utf-8")


class SoftwareAuthenticator:
    def __init__(self, rp_id: str, origin: str) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0

    def _cose_public_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,  # kty: EC2
                3: -7,  # alg: ES256
                -1: 1,  # crv: P-256
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _authenticator_data(self, flags: int, *, attested: bool) -> bytes:
        data = hashlib.sha256(self.rp_id.encode("utf-8")).digest()
        data += bytes([flags]) + self.sign_count.to_bytes(4, "big")
        if attested:
            data += b"\x00" * 16  # AAGUID
            data += len(self.credential_id).to_bytes(2, "big")
            data += self.credential_id
            data += self._cose_public_key()
        return data

    def registration_credential(self, options: dict[str, Any]) -> dict[str, Any]:
        client_data = _client_data("webauthn.create", options["challenge"], self.origin)
        auth_data = self._authenticator_data(
            FLAG_USER_PRESENT | FLAG_USER_VERIFIED | FLAG_ATTESTED_CREDENTIAL_DATA,
            attested=True,
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def authentication_credential(
        self, options: dict[str, Any], *, user_handle: bytes | None = None
    ) -> dict[str, Any]:
        client_data = _client_data("webauthn.get", options["challenge"], self.origin)
        self.sign_count += 1
        auth_data = self._authenticator_data(FLAG_USER_PRESENT | FLAG_USER_VERIFIED, attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        response: dict[str, Any] = {
            "clientDataJSON": b64url(client_data),
            "authenticatorData": b64url(auth_data),
            "signature": b64url(signature),
        }
        if user_handle is not None:
            response["userHandle"] = b64url(user_handle)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
