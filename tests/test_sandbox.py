from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location(
    "sandbox_server", Path(__file__).resolve().parents[1] / "sandbox" / "server.py"
)
assert _spec is not None and _spec.loader is not None
_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_server)
client = TestClient(_server.app)


def test_exec_returns_stdout_and_result_json_from_uploaded_file():
    code = (
        "import json\nt = open('in.txt').read()\nprint(t)\n"
        "json.dump({'n': len(t)}, open('result.json', 'w'))"
    )
    body = {"code": code, "files": {"in.txt": base64.b64encode(b"hello").decode()}}
    data = client.post("/exec", json=body).json()
    assert data == {"ok": True, "stdout": "hello\n", "stderr": "", "result_json": {"n": 5}}


def test_exec_times_out():
    data = client.post("/exec", json={"code": "while True: pass", "timeout_seconds": 1}).json()
    assert data["ok"] is False


def test_exec_rejects_path_traversal_filename():
    body = {"code": "pass", "files": {"../x": base64.b64encode(b"x").decode()}}
    assert client.post("/exec", json=body).status_code == 400
