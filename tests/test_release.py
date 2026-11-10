from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _audit_module():
    spec = importlib.util.spec_from_file_location(
        "license_audit", ROOT / "scripts" / "license_audit.py"
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(spec.loader, importlib.machinery.SourceFileLoader)
    spec.loader.exec_module(module)
    return module


def test_no_forbidden_python_license_ships():
    audit = _audit_module()

    assert audit.failures(audit.python_packages()) == []


def test_notices_name_every_direct_dependency():
    audit = _audit_module()
    python, web = audit.direct_dependencies()

    assert audit.notices_failures(python, web) == []


def test_no_forbidden_web_license_ships():
    audit = _audit_module()
    packages = audit.web_packages()
    if packages is None:
        pytest.skip("web/node_modules is absent")

    assert audit.failures(packages) == []


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
    assert f"ghcr.io/joy9001/arno-backup:${{ARNO_VERSION:-{version}}}" in compose
    assert 'max-size: "10m"' in compose
    assert "pre-migration-data:/app/pre-migration" in compose


def test_release_workflow_pushes_the_pinned_images():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "ghcr.io/joy9001/arno" in workflow
    assert "ghcr.io/joy9001/arno-backup" in workflow
    assert "docker/build-push-action" in workflow


def test_phoenix_has_its_own_role_and_database():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    provisioning = (ROOT / "deploy/phoenix-db.sh").read_text(encoding="utf-8")
    assert "CREATE ROLE phoenix LOGIN" in provisioning
    assert "CREATE DATABASE phoenix OWNER phoenix" in provisioning
    assert "REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC" in provisioning
    assert "PHOENIX_API_KEY: ${PHOENIX_API_KEY:-${PHOENIX_ADMIN_SECRET:-}}" in compose


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
