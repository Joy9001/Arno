"""Run a parser's text in a fresh Monty sandbox, with typed host functions.

Replay is a separate session from the Code Mode run that wrote it: only that proves the
script stands alone. Host functions return JSON-native values. The clock is frozen to one
instant, random starts from a fixed seed, and sleeps are zero (with a one-second total cap),
which is the sandbox's available block for the clock and randomness.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_monty import (
    AsyncMonty,
    MontyCrashedError,
    MontyRuntimeError,
    MontySyntaxError,
    OSPolicy,
)

from arno.agent.spend import COST_STOP_ERRORS
from arno.importers.checks import check_statement as check_rows
from arno.importers.helpers import PRELUDE
from arno.importers.readers import Doc, DocError
from arno.importers.vision import transcribe_page as vision_transcribe
from arno.ledger.payees import is_placeholder_name

logger = logging.getLogger("arno.importers.monty")

LIMITS = {
    "max_memory": 256 * 1024 * 1024,
    "max_feed_duration_secs": 30,
    "max_suspensions": 5000,
    "max_total_sleep_secs": 1,
}
OS_POLICY: OSPolicy = {
    "datetime": dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC),
    "random_start": {"seed": 0},
    "sleep": "zero",
}
MAX_ERROR_CHARS = 600


@dataclass
class ScriptContext:
    period: tuple[str, str] | None = None
    ledger_opening_paise: int | None = None
    ledger_opening_as_of: str | None = None
    manual_opening_paise: int | None = None
    account_name: str | None = None
    fingerprint: str | None = None
    expected_fingerprint: str | None = None
    allow_vision: bool = False
    # Set by the pipeline so the gate can replay this bank's committed statements.
    import_id: str | None = None


@dataclass
class ScriptResult:
    output: Any = None
    error: str | None = None
    seconds: float = 0.0
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None


def _counted(name: str, fn: Any, stats: dict[str, Any]) -> Any:
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        stats["calls"][name] = stats["calls"].get(name, 0) + 1
        try:
            return await fn(*args, **kwargs)
        except DocError as exc:
            raise ValueError(str(exc)) from exc

    return wrapper


async def run_script(
    doc: Doc,
    script: str,
    context: ScriptContext | None = None,
    *,
    with_prelude: bool = True,
    stage: str = "replay",
    import_id: str | None = None,
) -> ScriptResult:
    context = context or ScriptContext()
    stats: dict[str, Any] = {"calls": {}, "bytes": 0}
    started = time.perf_counter()
    cost_stop: Exception | None = None
    # The gate passes the import it checks on the context; a replay leaves it None and accepts any.
    expected_import = import_id or context.import_id

    def accept_import(value: Any) -> None:
        """A script may name its import, but only the one this sandbox runs on."""
        if expected_import is not None and value is not None and str(value) != expected_import:
            raise ValueError(
                "this parser script runs on one import at a time; "
                f"import_id must be {expected_import} or omitted"
            )

    async def doc_info(*, import_id: Any = None) -> dict[str, Any]:
        accept_import(import_id)
        return doc.info()

    async def doc_words(page: Any = 0, *, import_id: Any = None) -> list[dict[str, Any]]:
        accept_import(import_id)
        return doc.words(page)

    async def doc_words_range(
        first: Any = 0, last: Any = 0, *, import_id: Any = None
    ) -> list[list[dict[str, Any]]]:
        accept_import(import_id)
        return doc.words_range(first, last)

    async def doc_cells(sheet: Any = 0, *, import_id: Any = None) -> list[list[Any]]:
        accept_import(import_id)
        return doc.cells(sheet)

    async def ledger_opening(
        account: str | None = None, *, import_id: Any = None
    ) -> dict[str, Any] | None:
        accept_import(import_id)
        del account
        if context.ledger_opening_paise is None:
            return None
        return {
            "paise": context.ledger_opening_paise,
            "as_of": context.ledger_opening_as_of,
        }

    async def check_statement(
        rows: Any,
        opening: Any = None,
        closing: Any = None,
        headers: Any = None,
        empty_statement: bool = False,
        *,
        import_id: Any = None,
    ) -> dict[str, Any]:
        accept_import(import_id)
        return check_rows(
            rows,
            opening,
            closing,
            period=context.period,
            ledger_opening=context.ledger_opening_paise,
            manual_opening=context.manual_opening_paise,
            headers=headers,
            expected_fingerprint=context.expected_fingerprint,
            empty_statement=empty_statement,
        )

    async def transcribe_page(page: Any = 0, *, import_id: Any = None) -> dict[str, Any]:
        nonlocal cost_stop
        accept_import(import_id)
        if not context.allow_vision:
            raise ValueError("vision is not available in this replay")
        if cost_stop is not None:
            raise cost_stop
        try:
            return await vision_transcribe(doc, int(page))
        except COST_STOP_ERRORS as exc:
            # Monty transports host exceptions as script errors. Preserve this stop outside
            # the sandbox, including when the script catches it and tries another page.
            cost_stop = exc
            raise

    functions = {
        "doc_info": _counted("doc_info", doc_info, stats),
        "doc_words": _counted("doc_words", doc_words, stats),
        "doc_words_range": _counted("doc_words_range", doc_words_range, stats),
        "doc_cells": _counted("doc_cells", doc_cells, stats),
        "ledger_opening": _counted("ledger_opening", ledger_opening, stats),
        "check_statement": _counted("check_statement", check_statement, stats),
        "transcribe_page": _counted("transcribe_page", transcribe_page, stats),
    }
    body = (PRELUDE + "\n" if with_prelude else "") + script
    try:
        async with AsyncMonty() as pool:
            async with pool.checkout(limits=LIMITS, os_policy=OS_POLICY) as session:
                output = await session.feed_run(body, external_lookup=functions)
        return ScriptResult(
            output=output,
            seconds=time.perf_counter() - started,
            stats=stats,
        )
    except MontySyntaxError as exc:
        return _failed(stage, f"SyntaxError: {str(exc)[:MAX_ERROR_CHARS]}", started, stats)
    except MontyRuntimeError as exc:
        return _failed(stage, f"RuntimeError: {str(exc)[-MAX_ERROR_CHARS:]}", started, stats)
    except MontyCrashedError as exc:
        return _failed(stage, f"Crashed: {str(exc)[:300]}", started, stats)
    except Exception as exc:  # noqa: BLE001 - a sandbox fault is a readable parse failure
        return _failed(stage, f"{type(exc).__name__}: {str(exc)[:300]}", started, stats)
    finally:
        if cost_stop is not None:
            raise cost_stop


def _failed(
    stage: str,
    error: str,
    started: float,
    stats: dict[str, Any],
) -> ScriptResult:
    """A sandbox failure carries its type, never the script text or the error's full message."""
    del stage
    return ScriptResult(
        error=error,
        seconds=time.perf_counter() - started,
        stats=stats,
    )


