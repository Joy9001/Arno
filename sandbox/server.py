"""Code sandbox: POST /exec runs a Python script in a scratch directory.

The script reads uploaded files from its working directory and may write `result.json` there;
its parsed content comes back as `result_json` (null if absent or not valid JSON).
"""

import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MAX_OUTPUT = 100_000

app = FastAPI()


class ExecRequest(BaseModel):
    code: str
    files: dict[str, str] = {}
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class ExecResponse(BaseModel):
    ok: bool
    stdout: str
    stderr: str
    result_json: Any | None = None


@app.post("/exec")
def run(req: ExecRequest) -> ExecResponse:
    for name in req.files:
        if not name or name == "script.py" or name in {".", ".."} or "/" in name or "\\" in name:
            raise HTTPException(400, f"invalid filename: {name!r}")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            for name, b64 in req.files.items():
                (root / name).write_bytes(base64.b64decode(b64, validate=True))
        except ValueError:
            raise HTTPException(400, "files must be base64") from None
        (root / "script.py").write_text(req.code, encoding="utf-8")
        try:
            done = subprocess.run(
                [sys.executable, "-I", "script.py"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=req.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExecResponse(
                ok=False, stdout="", stderr=f"timed out after {req.timeout_seconds}s"
            )
        try:
            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
        except OSError, ValueError:
            result = None
        return ExecResponse(
            ok=done.returncode == 0,
            stdout=done.stdout[:MAX_OUTPUT],
            stderr=done.stderr[:MAX_OUTPUT],
            result_json=result,
        )
