"""A reusable driver for real user journeys against a real, running Fin-Agent.

The terminal `fin-agent chat` runs the agent in the caller's process, so it cannot do what a
browser does: upload a statement, answer a confirm card, review and commit an import, or show the
cards and tool calls a turn produced. This driver speaks the same HTTP the web app speaks, to a
server it starts itself on a scratch database, and keeps its session in a small state file so
separate shell invocations continue the same conversation.

    uv run python -m tests.e2e.driver up --port 8123 --seed demo
    uv run python -m tests.e2e.driver chat "How much did I spend on food last month?"
    uv run python -m tests.e2e.driver state
    uv run python -m tests.e2e.driver down

State (cookie, base URL, current thread, the last turn's confirm cards) lives under `$E2E_STATE`,
defaulting to `<tempdir>/fin-agent-e2e-<database>`. The server's log is `server.log` there.
`tests/e2e/README.md` documents every command and the journeys this was proven on.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from fin_agent.auth.sessions import COOKIE_NAME
from fin_agent.ledger.common import today as ledger_today
from tests.software_authenticator import SoftwareAuthenticator

SERVE_COMMAND = ["uv", "run", "fin-agent", "serve"]
STATE_FILE = "state.json"
LOG_FILE = "server.log"
DEFAULT_TIMEOUT = 300.0
JSON_LIMIT = 400


# --- small helpers ----------------------------------------------------------------------------


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set; point it at a scratch database ending in _test")
    return dsn


def _db_name(dsn: str | None = None) -> str:
    from fin_agent.db.conn import conninfo_to_dict

    return str(conninfo_to_dict(dsn or _dsn()).get("dbname", ""))


def state_dir() -> Path:
    """Where this agent's state file and server log live."""
    override = os.environ.get("E2E_STATE")
    if override:
        return Path(override).expanduser()
    return Path(tempfile.gettempdir()) / f"fin-agent-e2e-{_db_name()}"