def normalize_accounts(output: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Turn a script's result into accounts, or a reason it is not a statement."""
    if not isinstance(output, dict):
        return [], f"script returned {type(output).__name__}, not a dict"
    if output.get("not_a_statement") or (output.get("kind") and not output.get("accounts")):
        return [], str(output.get("not_a_statement") or output.get("kind"))[:200]
    if output.get("needs_input"):
        return [], str(output["needs_input"])[:500]
    raw_accounts = output.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        return [], "no accounts"
    accounts: list[dict[str, Any]] = []
    for raw in raw_accounts:
        if not isinstance(raw, dict):
            continue
        rows: list[dict[str, Any]] = []
        for row in raw.get("rows") or []:
            if not isinstance(row, dict):
                continue
            rows.append(
                {
                    "date": str(row.get("date") or ""),
                    "description": str(row.get("description") or ""),
                    "debit": row.get("debit") if isinstance(row.get("debit"), int) else 0,
                    "credit": row.get("credit") if isinstance(row.get("credit"), int) else 0,
                    "balance": row.get("balance") if isinstance(row.get("balance"), int) else None,
                    "upi_ref": _upi_ref(row.get("upi_ref")),
                    "counterparty": _counterparty(row.get("counterparty")),
                }
            )
        accounts.append(
            {
                "name": str(raw.get("name") or "statement"),
                "institution": str(raw.get("institution") or "") or None,
                "account_number": str(raw.get("account_number") or "") or None,
                "opening": raw.get("opening") if isinstance(raw.get("opening"), int) else None,
                "closing": raw.get("closing") if isinstance(raw.get("closing"), int) else None,
                "headers": raw.get("headers") or None,
                "empty_statement": bool(raw.get("empty_statement")),
                "rows": rows,
            }
        )
    return accounts, None


def _upi_ref(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:40]


def _counterparty(value: Any) -> str | None:
    """The row's counterparty, or None when it names only a payment rail or a placeholder."""
    text = str(value or "").strip()[:120]
    if not text or is_placeholder_name(text):
        return None
    return text


__all__ = [
    "LIMITS",
    "OS_POLICY",
    "ScriptContext",
    "ScriptResult",
    "normalize_accounts",
    "run_script",
]
