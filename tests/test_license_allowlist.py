import json
import subprocess
import sys

import pytest

from scripts import license_audit


def test_license_audit_rejects_forbidden_and_unrecognized_licenses():
    packages = {
        name: {"version": "1", "license": license_name}
        for name, license_name in {
            "allowed": "MIT",
            "forbidden": "GPL-3.0",
            "unlisted": "BUSL-1.1",
            "unknown": "UNKNOWN",
        }.items()
    }
    assert [failure.split()[0] for failure in license_audit.failures(packages)] == [
        "forbidden",
        "unknown",
        "unlisted",
    ]
    assert "UNKNOWN" in license_audit._expressions("MIT OR BUSL-1.1")


def test_web_license_audit_fails_when_inventory_command_fails(monkeypatch, tmp_path, capsys):
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(license_audit, "ROOT", tmp_path)
    monkeypatch.setattr(license_audit.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        license_audit.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 17, stdout="", stderr="inventory failed"
        ),
    )
    monkeypatch.setattr(sys, "argv", ["license_audit.py", "--web-only"])

    assert license_audit.main() == 1
    assert "inventory failed" in capsys.readouterr().err


def test_license_audit_rejects_conflicting_scopes(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["license_audit.py", "--python-only", "--web-only"])

    with pytest.raises(SystemExit) as exc:
        license_audit.main()

    assert exc.value.code == 2


def test_web_only_license_audit_enforces_web_notices(monkeypatch, tmp_path, capsys):
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("Notices\n", encoding="utf-8")
    monkeypatch.setattr(license_audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        license_audit,
        "direct_dependencies",
        lambda *, python=True, web=True: (
            {"backend"} if python else set(),
            {"frontend"} if web else set(),
        ),
    )
    monkeypatch.setattr(
        license_audit, "web_packages", lambda: {"frontend": {"version": "1", "license": "MIT"}}
    )
    monkeypatch.setattr(sys, "argv", ["license_audit.py", "--web-only"])

    assert license_audit.main() == 1
    assert "THIRD_PARTY_NOTICES.md does not name: frontend" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("scope", "manifest", "dependency", "packages_fn"),
    [
        ("--python-only", "pyproject.toml", "backend", "python_packages"),
        ("--web-only", "web/package.json", "frontend", "web_packages"),
    ],
)
def test_license_audit_scopes_manifests_and_notices_to_one_ecosystem(
    monkeypatch, tmp_path, capsys, scope, manifest, dependency, packages_fn
):
    """Each scope needs only its own manifest and notice; the other may be absent."""
    path = tmp_path / manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    if manifest.endswith("pyproject.toml"):
        path.write_text(f'[project]\ndependencies = ["{dependency}>=1"]\n', encoding="utf-8")
    else:
        path.write_text(json.dumps({"dependencies": {dependency: ">=1"}}), encoding="utf-8")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text(f"Notices\n{dependency}\n", encoding="utf-8")
    monkeypatch.setattr(license_audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        license_audit, packages_fn, lambda: {dependency: {"version": "1", "license": "MIT"}}
    )
    monkeypatch.setattr(sys, "argv", ["license_audit.py", scope])

    assert license_audit.main() == 0
    assert "every direct dependency is named" in capsys.readouterr().out
