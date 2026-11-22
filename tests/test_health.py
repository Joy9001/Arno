from __future__ import annotations

import asyncio
import threading

import httpx

from arno.api.middleware import CONTENT_SECURITY_POLICY
from arno.auth.sessions import COOKIE_NAME
from arno.db.migrate import discover
from arno.tools import projection as projection_tools


def test_health_reports_database_and_schema_version(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "schema_version": len(discover()),
    }
    assert response.headers["cache-control"] == "no-store"


async def test_health_responds_while_projection_is_computing(session_client, monkeypatch):
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    project = projection_tools.run_projection

    def blocked_project(inputs, **kwargs):
        loop.call_soon_threadsafe(started.set)
        if not release.wait(timeout=5):
            raise AssertionError("health did not complete while projection was running")
        return project(inputs, **kwargs)

    monkeypatch.setattr(projection_tools, "run_projection", blocked_project)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=session_client.app),
        base_url="http://testserver",
        cookies={COOKIE_NAME: session_client.cookies[COOKIE_NAME]},
    ) as client:
        pending = asyncio.create_task(client.get("/api/projection"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            response = await asyncio.wait_for(client.get("/api/health"), timeout=3)
            assert response.status_code == 200
            assert not pending.done()
        finally:
            release.set()
            projection = await pending
        assert projection.status_code == 200


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
