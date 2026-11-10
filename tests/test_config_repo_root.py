"""An installed console command can point REPO_ROOT at the runtime workspace."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_repo_root_env_override_wins_over_the_package_location(tmp_path):
    env = {**os.environ, "ARNO_REPO_ROOT": str(tmp_path)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from arno.config import REPO_ROOT; print(REPO_ROOT)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert Path(result.stdout.strip()) == tmp_path.resolve()
