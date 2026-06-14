#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "ollama>=0.4.0",
#   "rich>=13.0",
# ]
# ///
"""
eval_finagent.py — Human-judged, task-based eval runner for FinAgent candidates.

Each task from eval_tasks.py is driven as a multi-turn conversation:
  - You play the user.
  - Model responses + tool calls are printed live.
  - Tool calls get auto-mocked (you can override each).
  - At any prompt: /pass /partial /fail /skip /note <text> /quit
  - Soft cap at 6 user turns — a reminder fires; you can keep going.

Usage:
    uv run eval_finagent.py                                # all models, all tasks
    uv run eval_finagent.py --models granite4.1:3b
    uv run eval_finagent.py --buckets critical             # one bucket
    uv run eval_finagent.py --buckets critical important
    uv run eval_finagent.py --tags hinglish                # filter by tag
    uv run eval_finagent.py --tasks T01 T05                # specific tasks
    uv run eval_finagent.py --resume                       # skip models with existing results
    uv run eval_finagent.py --no-bench                     # skip preflight (e.g. re-runs)

Outputs (written to ./eval_results/):
    results_<model>_<ts>.json     full transcripts + scores
    scorecard_<model>_<ts>.md     Obsidian-ready scorecard
    eval_summary_<ts>.md          cross-model comparison
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import os

import ollama
from urllib.parse import urlsplit


def _client_host() -> str:
    """Resolve a *connectable* Ollama host for the client.

    Two Windows gotchas are handled here:
      - `OLLAMA_HOST` is commonly set to `0.0.0.0` (or `::`) to make the
        *server* bind all interfaces. That is a bind-all address, not a
        connectable client address — connecting to it fails. Rewrite it to
        loopback.
      - The bare `ollama` client resolves `localhost` to IPv6 (`::1`) while
        the server listens on IPv4, causing spurious "Failed to connect"
        errors. Pin to 127.0.0.1.
    Override with a fully-specified OLLAMA_HOST (e.g. http://host:port) if needed.
    """
    raw = (os.environ.get("OLLAMA_HOST") or "").strip()
    if not raw:
        return "http://127.0.0.1:11434"
    host = raw if "://" in raw else f"http://{raw}"
    parts = urlsplit(host)
    hostname = (parts.hostname or "127.0.0.1").strip("[]")
    if hostname in ("0.0.0.0", "::", "localhost"):
        hostname = "127.0.0.1"
    scheme = parts.scheme or "http"
    port = parts.port or 11434
    return f"{scheme}://{hostname}:{port}"


_OLLAMA_HOST = _client_host()
ollama = ollama.Client(host=_OLLAMA_HOST)
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from eval_tasks import (
    BUCKET_WEIGHTS,
    LAST_THURSDAY,
    TASKS,
    TODAY,
    YESTERDAY,
    Task,
    filter_tasks,
)

# ─────────────────────────────────────────────────────────────────────────────
# Constants — system prompt, tool schemas, mock results
# ─────────────────────────────────────────────────────────────────────────────

RESULTS_DIR = Path(__file__).parent / "eval_results"

SYSTEM_PROMPT = f"""You are FinAgent, a private personal-finance assistant running locally on \
Joy's hardware. You are warm, terse, and competent. You speak the way a \
financially-literate friend speaks — direct, no hedging, no moralizing \
about spending, no investment advice (you are not licensed for it), no \
unsolicited tips.

Currency context:
- Default currency is INR (₹) unless told otherwise.
- "lakh" = 100,000. "crore" = 10,000,000. "k" = 1,000.
- "EMI" = equated monthly installment (loan repayment).

Date context:
- Today is {TODAY} (Saturday).
- "Last Thursday" = {LAST_THURSDAY}. "Next month" = June 2026.
- Resolve relative dates ("yesterday", "3 days back", "last month") yourself \
using today's date. Do NOT ask the user to confirm dates you can derive.

Language:
- The user speaks English, Hindi, and Hinglish freely. Respond in the language they used.
- Devanagari and Roman script are both fine.

Tool use:
- When the user describes a transaction, an account query, a calculation, \
or anything else with a defined tool, call the tool. Do not perform arithmetic \
mentally if a calc tool exists.
- If a required argument is missing, ask one short follow-up. Don't guess.
- If multiple tools are needed in sequence, call them in sequence — do not \
preview the plan in prose first.

Output:
- Keep replies under 3 sentences unless the user explicitly asks for more.
- For confirmations, echo the parsed values back. Use Indian comma grouping \
(1,50,000 not 150,000) in prose; raw integers in JSON.
- Never include "as an AI" preambles. Never apologize for limitations unrelated to the request."""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "log_expense",
            "description": "Record an outgoing transaction to the ledger.",
            "parameters": {
                "type": "object",
                "required": ["amount", "currency", "category", "date"],
                "properties": {
                    "amount":   {"type": "number",  "description": "Positive amount in major units."},
                    "currency": {"type": "string",  "enum": ["INR", "USD", "EUR", "GBP", "SGD", "AED"]},
                    "category": {"type": "string",  "enum": ["food", "transport", "groceries", "utilities", "rent", "entertainment", "health", "travel", "shopping", "subscriptions", "emi", "other"]},
                    "vendor":   {"type": "string"},
                    "date":     {"type": "string",  "description": "ISO 8601 date YYYY-MM-DD."},
                    "notes":    {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_income",
            "description": "Record an incoming transaction.",
            "parameters": {
                "type": "object",
                "required": ["amount", "currency", "source", "date"],
                "properties": {
                    "amount":   {"type": "number"},
                    "currency": {"type": "string", "enum": ["INR", "USD", "EUR", "GBP", "SGD", "AED"]},
                    "source":   {"type": "string"},
                    "date":     {"type": "string"},
                    "notes":    {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_ledger",
            "description": "Read transactions from the ledger.",
            "parameters": {
                "type": "object",
                "required": ["start_date", "end_date"],
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date":   {"type": "string"},
                    "category":   {"type": "string"},
                    "vendor":     {"type": "string"},
                    "min_amount": {"type": "number"},
                    "max_amount": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_emi",
            "description": "Compute monthly EMI for a loan.",
            "parameters": {
                "type": "object",
                "required": ["principal", "annual_rate_percent", "tenure_months"],
                "properties": {
                    "principal":           {"type": "number"},
                    "annual_rate_percent": {"type": "number"},
                    "tenure_months":       {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "split_bill",
            "description": "Compute per-person share of a bill.",
            "parameters": {
                "type": "object",
                "required": ["total", "n_people"],
                "properties": {
                    "total":               {"type": "number"},
                    "n_people":            {"type": "integer", "minimum": 1},
                    "my_share_multiplier": {"type": "number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "Return current balance for one or all accounts.",
            "parameters": {
                "type": "object",
                "properties": {"account": {"type": "string"}},
            },
        },
    },
]

INTENT_SCHEMA = {
    "type": "object",
    "required": ["intent", "entities", "confidence"],
    "properties": {
        "intent": {"type": "string", "enum": ["log_expense", "log_income", "query", "calc", "chitchat", "unknown"]},
        "entities": {
            "type": "object",
            "properties": {
                "amount":   {"type": ["number", "null"]},
                "currency": {"type": ["string", "null"]},
                "vendor":   {"type": ["string", "null"]},
                "category": {"type": ["string", "null"]},
                "date":     {"type": ["string", "null"]},
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

MOCK_TOOL_RESULTS: dict[str, str] = {
    "query_ledger": json.dumps({
        "transactions": [
            {"date": "2026-04-05", "amount": 450, "vendor": "Swiggy", "category": "food"},
            {"date": "2026-04-12", "amount": 380, "vendor": "Zomato", "category": "food"},
            {"date": "2026-04-18", "amount": 220, "vendor": "Dominos", "category": "food"},
        ],
        "total": 1050,
        "count": 3,
    }),
    "get_balance":   json.dumps({"total": 45000, "accounts": {"savings": 40000, "wallet": 5000}}),
    "split_bill":    json.dumps({"per_person": 400, "total": 1200, "n_people": 3}),
    "calculate_emi": json.dumps({"emi": 10348.50, "total_payment": 62091, "total_interest": 2091}),
    "log_expense":   json.dumps({"id": "txn_001", "status": "logged"}),
    "log_income":    json.dumps({"id": "txn_002", "status": "logged"}),
}

CANDIDATE_MODELS = [
    "granite4.1:3b",
    "cogito:3b",
    "gemma4:e2b",
    "gemma4:e4b",
    "qwen3.5:4b",
    "nemotron-3-nano:4b",
    "phi4-mini:3.8b",
    "phi4-mini-reasoning:3.8b",
    "gemma4:26b",
]

# Needles planted in the synthetic ledger for T26 (long-context retrieval).
NEEDLES = [
    {"date": "2026-03-14", "amount": 2847, "currency": "INR", "vendor": "Apollo Pharmacy",
     "category": "health", "notes": "annual health checkup"},
    {"date": "2026-04-22", "amount": 4210, "currency": "INR", "vendor": "Tata Power",
     "category": "utilities", "notes": "bill spike from AC usage"},
    {"date": "2026-02-09", "amount": 1450, "currency": "INR", "vendor": "Uber",
     "category": "transport", "notes": "airport drop, Mumbai trip"},
]

SOFT_TURN_CAP = 6

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PreflightResult:
    avg_tok_s: float = 0.0
    avg_ttft_ms: float = 0.0
    runs: list[dict] = field(default_factory=list)
    gate: str = "UNKNOWN"   # "PASS" | "FAIL" | "SKIPPED"
    notes: str = ""


@dataclass
class TaskResult:
    task_id: str
    bucket: str
    score: str                              # "PASS" | "PARTIAL" | "FAIL" | "SKIP"
    notes: str = ""
    turn_count: int = 0
    duration_s: float = 0.0
    transcript: list[dict] = field(default_factory=list)


@dataclass
class ModelResult:
    model: str
    timestamp: str
    preflight: PreflightResult = field(default_factory=PreflightResult)
    tasks: list[TaskResult] = field(default_factory=list)
    eliminated: bool = False
    elimination_reason: str = ""

    def weighted_score(self) -> tuple[float, float]:
        """Return (achieved, max_possible) raw weighted scores, before speed mult."""
        achieved = 0.0
        possible = 0.0
        for tr in self.tasks:
            w = BUCKET_WEIGHTS.get(tr.bucket, 0)
            if tr.score == "SKIP":
                continue
            possible += w
            if tr.score == "PASS":
                achieved += w
            elif tr.score == "PARTIAL":
                achieved += w * 0.5
        return achieved, possible

    def speed_mult(self) -> float:
        s = self.preflight.avg_tok_s
        if s >= 15:
            return 1.0
        if s >= 8:
            return 0.85
        return 0.0

    def final_score(self) -> float:
        achieved, _ = self.weighted_score()
        return round(achieved * self.speed_mult(), 1)

    def bucket_stats(self) -> dict[str, dict]:
        stats: dict[str, dict] = {b: {"pass": 0, "partial": 0, "fail": 0, "skip": 0, "total": 0}
                                  for b in BUCKET_WEIGHTS}
        for tr in self.tasks:
            b = stats.setdefault(tr.bucket, {"pass": 0, "partial": 0, "fail": 0, "skip": 0, "total": 0})
            b["total"] += 1
            b[tr.score.lower()] = b.get(tr.score.lower(), 0) + 1
        return stats


# ─────────────────────────────────────────────────────────────────────────────
# Ollama helpers
# ─────────────────────────────────────────────────────────────────────────────

BENCH_PROMPT = (
    "You are a financial assistant. List the 6 most common personal expense categories "
    "and give one concrete example for each. Use Indian Rupees and Indian contexts."
)


def _model_name(m) -> str:
    # Handle ollama client version differences (attr `model` or `name`, or dict).
    for attr in ("model", "name"):
        val = getattr(m, attr, None)
        if val:
            return val
    if isinstance(m, dict):
        return m.get("model") or m.get("name") or ""
    return str(m)


def check_model_available(model: str, console: Console | None = None) -> bool:
    try:
        resp = ollama.list()
        raw = getattr(resp, "models", None)
        if raw is None and isinstance(resp, dict):
            raw = resp.get("models", [])
        names = [_model_name(m) for m in (raw or [])]
        names = [n for n in names if n]

        line = f"Available models ({len(names)}):"
        if console:
            console.print(f"[bold]{line}[/]")
            for n in sorted(names):
                console.print(f"  - {n}")
        else:
            print(line)
            for n in sorted(names):
                print(f"  - {n}")

        return any(model in n for n in names)
    except Exception as e:
        msg = f"check_model_available error: {e}"
        if console:
            console.print(f"[red]{msg}[/]")
        else:
            print(msg)
        return False


def run_bench(model: str, n_runs: int = 3, console: Console | None = None) -> PreflightResult:
    results = []
    for i in range(n_runs):
        try:
            start = time.time()
            resp = ollama.generate(model=model, prompt=BENCH_PROMPT)
            elapsed = time.time() - start
            tok_s = resp.eval_count / max(resp.eval_duration / 1e9, 0.001)
            ttft_ms = resp.prompt_eval_duration / 1e6
            results.append({"tok_s": round(tok_s, 1), "ttft_ms": round(ttft_ms, 1),
                            "elapsed_s": round(elapsed, 1)})
            if console:
                console.print(f"  Run {i+1}/{n_runs}: {tok_s:.1f} tok/s, TTFT {ttft_ms:.0f} ms")
        except Exception as e:
            if console:
                console.print(f"  [red]Run {i+1} failed: {e}[/]")
            results.append({"tok_s": 0, "ttft_ms": 9999, "elapsed_s": 0})

    valid = [r for r in results if r["tok_s"] > 0]
    if not valid:
        return PreflightResult(gate="FAIL", runs=results, notes="All bench runs failed.")

    avg_tok_s = statistics.mean(r["tok_s"] for r in valid)
    avg_ttft = statistics.mean(r["ttft_ms"] for r in valid)
    gate = "PASS" if avg_tok_s >= 8 else "FAIL"
    notes = "" if gate == "PASS" else f"Below 8 tok/s gate ({avg_tok_s:.1f} tok/s)."
    return PreflightResult(avg_tok_s=round(avg_tok_s, 1), avg_ttft_ms=round(avg_ttft, 1),
                           runs=results, gate=gate, notes=notes)


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic ledger for long-context tasks
# ─────────────────────────────────────────────────────────────────────────────

def generate_synthetic_ledger(needles: list[dict], n_transactions: int = 2000) -> str:
    VENDORS = ["Swiggy", "Zomato", "BigBasket", "DMart", "IRCTC", "Ola", "Flipkart",
               "Amazon", "Jio Recharge", "BESCOM", "Spencer's", "Blinkit", "Rapido",
               "MakeMyTrip", "BookMyShow", "Myntra", "Nykaa", "Lenskart"]
    CATEGORIES = ["food", "groceries", "transport", "shopping", "utilities",
                  "subscriptions", "entertainment"]
    NOTES = ["", "", "", "monthly", "auto-pay", "reimbursement pending", "shared with friend", ""]

    random.seed(42)
    transactions = []
    for _ in range(n_transactions):
        yr = 2025 if random.random() < 0.4 else 2026
        mo = random.randint(1, 5) if yr == 2026 else random.randint(1, 12)
        dy = random.randint(1, 28)
        transactions.append({
            "date": f"{yr}-{mo:02d}-{dy:02d}",
            "amount": random.randint(50, 8000),
            "currency": "INR",
            "vendor": random.choice(VENDORS),
            "category": random.choice(CATEGORIES),
            "notes": random.choice(NOTES),
        })

    positions = sorted(random.sample(range(len(transactions)), len(needles)))
    for pos, needle in zip(positions, needles):
        transactions.insert(pos, needle)

    lines = ["# Transaction ledger — Jan 2025 to May 2026"] + [json.dumps(t) for t in transactions]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Transcript serialization helpers
# ─────────────────────────────────────────────────────────────────────────────

def serialize_assistant_msg(msg) -> dict:
    """Convert an Ollama message object to a plain dict for transcript storage."""
    data: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        data["tool_calls"] = [
            {"name": tc.function.name, "arguments": tc.function.arguments}
            for tc in msg.tool_calls
        ]
    return data


# ─────────────────────────────────────────────────────────────────────────────
# REPL — drive a single task
# ─────────────────────────────────────────────────────────────────────────────

HOTKEY_HELP = "[Enter=continue | /pass /partial /fail /skip /note <text> /quit]"


def _print_task_header(console: Console, task: Task) -> None:
    bucket_color = {"critical": "red", "important": "yellow", "nice": "cyan"}.get(task.bucket, "white")
    header = (
        f"[bold]{task.id}[/]  [{bucket_color}]{task.bucket.upper()}[/]  "
        f"[dim]tags:[/] {', '.join(task.tags)}\n\n"
        f"[bold]Goal:[/] {task.goal}\n\n"
        f"[bold]Rubric:[/]\n"
    )
    for r in task.rubric:
        header += f"  • {r}\n"
    if task.red_flags:
        header += "\n[bold red]Red flags:[/]\n"
        for f_ in task.red_flags:
            header += f"  ✗ {f_}\n"
    if task.notes_for_judge:
        header += f"\n[bold dim]Judge note:[/] {task.notes_for_judge}\n"
    console.print(Panel(header.rstrip(), border_style=bucket_color, title="Task"))


def _print_model_turn(console: Console, msg) -> None:
    if msg.content:
        console.print(f"\n[bold green]Agent:[/] {msg.content}")
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args_str = json.dumps(tc.function.arguments, ensure_ascii=False)
            if len(args_str) > 200:
                args_str = args_str[:200] + "…"
            console.print(f"  [cyan]→ tool call:[/] [bold]{tc.function.name}[/]({args_str})")


def _inject_mocks(console: Console, msg, messages: list[dict],
                   transcript: list[dict]) -> None:
    """For each tool call in msg, prompt for an override and append a tool result."""
    for tc in msg.tool_calls:
        tname = tc.function.name
        default_mock = MOCK_TOOL_RESULTS.get(tname, "{}")
        console.print(f"  [dim]mock for {tname}:[/] {default_mock}")
        override = Prompt.ask("  [Enter=accept | type JSON to override]", default="")
        mock = override.strip() if override.strip() else default_mock
        messages.append({"role": "tool", "name": tname, "content": mock})
        transcript.append({"role": "tool", "name": tname, "content": mock,
                           "overridden": bool(override.strip())})


def _drive_model_turn(model: str, messages: list[dict], use_tools: bool,
                       use_format: bool, console: Console,
                       transcript: list[dict]) -> None:
    """Call the model repeatedly until it produces a text-only response."""
    while True:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if use_tools:
            kwargs["tools"] = TOOL_SCHEMAS
        if use_format:
            kwargs["format"] = INTENT_SCHEMA
        resp = ollama.chat(**kwargs)
        msg = resp.message

        _print_model_turn(console, msg)
        transcript.append(serialize_assistant_msg(msg))
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": msg.tool_calls,
        })

        if not msg.tool_calls:
            return
        _inject_mocks(console, msg, messages, transcript)


def run_task(model: str, task: Task, console: Console) -> TaskResult:
    _print_task_header(console, task)

    sys_msg = {"role": "system", "content": SYSTEM_PROMPT}
    messages: list[dict] = [sys_msg]
    transcript: list[dict] = []

    # Special handling
    use_tools = "json" not in task.tags
    use_format = "json" in task.tags

    if "long-context" in task.tags:
        console.print("[dim]  Generating ~28K-token synthetic ledger…[/]")
        ledger = generate_synthetic_ledger(NEEDLES)
        first_user_msg = f"{ledger}\n\n---\n{task.opening}"
        first_user_display = f"[ledger of ~2000 transactions] + {task.opening}"
        use_tools = False
    else:
        first_user_msg = task.opening
        first_user_display = task.opening

    scripted_queue = list(task.scripted_turns)
    notes_parts: list[str] = []
    user_turn_count = 0
    soft_cap_warned = False
    start_time = time.time()

    pending_user_msg: str | None = first_user_msg
    pending_user_display: str | None = first_user_display

    def _record_result(score: str) -> TaskResult:
        return TaskResult(
            task_id=task.id,
            bucket=task.bucket,
            score=score,
            notes=" | ".join(notes_parts).strip(),
            turn_count=user_turn_count,
            duration_s=round(time.time() - start_time, 1),
            transcript=transcript,
        )

    while True:
        # 1. Send pending user message
        if pending_user_msg is not None:
            user_turn_count += 1
            console.print(f"\n[bold yellow]You (turn {user_turn_count}):[/] {pending_user_display}")
            messages.append({"role": "user", "content": pending_user_msg})
            transcript.append({"role": "user", "content": pending_user_msg})
            pending_user_msg = None
            pending_user_display = None

        # 2. Drive model (tool calls auto-mocked)
        try:
            _drive_model_turn(model, messages, use_tools, use_format, console, transcript)
        except KeyboardInterrupt:
            console.print("\n[yellow]Ctrl-C — marking SKIP.[/]")
            return _record_result("SKIP")
        except Exception as e:
            console.print(f"\n[red]API error: {e}[/]")
            notes_parts.append(f"API error: {e}")
            return _record_result("FAIL")

        # 3. Soft cap reminder
        if user_turn_count >= SOFT_TURN_CAP and not soft_cap_warned:
            console.print(f"\n[yellow]⚠ Soft cap ({SOFT_TURN_CAP} turns) reached — "
                          f"consider scoring soon (can still continue).[/]")
            soft_cap_warned = True

        # 4. Get next user input (scripted or freeform) + hotkeys
        if scripted_queue:
            console.print(f"\n[dim cyan]Suggested next:[/] {scripted_queue[0]}")
            label = "[Enter=send suggested | text overrides | /pass /partial /fail /skip /note /quit]"
        else:
            label = f"\n[Your turn | /pass /partial /fail /skip /note /quit]"

        choice = Prompt.ask(label, default="").strip()

        # Parse hotkeys
        if choice.startswith("/"):
            parts = choice.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/pass":
                return _record_result("PASS")
            elif cmd == "/partial":
                if arg:
                    notes_parts.append(arg)
                else:
                    follow = Prompt.ask("  Brief note (optional)", default="").strip()
                    if follow:
                        notes_parts.append(follow)
                return _record_result("PARTIAL")
            elif cmd == "/fail":
                if arg:
                    notes_parts.append(arg)
                else:
                    follow = Prompt.ask("  Brief note (optional)", default="").strip()
                    if follow:
                        notes_parts.append(follow)
                return _record_result("FAIL")
            elif cmd == "/skip":
                return _record_result("SKIP")
            elif cmd == "/quit":
                return _record_result("SKIP")
            elif cmd == "/note":
                if arg:
                    notes_parts.append(arg)
                    console.print(f"  [green]Note added.[/]")
                else:
                    note = Prompt.ask("  Note", default="").strip()
                    if note:
                        notes_parts.append(note)
                continue
            else:
                console.print(f"  [red]Unknown hotkey: {cmd}.[/] Try /pass /partial /fail /skip /note /quit")
                continue

        # Non-hotkey: scripted (if empty) or freeform
        if not choice:
            if scripted_queue:
                pending_user_msg = scripted_queue.pop(0)
                pending_user_display = pending_user_msg
            else:
                console.print("  [dim]Empty input — type something or /skip.[/]")
                continue
        else:
            if scripted_queue:
                scripted_queue.pop(0)  # user overrode the suggestion
            pending_user_msg = choice
            pending_user_display = choice


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

SCORE_COLORS = {"PASS": "green", "PARTIAL": "yellow", "FAIL": "red", "SKIP": "dim"}
BUCKET_COLORS = {"critical": "red", "important": "yellow", "nice": "cyan"}


def print_model_summary(console: Console, result: ModelResult) -> None:
    table = Table(title=f"Scorecard — {result.model}", box=box.ROUNDED, show_lines=False)
    table.add_column("Bucket", style="cyan")
    table.add_column("Pass", justify="right")
    table.add_column("Partial", justify="right")
    table.add_column("Fail", justify="right")
    table.add_column("Skip", justify="right")
    table.add_column("Total", justify="right")
    for bucket, stats in result.bucket_stats().items():
        if stats["total"] == 0:
            continue
        table.add_row(
            f"[{BUCKET_COLORS.get(bucket, 'white')}]{bucket}[/]",
            str(stats.get("pass", 0)),
            str(stats.get("partial", 0)),
            str(stats.get("fail", 0)),
            str(stats.get("skip", 0)),
            str(stats["total"]),
        )
    console.print(table)

    pf = result.preflight
    pf_color = "green" if pf.gate == "PASS" else ("dim" if pf.gate == "SKIPPED" else "red")
    console.print(
        f"Preflight: [{pf_color}]{pf.gate}[/] | "
        f"{pf.avg_tok_s:.1f} tok/s | TTFT {pf.avg_ttft_ms:.0f} ms"
    )

    achieved, possible = result.weighted_score()
    console.print(
        f"Weighted: [bold]{achieved:.1f}[/] / {possible:.1f}  "
        f"(speed × {result.speed_mult():.2f} → final [bold]{result.final_score():.1f}[/])"
    )
    if result.eliminated:
        console.print(f"[red]ELIMINATED:[/] {result.elimination_reason}")


def write_markdown_scorecard(result: ModelResult, path: Path) -> None:
    lines = [f"# Scorecard — {result.model}", "", f"**Date:** {result.timestamp}", ""]

    pf = result.preflight
    lines += [
        "## Preflight",
        "| Metric | Value |",
        "|---|---|",
        f"| tok/s (avg) | {pf.avg_tok_s:.1f} |",
        f"| TTFT (avg) | {pf.avg_ttft_ms:.0f} ms |",
        f"| Gate | {pf.gate} |",
        f"| Notes | {pf.notes} |",
        "",
    ]

    achieved, possible = result.weighted_score()
    lines += [
        "## Summary",
        f"- Weighted (raw): **{achieved:.1f} / {possible:.1f}**",
        f"- Speed multiplier: ×{result.speed_mult():.2f}",
        f"- **Final score: {result.final_score():.1f}**",
        "",
    ]

    lines += [
        "## Bucket breakdown",
        "| Bucket | Pass | Partial | Fail | Skip | Total |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for bucket, stats in result.bucket_stats().items():
        if stats["total"] == 0:
            continue
        lines.append(
            f"| {bucket} | {stats.get('pass', 0)} | {stats.get('partial', 0)} | "
            f"{stats.get('fail', 0)} | {stats.get('skip', 0)} | {stats['total']} |"
        )
    lines.append("")

    lines += [
        "## Task results",
        "| Task | Bucket | Score | Turns | Duration | Notes |",
        "|---|---|---|---:|---:|---|",
    ]
    for tr in result.tasks:
        lines.append(
            f"| {tr.task_id} | {tr.bucket} | **{tr.score}** | {tr.turn_count} | "
            f"{tr.duration_s:.1f}s | {tr.notes or ''} |"
        )
    lines.append("")

    lines += ["## Transcripts", ""]
    for tr in result.tasks:
        lines.append(f"### {tr.task_id} — `{tr.score}`")
        if tr.notes:
            lines.append(f"_Notes: {tr.notes}_")
        lines.append("")
        lines.append("```")
        for entry in tr.transcript:
            role = entry.get("role", "?")
            if role == "user":
                lines.append(f"User: {entry.get('content', '')}")
            elif role == "assistant":
                content = entry.get("content", "")
                if content:
                    lines.append(f"Agent: {content}")
                for tc in entry.get("tool_calls") or []:
                    lines.append(f"Agent → {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")
            elif role == "tool":
                tag = " (overridden)" if entry.get("overridden") else ""
                lines.append(f"Tool ({entry.get('name', '?')}){tag}: {entry.get('content', '')}")
        lines.append("```")
        lines.append("")

    if result.eliminated:
        lines += ["## Elimination reason", result.elimination_reason, ""]

    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_markdown(results: list[ModelResult], path: Path) -> None:
    lines = [
        "# Eval Summary — All Models",
        "",
        f"**Date:** {datetime.now().isoformat()[:19]}",
        "",
        "| Model | tok/s | Weighted | Final | Eliminated |",
        "|---|---:|---|---:|---|",
    ]
    for r in sorted(results, key=lambda x: -x.final_score()):
        achieved, possible = r.weighted_score()
        lines.append(
            f"| {r.model} | {r.preflight.avg_tok_s:.1f} | "
            f"{achieved:.1f}/{possible:.1f} | {r.final_score():.1f} | "
            f"{r.elimination_reason if r.eliminated else ''} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def run_model(model: str, tasks: list[Task], do_bench: bool, console: Console) -> ModelResult:
    ts = datetime.now().isoformat()[:19].replace(":", "-")
    result = ModelResult(model=model, timestamp=ts)

    console.print(Panel(f"[bold cyan]{model}[/]", title="Evaluating", border_style="cyan"))

    # Preflight
    if do_bench:
        console.print("[bold]Pre-flight bench…[/]")
        result.preflight = run_bench(model, console=console)
        if result.preflight.gate == "FAIL":
            result.eliminated = True
            result.elimination_reason = f"Speed: {result.preflight.avg_tok_s:.1f} tok/s < 8 tok/s"
            console.print(f"[red]ELIMINATED at pre-flight: {result.elimination_reason}[/]")
            return result
    else:
        result.preflight = PreflightResult(gate="SKIPPED", notes="--no-bench")

    # Tasks
    for i, task in enumerate(tasks, 1):
        console.print(f"\n[bold]── Task {i}/{len(tasks)} ──[/]")
        try:
            tr = run_task(model, task, console)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted between tasks. Saving partial results.[/]")
            break
        result.tasks.append(tr)
        score_color = SCORE_COLORS.get(tr.score, "white")
        console.print(f"[{score_color}]→ {tr.task_id}: {tr.score}[/]"
                      f" ({tr.turn_count} turns, {tr.duration_s:.1f}s)"
                      + (f"  notes: {tr.notes}" if tr.notes else ""))

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="FinAgent task-based eval runner")
    parser.add_argument("--models", nargs="+", default=[], help="Models to test (default: all candidates)")
    parser.add_argument("--buckets", nargs="+", default=[],
                        help="Filter by bucket: critical / important / nice")
    parser.add_argument("--tags", nargs="+", default=[], help="Filter tasks by tag (any match)")
    parser.add_argument("--tasks", nargs="+", default=[], help="Specific task IDs (e.g. T01 T05)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip models that already have a results file")
    parser.add_argument("--no-bench", dest="no_bench", action="store_true",
                        help="Skip preflight bench (faster for re-runs)")
    args = parser.parse_args()

    console = Console()
    RESULTS_DIR.mkdir(exist_ok=True)

    models_to_test = args.models or CANDIDATE_MODELS
    selected_tasks = filter_tasks(
        TASKS,
        buckets=set(args.buckets) if args.buckets else None,
        tags=set(args.tags) if args.tags else None,
        ids=set(args.tasks) if args.tasks else None,
    )

    if not selected_tasks:
        console.print("[red]No tasks matched the filter. Exiting.[/]")
        sys.exit(1)

    console.print(f"[bold]Models:[/] {', '.join(models_to_test)}")
    console.print(f"[bold]Tasks ({len(selected_tasks)}):[/] {', '.join(t.id for t in selected_tasks)}")

    all_results: list[ModelResult] = []

    for model in models_to_test:
        if args.resume:
            safe_model = model.replace(":", "_").replace("/", "_")
            existing = list(RESULTS_DIR.glob(f"results_{safe_model}*.json"))
            if existing:
                console.print(f"[dim]Skipping {model} — results exist: {existing[0].name}[/]")
                continue

        if not check_model_available(model, console):
            console.print(f"[yellow]Model '{model}' not found in Ollama.[/]")
            console.print(f"  Pull it with: [bold]ollama pull {model}[/]")
            if Prompt.ask(f"Skip {model}?", choices=["y", "n"], default="y") == "y":
                continue

        try:
            result = run_model(model, selected_tasks, not args.no_bench, console)
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Saving partial results.[/]")
            result = ModelResult(model=model, timestamp=datetime.now().isoformat()[:19].replace(":", "-"),
                                 elimination_reason="Interrupted")

        all_results.append(result)

        safe_model = model.replace(":", "_").replace("/", "_")
        json_path = RESULTS_DIR / f"results_{safe_model}_{result.timestamp}.json"
        md_path = RESULTS_DIR / f"scorecard_{safe_model}_{result.timestamp}.md"

        json_path.write_text(json.dumps(asdict(result), indent=2, default=str, ensure_ascii=False),
                             encoding="utf-8")
        write_markdown_scorecard(result, md_path)
        print_model_summary(console, result)
        console.print(f"[dim]Saved: {md_path.name}[/]\n")

    # Cross-model summary
    if len(all_results) > 1:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_path = RESULTS_DIR / f"eval_summary_{ts}.md"
        write_summary_markdown(all_results, summary_path)
        console.print(f"\n[bold]Summary written:[/] {summary_path.name}")

        table = Table(title="Final comparison", box=box.SIMPLE_HEAVY)
        table.add_column("Model")
        table.add_column("tok/s", justify="right")
        table.add_column("Weighted", justify="right")
        table.add_column("Final", justify="right")
        table.add_column("Status")
        for r in sorted(all_results, key=lambda x: -x.final_score()):
            achieved, possible = r.weighted_score()
            status = f"[red]{r.elimination_reason}[/]" if r.eliminated else "[green]viable[/]"
            table.add_row(r.model, f"{r.preflight.avg_tok_s:.1f}",
                          f"{achieved:.1f}/{possible:.1f}",
                          f"{r.final_score():.1f}", status)
        console.print(table)


if __name__ == "__main__":
    main()
