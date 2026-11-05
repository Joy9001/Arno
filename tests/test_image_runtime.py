"""Optional smoke checks for locally built release images.

Set FIN_AGENT_API_TEST_IMAGE and FIN_AGENT_BACKUP_TEST_IMAGE to image tags to run them.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest


def _run_in_image(image_variable: str, script: str) -> None:
    image = os.environ.get(image_variable)
    if not image or not shutil.which("docker"):
        pytest.skip(f"Set {image_variable} and install Docker to test the image")
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_api_image_has_writable_workspace_and_restore_tools():
    _run_in_image(
        "FIN_AGENT_API_TEST_IMAGE",
        "set -eu; test -w /app; test -w /app/README.md; "
        "test -w /app/pre-migration; fin-agent --help >/dev/null; "
        "pg_dump --version; pg_restore --version; psql --version; "
        "restic version; rclone version >/dev/null",
    )


def test_backup_image_has_backup_tools():
    _run_in_image(
        "FIN_AGENT_BACKUP_TEST_IMAGE",
        "set -eu; pg_dump --version; psql --version; "
        "restic version; rclone version >/dev/null; jq --version",
    )
