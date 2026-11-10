from __future__ import annotations

from arno.api.middleware import CONTENT_SECURITY_POLICY
from arno.db.migrate import discover


def test_health_reports_database_and_schema_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "schema_version": len(discover()),
    }
    assert response.headers["cache-control"] == "no-store"


def test_every_response_carries_the_strict_policy(client):
    for path in ("/api/health", "/api/auth/state", "/api/nope"):
        response = client.get(path)
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_policy_blocks_inline_scripts_and_foreign_images(client):
    policy = client.get("/api/health").headers["content-security-policy"]

    assert "script-src 'self'" in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy
    assert "img-src 'self' blob: data:" in policy
    assert "http:" not in policy and "https:" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy


def test_unknown_api_route_is_a_problem_document(client):
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404


def test_trusted_host_rejects_a_foreign_host_header(client):
    response = client.get("/api/health", headers={"host": "evil.example"})

    assert response.status_code == 400
    assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