def load_state() -> dict[str, Any]:
    path = state_dir() / STATE_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> Path:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILE
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _short(value: Any, limit: int = JSON_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _rupees(paise: int | None) -> str:
    if paise is None:
        return "unknown"
    sign = "-" if paise < 0 else ""
    value = abs(int(paise))
    return f"{sign}₹{value // 100:,}.{value % 100:02d}"


def _paise(text: str) -> int:
    """Rupees as the user types them, to integer paise, refusing more precision than a paise."""
    cleaned = text.replace(",", "").replace("₹", "").strip()
    try:
        value = Decimal(cleaned) * 100
    except InvalidOperation as exc:
        raise SystemExit(f"{text!r} is not an amount") from exc
    if value != value.to_integral_value():
        raise SystemExit(f"{text!r} is more precise than a paise")
    return int(value)


def _utf8_console() -> None:
    """A Windows console reports cp1252, where `₹` raises; print with replacement instead."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except OSError, ValueError:  # pragma: no cover - a closed or exotic stream
            pass


class DriverError(RuntimeError):
    """A request the server refused, with the problem document's own words."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


# --- the HTTP client --------------------------------------------------------------------------


class Driver:
    """The web app's HTTP surface, as the browser calls it.

    Pass `http` to drive an in-process app (a `TestClient`); otherwise a real `httpx.Client` is
    built for `base_url`.
    """

    def __init__(
        self, base_url: str, *, cookie: str | None = None, http: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(
            base_url=self.base_url, timeout=httpx.Timeout(30.0, read=None)
        )
        self.cookie = cookie
        self.use_cookie()

    # A hand-set header wins over httpx's cookie jar, so one code path serves both clients.
    def use_cookie(self) -> None:
        if self.cookie:
            self.http.headers["Cookie"] = f"{COOKIE_NAME}={self.cookie}"

    def close(self) -> None:
        self.http.close()

    # -- plumbing --

    def _detail(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return _short(response.text)
        if not isinstance(body, dict):
            return _short(body)
        detail = str(body.get("detail") or _short(body))
        # Problem documents carry extra keys (a 409's conflicting rows, a 422's field), and an
        # end-to-end agent needs them to say what went wrong.
        extra = {
            key: value
            for key, value in body.items()
            if key not in ("type", "title", "status", "detail")
        }
        return f"{detail} {json.dumps(extra, ensure_ascii=False, default=str)}" if extra else detail

    def _check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            raise DriverError(response.status_code, self._detail(response))
        return response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._check(self.http.get(path, **kwargs)).json()

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._check(self.http.post(path, **kwargs)).json()

    # -- auth --

    def auth_state(self) -> dict[str, Any]:
        return self.get("/api/auth/state")

    def session(self) -> dict[str, Any]:
        return self.get("/api/auth/session")

    def logged_in(self) -> bool:
        try:
            return bool(self.session().get("authenticated"))
        except DriverError:
            return False

    def enroll(self, token: str) -> dict[str, Any]:
        """The first-run ceremony: a real setup token and a real WebAuthn registration."""
        origin = str(self.auth_state()["rp_origin"])
        rp_id = urlsplit(origin).hostname or "localhost"
        authenticator = SoftwareAuthenticator(rp_id, origin)
        begin = self.post("/api/auth/setup/begin", json={"token": token})
        credential = authenticator.registration_credential(begin["options"])
        response = self.http.post(
            "/api/auth/setup/verify",
            json={"challenge_id": begin["challenge_id"], "credential": credential},
        )
        self._check(response)
        self.cookie = response.cookies.get(COOKIE_NAME) or self.cookie
        self.use_cookie()
        return response.json()

    # -- attachments and chat --

    def upload(self, name: str, data: bytes, content_type: str) -> dict[str, Any]:
        return self.post(
            "/api/attachments",
            files={"file": (name, data, content_type)},
            data={"purpose": "statement"},
        )

    def upload_file(self, path: Path) -> dict[str, Any]:
        import mimetypes

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.upload(path.name, path.read_bytes(), content_type)

    def chat(self, thread_id: str, text: str = "", *, files: Iterable[Path] = ()) -> dict:
        """One user turn, exactly as the composer sends it."""
        parts: list[dict[str, Any]] = []
        if text:
            parts.append({"type": "text", "text": text})
        for path in files:
            uploaded = self.upload_file(Path(path))
            parts.append(
                {
                    "type": "data-statement",
                    "data": {
                        "attachment_id": uploaded["id"],
                        "filename": uploaded["filename"],
                        "detected_kind": uploaded["detected_kind"],
                    },
                }
            )
        return self.send(thread_id, [{"id": _new_id("msg"), "role": "user", "parts": parts}])

    def approve(
        self,
        thread_id: str,
        tool_call_id: str,
        *,
        approved: bool,
        tool_name: str,
        tool_input: Any,
    ) -> dict[str, Any]:
        """Answer a confirm card the way the AI SDK does: the tool part, responded."""
        message = approval_message(
            tool_call_id, approved=approved, tool_name=tool_name, tool_input=tool_input
        )
        return self.send(thread_id, [message])

    def send(self, thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = chat_body(thread_id, messages)
        with self.http.stream("POST", "/api/chat", json=body) as response:
            if response.status_code >= 400:
                response.read()
                raise DriverError(response.status_code, self._detail(response))
            thread = response.headers.get("x-thread-id") or thread_id
            return turn_summary(thread, parse_stream(response.iter_lines()))

    # -- imports --

    def imports(self, limit: int = 20) -> dict[str, Any]:
        return self.get("/api/imports", params={"limit": limit})

    def import_detail(self, import_id: str) -> dict[str, Any]:
        return self.get(f"/api/imports/{import_id}")

    def set_opening(self, import_id: str, rupees: str) -> dict[str, Any]:
        return self.post(
            f"/api/imports/{import_id}/opening", json={"opening_paise": _paise(rupees)}
        )

    def set_password(self, import_id: str, password: str) -> dict[str, Any]:
        return self.post(f"/api/imports/{import_id}/password", json={"password": password})

    def resolve_line(self, import_id: str, line_id: str, resolution: str, **fields: Any) -> dict:
        payload = {"resolution": resolution, **{k: v for k, v in fields.items() if v is not None}}
        response = self.http.patch(f"/api/imports/{import_id}/lines/{line_id}", json=payload)
        return self._check(response).json()

    def commit(self, import_id: str) -> dict[str, Any]:
        return self.post(f"/api/imports/{import_id}/commit")

    def reject(self, import_id: str, reason: str | None = None) -> dict[str, Any]:
        return self.post(
            f"/api/imports/{import_id}/reject", params={"reason": reason} if reason else None
        )

    def undo_import(self, import_id: str) -> dict[str, Any]:
        return self.post(f"/api/imports/{import_id}/undo")

    def undo(self, undo_id: str) -> dict[str, Any]:
        return self.post(f"/api/undo/{undo_id}")

    # -- parser tasks --

    def task(self, task_id: str) -> dict[str, Any]:
        return self.get(f"/api/tasks/{task_id}")

    def answer_task(self, task_id: str, account: str) -> dict[str, Any]:
        """Answer the parser's "which account is this statement for?" question."""
        question = self.task(task_id).get("question") or {}
        if not question.get("question_id"):
            raise SystemExit(f"task {task_id} is not waiting on a question")
        return self.post(
            f"/api/tasks/{task_id}/answer",
            json={
                "question_id": question["question_id"],
                "account_id": resolve_account(self, account),
            },
        )

    def wait_task(self, task_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            payload = self.task(task_id)
            if payload.get("state") != "running":
                return payload
            if time.monotonic() > deadline:
                raise SystemExit(f"task {task_id} was still running after {timeout:.0f}s")
            time.sleep(1.0)


def chat_body(thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The body the web app's transport sends: the chat id, this turn's messages, a message id."""
    return {
        "id": thread_id,
        "trigger": "submit-message",
        "messageId": messages[-1]["id"] if messages else _new_id("msg"),
        "messages": messages,
    }


def approval_message(
    tool_call_id: str, *, approved: bool, tool_name: str, tool_input: Any
) -> dict[str, Any]:
    """The assistant message the AI SDK sends back when a confirm card is answered."""
    return {
        "id": _new_id("msg"),
        "role": "assistant",
        "parts": [
            {
                "type": f"tool-{tool_name}",
                "toolCallId": tool_call_id,
                "state": "approval-responded",
                "input": tool_input,
                "approval": {"id": tool_call_id, "approved": approved},
            }
        ],
    }


def resolve_account(driver: Driver, wanted: str) -> str:
    """An account id from an id or an exact name, as `driver answer` takes it."""
    accounts = driver.get("/api/accounts")["accounts"]
    if any(account["id"] == wanted for account in accounts):
        return wanted
    matches = [
        account
        for account in accounts
        if account["active"] and str(account["name"]).lower() == wanted.lower()
    ]
    if len(matches) == 1:
        return str(matches[0]["id"])
    choices = ", ".join(f"{account['name']} ({account['id']})" for account in accounts)
    raise SystemExit(f"no single active account matches {wanted!r}; choose from: {choices}")


# --- the stream -------------------------------------------------------------------------------


def parse_stream(lines: Iterable[str]) -> list[dict[str, Any]]:
    """The AI SDK UI message stream's data lines, as objects."""
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = line[len("data: ") :]
        if payload.strip() == "[DONE]":
            break
        events.append(json.loads(payload))
    return events


def turn_summary(thread_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything a later agent needs out of one turn's stream."""
    text: list[str] = []
    calls: dict[str, dict[str, Any]] = {}
    data: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    errors: list[str] = []
    number_flags: list[str] = []
    for event in events:
        kind = event.get("type")
        if kind == "text-delta":
            text.append(str(event.get("delta", "")))
        elif kind == "tool-input-available":
            calls[event["toolCallId"]] = {
                "tool_call_id": event["toolCallId"],
                "tool_name": event.get("toolName"),
                "args": event.get("input"),
            }
        elif kind in ("tool-output-available", "tool-output-error"):
            call = calls.setdefault(event["toolCallId"], {"tool_call_id": event["toolCallId"]})
            if kind == "tool-output-available":
                call["result"] = event.get("output")
            else:
                call["error"] = event.get("errorText")
        elif kind == "tool-approval-request":
            calls.setdefault(event["toolCallId"], {"tool_call_id": event["toolCallId"]})
            approvals.append(
                {"tool_call_id": event["toolCallId"], "approval_id": event.get("approvalId")}
            )
        elif kind == "data-number-flags":
            number_flags = list((event.get("data") or {}).get("amounts") or [])
        elif kind == "error":
            errors.append(str(event.get("errorText") or event.get("error")))
        elif isinstance(kind, str) and kind.startswith("data-"):
            data.append({"type": kind, "data": event.get("data")})
    for approval in approvals:
        call = calls.get(approval["tool_call_id"], {})
        approval["tool_name"] = call.get("tool_name")
        approval["input"] = call.get("args")
    return {
        "thread_id": thread_id,
        "text": "".join(text),
        "tool_calls": list(calls.values()),
        "data": data,
        "approvals": approvals,
        "undo_ids": [str(p["data"]["undo_id"]) for p in data if _has(p, "undo_id")],
        "import_ids": [str(p["data"]["import_id"]) for p in data if _has(p, "import_id")],
        "number_flags": number_flags,
        "errors": errors,
    }


def _has(part: dict[str, Any], key: str) -> bool:
    return isinstance(part.get("data"), dict) and bool(part["data"].get(key))


def render_turn(turn: dict[str, Any]) -> str:
    """A turn as a person reads it, with the id each next command needs."""
    lines = [f"thread: {turn['thread_id']}", "", "assistant:"]
    lines.extend(f"  {line}" for line in (turn["text"] or "(no text)").splitlines())
    if turn["tool_calls"]:
        lines += ["", "tool calls:"]
        for call in turn["tool_calls"]:
            lines.append(f"  - {call.get('tool_name')}({_short(call.get('args'))})")
            if "result" in call:
                lines.append(f"      -> {_short(call['result'])}")
            if "error" in call:
                lines.append(f"      !! {_short(call['error'])}")
    if turn["data"]:
        lines += ["", "data parts:"]
        for part in turn["data"]:
            payload = json.dumps(part["data"], ensure_ascii=False, default=str)
            lines.append(f"  - {part['type']}: {_short(payload)}")
    if turn["approvals"]:
        lines += ["", "confirm cards:"]
        for approval in turn["approvals"]:
            lines.append(f"  - {approval['tool_name']} {approval['tool_call_id']}")
            lines.append(f"      {_short(approval.get('input'))}")
    if turn["number_flags"]:
        lines += ["", f"number check, could not trace: {', '.join(turn['number_flags'])}"]
    if turn["errors"]:
        lines += ["", "errors:", *(f"  - {error}" for error in turn["errors"])]
    steps = [f"undo: driver undo {undo_id}" for undo_id in turn["undo_ids"]]
    steps += [f"approve: driver approve {a['tool_call_id']} yes" for a in turn["approvals"]]
    steps += [f"import: driver import {import_id}" for import_id in turn["import_ids"]]
    if any(call.get("tool_name") == "delegate_task" for call in turn["tool_calls"]):
        steps.append("a subagent started: driver tasks")
    if steps:
        lines += ["", "next:", *(f"  {step}" for step in steps)]
    return "\n".join(lines)


# --- the server -------------------------------------------------------------------------------


def server_env(port: int) -> dict[str, str]:
    """What `serve` needs to be a browser-reachable, job-running instance on this port."""
    return {
        "FIN_AGENT_RP_ID": "localhost",
        "FIN_AGENT_RP_ORIGIN": f"http://localhost:{port}",
        "FIN_AGENT_COOKIE_SECURE": "0",
        "FIN_AGENT_JOBS_ENABLED": "1",
        "FIN_AGENT_TRUSTED_HOSTS": "localhost,127.0.0.1,testserver",
        "OTEL_SDK_DISABLED": "true",
        "PYTHONIOENCODING": "utf-8",
        "PYDANTIC_AI_NO_BANNER": "1",
    }


def _require_free_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise SystemExit(f"port {port} is already in use: {exc}") from exc


def prepare_database(*, empty: bool, seed: bool) -> str:
    """Migrations, role passwords, and (optionally) the demo persona. Returns the database name."""
    from fin_agent.config import get_settings
    from fin_agent.db.bootstrap import set_agent_ro_password, set_backup_password
    from fin_agent.db.conn import connect
    from fin_agent.db.migrate import run_migrations
    from fin_agent.demo.persona import seed_demo

    settings = get_settings(refresh=True)
    name = _db_name(settings.database_url)
    if not name.endswith("_test"):
        raise SystemExit(f"refusing to reset {name!r}: its name must end in _test")

    async def run() -> None:
        async with connect(settings.database_url) as conn:
            if empty:
                await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
                await conn.execute("CREATE SCHEMA public")
            await run_migrations(conn)
            await set_agent_ro_password(conn, settings.agent_ro_password)
            await set_backup_password(conn, settings.backup_password)
            if seed:
                await seed_demo(conn, reset=True, statement_coverage=True)

    asyncio.run(run())
    return name


def issue_setup_token() -> str:
    """A fresh first-run enrollment token, rotating the one the server logged at startup."""
    from fin_agent.auth.setup import issue_setup_token as issue
    from fin_agent.db.conn import connect

    async def run() -> str:
        async with connect(_dsn()) as conn:
            return (await issue(conn, ttl_hours=1)).token

    return asyncio.run(run())


def reset_passkeys() -> str:
    """Reopen first-run setup the way `fin-agent passkeys reset` does, and return its token."""
    from fin_agent.cli import _passkeys_reset  # the CLI's own recovery path, not a copy
    from fin_agent.config import get_settings

    _deleted, _revoked, issued = asyncio.run(_passkeys_reset(get_settings(refresh=True)))
    if issued is None:
        raise SystemExit("could not reopen setup after resetting the passkeys")
    return issued.token


def _spawn_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def start_server(port: int, log_path: Path) -> subprocess.Popen:
    with open(log_path, "ab") as handle:  # the child keeps its own descriptor
        return subprocess.Popen(
            [*SERVE_COMMAND, "--host", "127.0.0.1", "--port", str(port)],
            env=dict(os.environ),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **_spawn_kwargs(),
        )


def stop_server(pid: int) -> bool:
    """Stop the server and everything it started. True when something was still running."""
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, text=True
        )
        return result.returncode == 0
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def wait_for_health(base_url: str, timeout: float, process: subprocess.Popen) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    failure: Any = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(
                f"the server exited with code {process.returncode}; see {state_dir() / LOG_FILE}"
            )
        try:
            response = httpx.get(f"{base_url}/api/health", timeout=2.0)
            if response.status_code == 200:
                return response.json()
            failure = f"{response.status_code}: {response.text}"
        except httpx.HTTPError as exc:
            failure = exc
        time.sleep(0.25)
    raise SystemExit(f"the server did not answer /api/health within {timeout:.0f}s: {failure}")


# --- the SQL truth ----------------------------------------------------------------------------

# A live reversal cancels its movement, and the reversal row itself is bookkeeping: both stay out
# of every effective total. The parity of the reversal chain decides, so an undo of an undo
# restores the original — the same rule as the ledger's own `_effectively_reversed_ids`.
_HIDDEN = """
WITH RECURSIVE chain AS (
    SELECT m.id AS root, m.id AS node, 0 AS depth
    FROM movements m
    WHERE m.id IN (SELECT reverses_id FROM movements WHERE reverses_id IS NOT NULL)
    UNION ALL
    SELECT c.root, child.id, c.depth + 1
    FROM chain c JOIN movements child ON child.reverses_id = c.node
), leaf AS (
    SELECT c.root, max(c.depth) AS depth FROM chain c
    WHERE NOT EXISTS (SELECT 1 FROM movements x WHERE x.reverses_id = c.node)
    GROUP BY c.root
), hidden AS (
    SELECT root AS id FROM leaf WHERE depth % 2 = 1
    UNION
    SELECT id FROM movements WHERE kind = 'reversal'
)
"""


def ledger_summary() -> dict[str, Any]:
    """Balances, spending, debts, goals, schedules, and open imports, read straight from SQL.

    Nothing here calls the app's own read paths, so an agent can compare what the bot said with
    this. Spending covers this month and last month; due dates are not expanded (that needs the
    schedule's RRULE, which SQL cannot do), so an active schedule is listed with its rule.
    """
    from fin_agent.db.conn import connect

    today = ledger_today()
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    next_month = (this_month + timedelta(days=32)).replace(day=1)

    async def run() -> dict[str, Any]:
        async with connect(_dsn()) as conn:
            accounts = await conn.fetch(
                """
                SELECT a.id, a.name, a.kind, a.liquid, a.active,
                       a.statement_balance_paise, a.statement_balance_as_of,
                       coalesce((
                           SELECT b.balance_paise FROM v_balances b
                           WHERE b.account_id = a.id ORDER BY b.as_of DESC LIMIT 1
                       ), 0)::bigint AS balance_paise
                FROM accounts a
                WHERE a.kind NOT IN ('spending', 'income', 'equity')
                ORDER BY a.kind, a.name
                """
            )
            spending = await conn.fetch(
                _HIDDEN
                + """
                SELECT to_char(m.date, 'YYYY-MM') AS month,
                       coalesce(c.name, 'Uncategorized') AS category,
                       sum(CASE WHEN t.to_account_id = sp.ledger_account_id
                                THEN t.amount ELSE -t.amount END)::bigint AS amount_paise
                FROM pgledger_transfers t
                JOIN accounts sp ON sp.kind = 'spending'
                JOIN movements m ON m.id = t.metadata ->> 'movement_id'
                LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
                LEFT JOIN categories c ON c.id = tl.category_id
                WHERE m.date >= $1 AND m.date < $2
                  AND (t.from_account_id = sp.ledger_account_id
                       OR t.to_account_id = sp.ledger_account_id)
                  AND m.id NOT IN (SELECT id FROM hidden)
                GROUP BY 1, 2
                ORDER BY 1 DESC, 3 DESC
                """,
                last_month,
                next_month,
            )
            owed = await conn.fetch(
                """
                SELECT display_name, balance_paise, direction, next_expected_date
                FROM v_owed ORDER BY abs(balance_paise) DESC, display_name
                """
            )
            goals = await conn.fetch(
                """
                SELECT g.name, g.target_paise, g.deadline, g.priority, g.status,
                       coalesce((
                           SELECT b.balance_paise FROM v_balances b
                           WHERE b.account_id = g.account_id ORDER BY b.as_of DESC LIMIT 1
                       ), 0)::bigint AS saved_paise
                FROM goals g WHERE g.status = 'active' ORDER BY g.priority, g.name
                """
            )
            schedules = await conn.fetch(
                """
                SELECT name, kind, amount_paise, rule, starts, ends
                FROM schedules WHERE active ORDER BY starts, name
                """
            )
            open_imports = await conn.fetch(
                """
                SELECT i.id, i.task_id, i.status, i.reason, i.counts, i.created_at,
                       (SELECT a.filename FROM attachments a WHERE a.import_id = i.id
                        ORDER BY a.created_at DESC LIMIT 1) AS filename
                FROM imports i WHERE i.status NOT IN ('committed', 'rejected')
                ORDER BY i.created_at DESC
                """
            )
            return {
                "as_of": today.isoformat(),
                "accounts": [dict(row) for row in accounts],
                "spending": [dict(row) for row in spending],
                "owed": [dict(row) for row in owed],
                "goals": [dict(row) for row in goals],
                "schedules": [dict(row) for row in schedules],
                "open_imports": [dict(row) for row in open_imports],
            }

    return asyncio.run(run())


def import_rows(limit: int = 20) -> list[dict[str, Any]]:
    """Recent imports with the parser task each one runs under."""
    from fin_agent.db.conn import connect

    async def run() -> list[dict[str, Any]]:
        async with connect(_dsn()) as conn:
            rows = await conn.fetch(
                """
                SELECT i.id, i.task_id, i.status, i.reason, i.counts, i.created_at,
                       (SELECT a.filename FROM attachments a WHERE a.import_id = i.id
                        ORDER BY a.created_at DESC LIMIT 1) AS filename
                FROM imports i ORDER BY i.created_at DESC, i.id LIMIT $1
                """,
                limit,
            )
            return [dict(row) for row in rows]

    return asyncio.run(run())


def _render_state(summary: dict[str, Any]) -> str:
    lines = [f"as of {summary['as_of']}", "", "accounts:"]
    for account in summary["accounts"]:
        tags = ["" if account["active"] else "archived", "liquid" if account["liquid"] else ""]
        label = ", ".join(tag for tag in tags if tag)
        statement = (
            f"  statement {_rupees(account['statement_balance_paise'])}"
            f" as of {account['statement_balance_as_of']}"
            if account["kind"] == "card"
            else ""
        )
        lines.append(
            f"  {account['name']} ({account['kind']}{', ' + label if label else ''})"
            f"  {_rupees(account['balance_paise'])}{statement}"
        )
    lines += ["", "spending by category:"]
    lines += [
        f"  {row['month']}  {row['category']}: {_rupees(row['amount_paise'])}"
        for row in summary["spending"]
    ] or ["  (none)"]
    lines += ["", "owed by person:"]
    lines += [
        f"  {row['display_name']}: {_rupees(row['balance_paise'])} ({row['direction']})"
        for row in summary["owed"]
    ] or ["  (nobody)"]
    lines += ["", "goals:"]
    lines += [
        f"  {row['name']}: {_rupees(row['saved_paise'])} of {_rupees(row['target_paise'])}"
        f" by {row['deadline']} (priority {row['priority']})"
        for row in summary["goals"]
    ] or ["  (none)"]
    lines += ["", "schedules (active; due dates need the rule expanded):"]
    lines += [
        f"  {row['name']} ({row['kind']}): {_rupees(row['amount_paise'])}"
        f" rule={row['rule']} from {row['starts']}" + (f" to {row['ends']}" if row["ends"] else "")
        for row in summary["schedules"]
    ] or ["  (none)"]
    lines += ["", "open imports:"]
    lines += [
        f"  {row['id']} {row['status']} {row['filename'] or '(no file)'}"
        + (f" — {row['reason']}" if row["reason"] else "")
        for row in summary["open_imports"]
    ] or ["  (none)"]
    return "\n".join(lines)


def _render_import(payload: dict[str, Any]) -> str:
    """One import's review state, and the exact command that clears each blocker."""
    import_id = payload["import_id"]
    account = payload.get("account") or {}
    period = payload.get("period") or {}
    check = payload.get("check_detail") or {}
    lines = [
        f"import: {import_id}",
        f"status: {payload['status']}",
        f"file: {(payload.get('attachment') or {}).get('filename')}",
        f"account: {account.get('name') or '(not chosen yet)'}",
        f"period: {period.get('start')} to {period.get('end')}",
        f"check: {payload.get('check') or 'not checked'}"
        + (f" ({check.get('reason')})" if check.get("reason") else ""),
        f"opening: {_rupees(payload.get('opening_paise'))}"
        f" ({payload.get('opening_source') or 'unstated'}"
        f"{', evidenced' if payload.get('opening_evidenced') else ''})",
        f"closing: {_rupees(payload.get('closing_paise'))}",
        f"counts: {json.dumps(payload.get('counts') or {}, default=str)}",
    ]
    if payload.get("reason"):
        lines.append(f"reason: {payload['reason']}")
    if payload.get("categorize"):
        lines.append(f"categorize: {json.dumps(payload['categorize'], default=str)}")
    lines.append("")
    states: dict[str, int] = {}
    for line in payload.get("lines") or []:
        states[str(line["match_state"])] = states.get(str(line["match_state"]), 0) + 1
    lines.append(f"lines: {len(payload.get('lines') or [])} {json.dumps(states)}")
    for line in payload.get("lines") or []:
        lines.append(
            f"  {line['date']}  {_short(line['description'], 50):<50}"
            f" debit {_rupees(line['debit_paise'])}"
            f" credit {_rupees(line['credit_paise'])}  {line['match_state']}"
            + (f" movement={line['movement_id']}" if line["movement_id"] else "")
        )
    blockers: list[str] = []
    if payload["status"] == "needs_password":
        blockers.append(f"needs its password: driver password {import_id} <password>")
    if payload.get("opening_required"):
        blockers.append(f"needs the opening balance: driver opening {import_id} <rupees>")
    for line in payload.get("lines") or []:
        if line.get("credit_decision_required"):
            blockers.append(
                f"line {line['id']} needs a credit decision: driver resolve {import_id}"
                f" {line['id']} classify_credit --credit-kind refund|transfer|income"
            )
        if line.get("suggested_transfer_account_id") and not line.get("transfer_confirmed"):
            blockers.append(
                f"line {line['id']} may be a transfer:"
                f" driver confirm-transfer {import_id} {line['id']}"
            )
        if line["match_state"] == "ambiguous":
            blockers.append(
                f"line {line['id']} may already be recorded: driver resolve {import_id}"
                f" {line['id']} merge --movement-id <id>"
            )
    if payload["status"] in ("needs_input", "failed") and not blockers:
        blockers.append(f"status {payload['status']}: see the reason above")
    if not blockers and payload["status"] == "review":
        blockers.append(f"ready: driver commit {import_id}")
    lines += ["", "next:" if blockers else "nothing to do"]
    lines += [f"  {blocker}" for blocker in blockers]
    return "\n".join(lines)


# --- commands ---------------------------------------------------------------------------------


def cmd_up(args: argparse.Namespace) -> int:
    os.environ.update(server_env(args.port))
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _require_free_port(args.port)
    base_url = f"http://localhost:{args.port}"
    name = prepare_database(empty=args.empty, seed=args.seed == "demo")

    log_path = directory / LOG_FILE
    log_path.write_text("", encoding="utf-8")
    process = start_server(args.port, log_path)
    health = wait_for_health(base_url, args.timeout, process)

    state = load_state()
    driver = Driver(base_url, cookie=state.get("cookie"))
    reused = False
    try:
        if state.get("cookie") and driver.logged_in():
            reused = True
            user = driver.session().get("user") or {}
        else:
            token = (
                issue_setup_token() if driver.auth_state()["setup_required"] else reset_passkeys()
            )
            user = driver.enroll(token).get("user") or {}
        cookie = driver.cookie
    except BaseException:
        stop_server(process.pid)
        raise
    finally:
        driver.close()

    save_state(
        {
            **state,
            "database": name,
            "base_url": base_url,
            "port": args.port,
            "pid": process.pid,
            "log": str(log_path),
            "cookie": cookie,
        }
    )
    print(f"up: {base_url}  db={name}  pid={process.pid}  schema={health['schema_version']}")
    print(f"log: {log_path}")
    print(f"user: {user.get('display_name')}")
    print(
        "session: reused from the state file"
        if reused
        else "enrolled: a software passkey registered through the real setup ceremony"
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    state = load_state()
    pid = state.get("pid")
    if not pid:
        print("down: no server recorded in the state file")
        return 1
    stopped = stop_server(int(pid))
    save_state({key: value for key, value in state.items() if key != "pid"})
    print(f"down: {'stopped' if stopped else 'was not running'} pid {pid}")
    return 0


def _driver(args: argparse.Namespace) -> Driver:
    state = load_state()
    if not state.get("base_url"):
        raise SystemExit("no server in the state file; run `driver up --port N` first")
    if not state.get("cookie"):
        raise SystemExit("no session in the state file; run `driver up` again to enroll")
    return Driver(state["base_url"], cookie=state["cookie"])


def _emit(args: argparse.Namespace, payload: Any, rendered: str) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str) if args.json else rendered)


def cmd_chat(args: argparse.Namespace) -> int:
    state = load_state()
    driver = _driver(args)
    thread_id = args.thread or (None if args.new else state.get("thread_id")) or _new_id("e2e")
    try:
        turn = driver.chat(thread_id, args.message or "", files=[Path(f) for f in args.file])
    finally:
        driver.close()
    # The confirm cards this turn offered, so `approve <id>` needs no second lookup.
    save_state({**state, "approvals": turn["approvals"], "thread_id": turn["thread_id"]})
    _emit(args, turn, render_turn(turn))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    state = load_state()
    thread_id = args.thread or state.get("thread_id")
    if not thread_id:
        raise SystemExit("no thread yet; run `driver chat` first")
    approvals = {item["tool_call_id"]: item for item in state.get("approvals") or []}
    approval = approvals.get(args.tool_call_id)
    if approval is None:
        known = ", ".join(approvals) or "(none)"
        raise SystemExit(f"{args.tool_call_id} is not a confirm card from the last turn; {known}")
    if not approval.get("tool_name"):
        raise SystemExit(f"the driver did not record the tool name for {args.tool_call_id}")
    driver = _driver(args)
    try:
        turn = driver.approve(
            thread_id,
            args.tool_call_id,
            approved=args.answer == "yes",
            tool_name=str(approval["tool_name"]),
            tool_input=approval.get("input"),
        )
    finally:
        driver.close()
    save_state({**state, "approvals": turn["approvals"], "thread_id": turn["thread_id"]})
    _emit(args, turn, render_turn(turn))
    return 0


def _simple(args: argparse.Namespace, call: str, *call_args: Any, **call_kwargs: Any) -> int:
    driver = _driver(args)
    try:
        payload = getattr(driver, call)(*call_args, **call_kwargs)
    finally:
        driver.close()
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_imports(args: argparse.Namespace) -> int:
    driver = _driver(args)
    try:
        payload = driver.imports(limit=args.limit)
    finally:
        driver.close()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    items = payload["items"]
    if not items:
        print("imports: none")
        return 0
    for item in items:
        print(
            f"{item['import_id']}  {item['status']:<14}"
            f" {item.get('filename') or '(no file)':<28} {item.get('created_at') or ''}"
            + (f"  — {item['reason']}" if item.get("reason") else "")
        )
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    driver = _driver(args)
    try:
        payload = driver.import_detail(args.import_id)
    finally:
        driver.close()
    _emit(args, payload, _render_import(payload))
    return 0


def _import_lines(rows: Iterable[dict[str, Any]]) -> str:
    return "\n".join(
        f"  import {row.get('import_id') or row['id']}  {row['status']:<14}"
        f" {row.get('filename') or '(no file)'}"
        + (f"  — {row['reason']}" if row.get("reason") else "")
        for row in rows
    )


def cmd_tasks(args: argparse.Namespace) -> int:
    rows = import_rows(limit=args.limit)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return 0
    if not rows:
        print("tasks: no imports, so no parser task")
        return 0
    for row in rows:
        print(
            f"{row['task_id'] or '(no task)':<34} import {row['id']}  {row['status']:<14}"
            f" {row.get('filename') or '(no file)'}"
            + (f"  — {row['reason']}" if row.get("reason") else "")
        )
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    driver = _driver(args)
    try:
        payload = driver.task(args.task_id)
    finally:
        driver.close()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"task: {payload['task_id']}  state={payload['state']}  outcome={payload['outcome']}")
    if payload.get("question"):
        print(f"question: {payload['question'].get('text')}")
        print(f"  answer with: driver answer {payload['task_id']} <account>")
    if payload.get("report"):
        print(f"report: {_short(payload['report'])}")
    if payload.get("imports"):
        print(_import_lines(payload["imports"]))
    for message in payload.get("messages") or []:
        for part in message.get("content") or []:
            if part.get("type") == "tool-call":
                print(f"  {part['toolName']}({_short(part.get('args'))})")
                if part.get("result") is not None:
                    print(f"      -> {_short(part['result'])}")
            elif part.get("type") == "text":
                print(f"  {part['text']}")
    return 0


def cmd_wait_task(args: argparse.Namespace) -> int:
    driver = _driver(args)
    try:
        payload = driver.wait_task(args.task_id, timeout=args.timeout)
    finally:
        driver.close()
    print(f"task {payload['task_id']}: {payload['state']} {payload['outcome'] or ''}".rstrip())
    if payload.get("imports"):
        print(_import_lines(payload["imports"]))
    if payload.get("report"):
        print(f"report: {_short(payload['report'])}")
    return 0


def cmd_answer(args: argparse.Namespace) -> int:
    driver = _driver(args)
    try:
        payload = driver.answer_task(args.task_id, args.account)
    finally:
        driver.close()
    print(f"answered {args.task_id}: {json.dumps(payload.get('question'), default=str)}")
    if payload.get("imports"):
        print(_import_lines(payload["imports"]))
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    summary = ledger_summary()
    _emit(args, summary, _render_state(summary))
    return 0


def cmd_invariants(args: argparse.Namespace) -> int:
    """`tests.invariants`, against the driver's own database."""
    from fin_agent.db.conn import connect
    from tests.invariants import check_ledger, checks_run

    async def run() -> list[Any]:
        async with connect(_dsn()) as conn:
            return await check_ledger(conn)

    violations = asyncio.run(run())
    for violation in violations:
        where = f" ids={','.join(violation.ids)}" if violation.ids else ""
        print(f"{violation.code}: {violation.detail}{where}")
    if violations:
        print(f"FAILED: {len(violations)} violation(s) in {checks_run()} checks")
        return 1
    print(f"OK: {checks_run()} checks")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests.e2e.driver",
        description="Drive real Fin-Agent journeys against a real server on a scratch database.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("--json", action="store_true", help="print the raw payload as JSON")
        return sub

    up = command("up", "migrate, start the server, and enroll a passkey")
    up.add_argument("--port", type=int, required=True)
    up.add_argument("--seed", choices=["demo"], help="seed the synthetic persona (resets the db)")
    up.add_argument("--empty", action="store_true", help="drop the schema first: no ledger at all")
    up.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for /api/health")

    command("down", "stop the server this driver started")

    chat = command("chat", "send a message the way the composer does, and read the turn")
    chat.add_argument("message", nargs="?", default="", help="the message text")
    chat.add_argument("--thread", help="continue this thread instead of the state file's")
    chat.add_argument("--new", action="store_true", help="start a fresh thread")
    chat.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="PATH",
        help="a statement to upload and attach (repeatable)",
    )

    approve = command("approve", "answer a confirm card")
    approve.add_argument("tool_call_id")
    approve.add_argument("answer", choices=["yes", "no"])
    approve.add_argument("--thread")

    undo = command("undo", "undo a card's write by its undo id")
    undo.add_argument("undo_id")

    imports = command("imports", "list imports")
    imports.add_argument("--limit", type=int, default=20)

    detail = command("import", "show one import's review state and blockers")
    detail.add_argument("import_id")

    opening = command("opening", "state the opening balance the import card asks for")
    opening.add_argument("import_id")
    opening.add_argument("rupees", help="as printed: a card's amount owed, a balance otherwise")

    password = command("password", "give a locked statement its password, never through chat")
    password.add_argument("import_id")
    password.add_argument("password")

    transfer = command("confirm-transfer", "confirm that a line is a transfer between my accounts")
    transfer.add_argument("import_id")
    transfer.add_argument("line_id")

    resolve = command("resolve", "resolve one review line another way")
    resolve.add_argument("import_id")
    resolve.add_argument("line_id")
    resolve.add_argument(
        "resolution",
        choices=[
            "new",
            "ignore",
            "merge",
            "confirm_transfer",
            "reject_transfer",
            "classify_credit",
        ],
    )
    resolve.add_argument("--movement-id")
    resolve.add_argument("--category-id")
    resolve.add_argument("--source-account-id")
    resolve.add_argument("--credit-kind", choices=["refund", "transfer", "income"])

    for name, help_text in (
        ("commit", "commit an import that is ready"),
        ("reject", "reject an import"),
        ("undo-import", "undo a committed import"),
    ):
        command(name, help_text).add_argument("import_id")

    tasks = command("tasks", "list imports with their parser task ids")
    tasks.add_argument("--limit", type=int, default=20)

    task = command("task", "show one parser task: state, report, transcript, imports")
    task.add_argument("task_id")

    wait = command("wait-task", "poll a parser task until it finishes")
    wait.add_argument("task_id")
    wait.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    answer = command("answer", "answer the parser's question with an account")
    answer.add_argument("task_id")
    answer.add_argument("account", help="an account id or its exact name")

    command("state", "print the ledger summary straight from SQL")
    command("invariants", "run tests.invariants against this database")
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    handlers = {
        "up": cmd_up,
        "down": cmd_down,
        "chat": cmd_chat,
        "approve": cmd_approve,
        "undo": lambda a: _simple(a, "undo", a.undo_id),
        "imports": cmd_imports,
        "import": cmd_import,
        "opening": lambda a: _simple(a, "set_opening", a.import_id, a.rupees),
        "password": lambda a: _simple(a, "set_password", a.import_id, a.password),
        "confirm-transfer": lambda a: _simple(
            a, "resolve_line", a.import_id, a.line_id, "confirm_transfer"
        ),
        "resolve": lambda a: _simple(
            a,
            "resolve_line",
            a.import_id,
            a.line_id,
            a.resolution,
            movement_id=a.movement_id,
            category_id=a.category_id,
            source_account_id=a.source_account_id,
            credit_kind=a.credit_kind,
        ),
        "commit": lambda a: _simple(a, "commit", a.import_id),
        "reject": lambda a: _simple(a, "reject", a.import_id),
        "undo-import": lambda a: _simple(a, "undo_import", a.import_id),
        "tasks": cmd_tasks,
        "task": cmd_task,
        "wait-task": cmd_wait_task,
        "answer": cmd_answer,
        "state": cmd_state,
        "invariants": cmd_invariants,
    }
    try:
        return int(handlers[args.command](args))
    except DriverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
