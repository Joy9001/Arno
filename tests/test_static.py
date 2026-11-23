from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from arno.api.app import create_app
from arno.config import Settings

INDEX = """<!doctype html>
<html lang="en">
  <head><title>Arno</title></head>
  <body><div id="root"></div><script type="module" src="/assets/app.js"></script></body>
</html>
"""


def _client(tmp_path: Path, settings: Settings) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX, encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
    return TestClient(create_app(replace(settings, web_dir=tmp_path)))


def test_web_shell_is_served_with_the_policy_and_no_inline_script(
    tmp_path, settings, clean_database
):
    with _client(tmp_path, settings) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "<script>" not in response.text
    assert 'src="/assets/app.js"' in response.text


def test_hashed_assets_are_cached_immutably(tmp_path, settings, clean_database):
    with _client(tmp_path, settings) as client:
        response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_unknown_page_paths_fall_back_to_the_shell(tmp_path, settings, clean_database):
    with _client(tmp_path, settings) as client:
        response = client.get("/threads/abc")

    assert response.status_code == 200
    assert "Arno" in response.text


def test_a_host_probe_for_a_health_or_model_endpoint_is_a_plain_404(
    tmp_path, settings, clean_database
):
    with _client(tmp_path, settings) as client:
        for path in ("/health", "/v1/models", "/v1/chat/completions"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert response.headers["content-type"].startswith("application/problem+json")
            assert "text/html" not in response.headers["content-type"]


def test_a_missing_asset_is_a_404_not_the_shell(tmp_path, settings, clean_database):
    with _client(tmp_path, settings) as client:
        response = client.get("/assets/missing.js")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_api_paths_never_fall_back_to_the_shell(tmp_path, settings, clean_database):
    with _client(tmp_path, settings) as client:
        response = client.get("/api/not-a-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_missing_build_is_a_plain_503(tmp_path, settings, clean_database):
    missing = tmp_path / "missing"
    with TestClient(create_app(replace(settings, web_dir=missing))) as client:
        response = client.get("/")

    assert response.status_code == 503
    assert "Web build missing" in response.json()["title"]


def test_a_built_asset_resolved_outside_the_build_is_a_404_not_the_shell(
    tmp_path, settings, clean_database, monkeypatch
):
    build = tmp_path / "dist"
    sibling = tmp_path / "dist-extras"
    build.mkdir()
    sibling.mkdir()
    external = sibling / "app.js"
    external.write_text("External fixture", encoding="utf-8")
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == build / "assets" / "app.js":
            return real_resolve(external)
        return real_resolve(path, *args, **kwargs)

    # Model an asset resolved through a filesystem link on platforms without symlink privileges.
    monkeypatch.setattr(Path, "resolve", resolve)
    with _client(build, settings) as client:
        response = client.get("/assets/app.js")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
