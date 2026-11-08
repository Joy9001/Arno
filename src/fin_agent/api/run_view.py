"""The parser run, step by step, for the run view.

Every fact here comes from what the host recorded: the step store's events (each `run_code` call
and the host calls inside it, with timestamps and failures) and the child's message history (the
script text, its result, and the model's reasoning). Step titles and stage names are host-made.
The model's own words, the script's first `#` line and its reasoning, are returned in separate
fields so the UI can label them as the agent's note.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ThinkingPart, ToolCallPart

from fin_agent.db.conn import Connection

# The parser's fixed order of work (see PARSE_INSTRUCTIONS). Steps join a stage by the host calls
# they make, so the spine is right even when the model never updates its own plan.
STAGES = (
    ("open", "Open the statement"),
    ("saved", "Check for a saved parser"),
    ("read", "Read the statement and write a parser"),
    ("submit", "Submit the parser and fix it until the checks pass"),
    ("moves", "Suggest moves between your own accounts"),
    ("report", "Report back"),
)
STAGE_OF = {
    "open_imports": 0,
    "wait_for_password": 0,
    "saved_parser": 1,
    "doc_words": 2,
    "doc_words_range": 2,
    "doc_cells": 2,
    "transcribe_page": 2,
    "check_statement": 3,
    "submit_parser": 3,
    "suggest_transfers": 4,
}
LABEL = {
    "open_imports": "Opened the statement files",
    "wait_for_password": "Waited for your password",
    "saved_parser": "Looked for a saved parser",
    "doc_info": "Read the file’s details",
    "doc_cells": "Read the sheet’s rows",
    "doc_words_range": "Read a range of pages",
    "ledger_opening": "Checked the ledger’s opening balance",
    "check_statement": "Previewed the checks",
    "transcribe_page": "Transcribed a scanned page",
    "submit_parser": "Submitted the parser",
    "suggest_transfers": "Suggested moves between your accounts",
}
SCRIPT_TOOL = "run_code"
_HOST_CALL = re.compile(
    r"\b(" + "|".join(sorted({*STAGE_OF, *LABEL}, key=len, reverse=True)) + r")\("
)
PURPOSE_LIMIT = 100
REASONING_LIMIT = 1500
ERROR_LIMIT = 220


def _label(name: str, count: int) -> str:
    if name == "doc_words":
        return "Read 1 page" if count == 1 else f"Read {count} pages"
    return LABEL.get(name, name.replace("_", " ").capitalize())


def _purpose(code: str) -> str | None:
    for line in code.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            return text[:PURPOSE_LIMIT] or None
        return None
    return None


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    return (lines[-1] if lines else str(text))[:ERROR_LIMIT]


def _doc_error(text: str) -> str:
    # `DocError('bad_range: at most 50 pages at once')` reads as its message.
    match = re.search(r"\('(?:\w+: )?(.+?)'\)", text)
    return (match.group(1) if match else text)[:ERROR_LIMIT]


def _script_error(content: Any) -> str | None:
    if isinstance(content, str) and content.lstrip().lower().startswith(("runtime error", "error")):
        return _last_line(content)
    text = str(content)
    status = re.search(r"['\"]status['\"]:\s*['\"](\w+)['\"]", text)
    if status and status.group(1) not in ("parser_submitted", "ok", "verified"):
        reason = re.search(r"['\"]reason['\"]:\s*['\"]([^'\"]{1,200})['\"]", text)
        return reason.group(1) if reason else f"The checks did not pass ({status.group(1)})"
    return None


def _scripts(messages: list[ModelMessage]) -> dict[str, dict[str, Any]]:
    """Each `run_code` call's script, result, and the reasoning of the response that made it."""
    scripts: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, ModelResponse):
            thinking = " ".join(
                part.content for part in message.parts if isinstance(part, ThinkingPart)
            ).strip()
            first = True
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_name == SCRIPT_TOOL:
                    try:
                        args = part.args_as_dict()
                    except TypeError, ValueError:
                        args = {}
                    scripts[part.tool_call_id] = {
                        "code": str(args.get("code") or ""),
                        "reasoning": (thinking[:REASONING_LIMIT] or None) if first else None,
                    }
                    first = False
            continue
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", None) == "tool-return" and part.tool_name == SCRIPT_TOOL:
                entry = scripts.setdefault(part.tool_call_id, {"code": "", "reasoning": None})
                entry["result"] = part.content
                entry["failed"] = getattr(part, "outcome", None) == "failed"
            elif getattr(part, "part_kind", None) == "retry-prompt" and (
                part.tool_name == SCRIPT_TOOL
            ):
                entry = scripts.setdefault(part.tool_call_id, {"code": "", "reasoning": None})
                entry["result"] = part.content
                entry["failed"] = True
    return scripts


async def _events(conn: Connection, task_id: str) -> list[dict[str, Any]]:
    exists = await conn.fetchval("SELECT to_regclass('public.step_persistence_events') IS NOT NULL")
    if not exists:
        return []
    rows = await conn.fetch(
        """
        SELECT run_id, seq, kind, tool_name, tool_call_id, timestamp, error
        FROM step_persistence_events WHERE conversation_id = $1 ORDER BY seq
        """,
        task_id,
    )
    return [dict(row) for row in rows]


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)


