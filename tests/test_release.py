from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_mit_and_matches_the_package_metadata():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]


def test_compose_pins_a_versioned_image_and_rotates_logs():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert f"ghcr.io/joy9001/arno:${{ARNO_VERSION:-{version}}}" in compose
    assert 'max-size: "10m"' in compose
    assert "pre-migration-data:/app/pre-migration" in compose


def test_every_version_mirror_matches_pyproject():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    web = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    init = (ROOT / "src" / "arno" / "__init__.py").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert web["version"] == version
    assert f'__version__ = "{version}"' in init
    assert f"ARNO_VERSION={version}\n" in example


def test_release_workflow_pushes_the_pinned_images():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "ghcr.io/joy9001/arno" in workflow
    assert "docker/build-push-action" in workflow


def test_phoenix_bearer_is_wired_in_compose():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "PHOENIX_API_KEY: ${PHOENIX_API_KEY:-${PHOENIX_ADMIN_SECRET:-}}" in compose


def test_compose_initializes_non_superuser_app_role():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    app_role = (ROOT / "deploy/app-role.sh").read_text(encoding="utf-8")

    assert "POSTGRES_USER:" not in compose
    assert "POSTGRES_DB:" not in compose
    assert "./deploy/app-role.sh:/docker-entrypoint-initdb.d/10-app-role.sh:ro" in compose
    assert "NOSUPERUSER NOBYPASSRLS NOREPLICATION" in app_role
    assert "GRANT pg_signal_backend TO app" in app_role
    assert "ALTER ROLE postgres PASSWORD NULL" in app_role


def _compose_api_environment(overrides: dict[str, str]) -> dict[str, str]:
    if shutil.which("docker") is None:
        pytest.skip("docker compose is unavailable")
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / "empty.env"
        env_file.write_text("", encoding="utf-8")
        env = os.environ.copy()
        env.pop("PHOENIX_API_KEY", None)
        env.pop("PHOENIX_ADMIN_SECRET", None)
        env.update(
            {
                "POSTGRES_PASSWORD": "dummy-postgres-password",
                "BACKUP_PASSWORD": "dummy-backup-password",
                "RESTIC_PASSWORD": "dummy-restic-password",
                "ARNO_ENV_FILE": str(env_file),
                **overrides,
            }
        )
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(ROOT / "compose.yaml"),
                "config",
                "--format",
                "json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    if result.returncode != 0 and "is not a docker command" in result.stderr:
        pytest.skip(f"docker compose config is unavailable: {result.stderr.strip()[:200]}")
    assert result.returncode == 0, result.stderr.strip()[:300]
    return json.loads(result.stdout)["services"]["api"]["environment"]


def test_compose_phoenix_bearer_falls_back_and_allows_override():
    admin_secret = "dummy-admin-secret-for-test"
    fallback = _compose_api_environment({"PHOENIX_ADMIN_SECRET": admin_secret})
    assert fallback["PHOENIX_API_KEY"] == admin_secret
    assert fallback["PHOENIX_ADMIN_SECRET"] == ""

    override = _compose_api_environment(
        {"PHOENIX_ADMIN_SECRET": admin_secret, "PHOENIX_API_KEY": "dummy-explicit-api-key"}
    )
    assert override["PHOENIX_API_KEY"] == "dummy-explicit-api-key"
