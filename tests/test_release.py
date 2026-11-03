from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _audit_module():
    spec = importlib.util.spec_from_file_location(
        "license_audit", ROOT / "scripts" / "license_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "ghcr.io/joy9001/fin-agent:${FIN_AGENT_VERSION:-0.1.1}" in compose
    assert "ghcr.io/joy9001/fin-agent-backup:${FIN_AGENT_VERSION:-0.1.1}" in compose
    assert 'max-size: "10m"' in compose
    assert "pre-migration-data:/app/pre-migration" in compose


def test_release_workflow_pushes_the_pinned_images():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "ghcr.io/joy9001/fin-agent" in workflow
    assert "ghcr.io/joy9001/fin-agent-backup" in workflow
    assert "docker/build-push-action" in workflow


def test_phoenix_has_its_own_role_and_database():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "CREATE ROLE phoenix LOGIN" in compose
    assert "CREATE DATABASE phoenix OWNER phoenix" in compose
    assert "REVOKE CONNECT ON DATABASE phoenix FROM PUBLIC" in compose
    assert 'PHOENIX_ALLOW_EXTERNAL_RESOURCES: "false"' in compose