def build_run(
    *,
    events: list[dict[str, Any]],
    messages: list[ModelMessage],
    running: bool,
    outcome: str | None,
    limit: int,
) -> dict[str, Any]:
    """Pure: the run view from recorded events and history. Tests feed it synthetic data."""
    scripts = _scripts(messages)
    segments: list[dict[str, Any]] = []
    by_run: dict[str, dict[str, Any]] = {}
    steps: list[dict[str, Any]] = []
    step_by_call: dict[str, dict[str, Any]] = {}
    for event in events:
        run_id = event["run_id"]
        segment = by_run.get(run_id)
        if segment is None:
            segment = {
                "run_id": run_id,
                "used": 0,
                "limit": limit,
                "started_at": event["timestamp"],
                "ended_at": None,
                "stop": None,
            }
            by_run[run_id] = segment
            segments.append(segment)
        kind, name, call_id = event["kind"], event["tool_name"], event["tool_call_id"] or ""
        if kind == "tool_call_started":
            segment["used"] += 1
        if kind in ("run_failed", "run_completed"):
            segment["ended_at"] = event["timestamp"]
            if event["error"]:
                segment["stop"] = "budget" if "UsageLimitExceeded" in event["error"] else "error"
        if name == SCRIPT_TOOL:
            if kind == "tool_call_started":
                step = {
                    "id": call_id,
                    "segment": len(segments) - 1,
                    "started_at": event["timestamp"],
                    "ended_at": None,
                    "calls": {},
                    "failed_calls": [],
                }
                steps.append(step)
                step_by_call[call_id] = step
            elif call_id in step_by_call and kind in ("tool_call_completed", "tool_call_failed"):
                step_by_call[call_id]["ended_at"] = event["timestamp"]
                if kind == "tool_call_failed":
                    step_by_call[call_id]["failed_calls"].append(
                        (SCRIPT_TOOL, _doc_error(event["error"] or "failed"))
                    )
            continue
        parent = call_id.split("__", 1)[0] if "__" in call_id else None
        if parent in step_by_call and name:
            step = step_by_call[parent]
            if kind == "tool_call_started":
                step["calls"][name] = step["calls"].get(name, 0) + 1
            elif kind == "tool_call_failed":
                step["failed_calls"].append((name, _doc_error(event["error"] or "failed")))

    ended = not running
    out_steps: list[dict[str, Any]] = []
    stage = 0
    submits = 0
    for index, step in enumerate(steps):
        script = scripts.get(step["id"], {})
        names = list(step["calls"])
        if not names:
            # Cut off before its first host call: name it by the calls its script makes.
            names = list(dict.fromkeys(_HOST_CALL.findall(script.get("code", ""))))
        mapped = [STAGE_OF[name] for name in names if name in STAGE_OF]
        stage = max(mapped) if mapped else stage
        failed_names = {name for name, _ in step["failed_calls"]}
        error = next((text for _, text in step["failed_calls"]), None)
        script_error = _script_error(script.get("result")) if "result" in script else None
        if script.get("failed") and not script_error:
            script_error = _last_line(script.get("result") or "failed")
        error = error or script_error
        last = index == len(steps) - 1
        if step["ended_at"] is None and "result" not in script:
            status = "running" if running and last else "cut_off"
        elif error:
            status = "failed"
        else:
            status = "ok"
        if "submit_parser" in names:
            submits += 1
        titles = [_label(name, step["calls"].get(name, 1)) for name in names]
        if len(titles) > 1:
            titles[1] = titles[1][:1].lower() + titles[1][1:]
        title = " and ".join(titles[:2]) if titles else "Worked on what it had read"
        if "submit_parser" in names:
            title = f"Submitted the parser · attempt {submits}"
        out_steps.append(
            {
                "id": step["id"],
                "stage": STAGES[stage][0],
                "segment": step["segment"],
                "title": title,
                "status": status,
                "calls": [
                    {"name": name, "count": count, "failed": name in failed_names}
                    for name, count in step["calls"].items()
                ],
                "error": error if status in ("failed",) else None,
                "purpose": _purpose(script.get("code", "")),
                "reasoning": script.get("reasoning"),
                "code": script.get("code") or None,
                "started_at": step["started_at"],
                "seconds": _seconds(step["started_at"], step["ended_at"]),
            }
        )

    keys = [key for key, _ in STAGES]
    reached = max((keys.index(step["stage"]) for step in out_steps), default=0)
    stages = []
    for index, (key, title) in enumerate(STAGES):
        own = [step for step in out_steps if step["stage"] == key]
        if outcome == "ok" or index < reached:
            status = "done"
        elif index == reached:
            status = "running" if running else "stopped"
        else:
            status = "todo" if running else "not_reached"
        stages.append(
            {
                "key": key,
                "title": title,
                "status": status,
                "steps": [step["id"] for step in own],
                "seconds": round(sum(step["seconds"] or 0 for step in own), 1),
            }
        )
    current = segments[-1] if segments else None
    return {
        "stages": stages,
        "steps": out_steps,
        "segments": segments,
        "used": current["used"] if current else 0,
        "limit": limit,
        "stop": current["stop"] if current and ended else None,
    }


async def run_view(
    conn: Connection,
    *,
    task_id: str,
    messages: list[ModelMessage],
    running: bool,
    outcome: str | None,
    limit: int,
) -> dict[str, Any]:
    return build_run(
        events=await _events(conn, task_id),
        messages=messages,
        running=running,
        outcome=outcome,
        limit=limit,
    )
