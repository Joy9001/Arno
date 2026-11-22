"""Complete first-run passkey setup through the API."""

from fastapi.testclient import TestClient

from tests.software_authenticator import SoftwareAuthenticator


def complete_setup(client: TestClient, token: str, authenticator: SoftwareAuthenticator):
    begin = client.post("/api/auth/setup/begin", json={"token": token})
    assert begin.status_code == 200, begin.text
    challenge_id = begin.json()["challenge_id"]
    credential = authenticator.registration_credential(begin.json()["options"])
    return client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": challenge_id, "credential": credential},
    )
