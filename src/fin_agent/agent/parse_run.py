"""The parse run: the same model, a different toolset, and no write or ledger tools.

It reads a sample, writes one Monty script, repairs it against `check_statement`, and submits
the script's text. The server reruns that text in a fresh `AsyncMonty`; rows stay on the host.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_ai_harness.skills import Skills

from fin_agent.agent.capabilities import RUN_MODEL_SETTINGS, turn_limits
from fin_agent.agent.models import primary_model
from fin_agent.agent.skills_backend import LocalFilesBackend
from fin_agent.importers.gate import run_gate
from fin_agent.importers.monty import ScriptContext
from fin_agent.importers.readers import Doc
from fin_agent.importers.vision import transcribe_page as vision_transcribe

logger = logging.getLogger("fin_agent.importers.parse_run")

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"

INSTRUCTIONS = (
    "You are a parse run for a bank or card statement. You have no ledger or write tools, and "
    "you must not try to write anything. Read the sample with the host functions, then write "
    "one Python script that returns accounts and rows. Call `submit_parser` with the script's "
    "text; never submit rows. The server reruns the text in a fresh sandbox. If a check fails, "
    "repair the script and submit it again. Return `opening` only when the statement prints it; "
    "never default a missing opening to zero or to `ledger_opening()`. Load the `parse-statement` "
    "skill before you begin."
)

PROMPT = """A new statement layout needs a parser.

Account: {account}
Detected kind: {kind}
Statement period (from outside the parser): {period}
Ledger opening: {opening}
Detected header fingerprint: {fingerprint}

File summary: {info}

Read a sample with `doc_info`, `doc_words`, `doc_words_range`, or `doc_cells`, then submit a
script. Return only the accounts and rows the statement contains. For a page with no text
layer, use `transcribe_page`.

Set `opening` only to a value the statement actually prints. If it prints none, return
`opening: None`; do not substitute zero or the ledger opening."""


@dataclass
class ParseDeps:
    doc: Doc
    context: ScriptContext
    account_name: str | None = None
    submitted: dict[str, Any] | None = None
    submissions: int = 0
    vision_calls: int = 0


@dataclass
class ParseResult:
    status: str
    script: str | None = None
    accounts: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    question: str | None = None
    reason: str | None = None
    kind: str | None = None
    submissions: int = 0
    helper_version: str | None = None

    @property
    def verified(self) -> bool:
        return self.status == "parser_submitted" and all(
            check.get("status") == "VERIFIED" for check in self.checks
        )


def build_parse_agent(
    model: Any = None,
    deps_type: type[ParseDeps] = ParseDeps,
    *,
    spend_store: Any = None,
) -> Agent[ParseDeps, str]:
    async def doc_info(ctx: RunContext[ParseDeps]) -> dict[str, Any]:
        """What the file is: kind, pages, text-layer pages, sheets, delimiter, encoding."""
        return ctx.deps.doc.info()

    async def doc_words(ctx: RunContext[ParseDeps], page: int = 0) -> list[dict[str, Any]]:
        """One page's positioned words. Use doc_words_range past a few pages."""
        return ctx.deps.doc.words(page)

    async def doc_words_range(
        ctx: RunContext[ParseDeps], first: int = 0, last: int = 0
    ) -> list[list[dict[str, Any]]]:
        """Many pages' words at once, one list of words for each page."""
        return ctx.deps.doc.words_range(first, last)

    async def doc_cells(ctx: RunContext[ParseDeps], sheet: int | str = 0) -> list[list[Any]]:
        """A sheet's rows by index or name. Text formats arrive as one-line cells."""
        return ctx.deps.doc.cells(sheet)

    async def ledger_opening(ctx: RunContext[ParseDeps], account: str | None = None) -> Any:
        """The account's ledger balance before this statement, or null on a first import."""
        del account
        if ctx.deps.context.ledger_opening_paise is None:
            return None
        return {
            "paise": ctx.deps.context.ledger_opening_paise,
            "as_of": ctx.deps.context.ledger_opening_as_of,
        }

    async def check_statement(
        ctx: RunContext[ParseDeps],
        rows: Any,
        opening: Any = None,
        closing: Any = None,
        headers: Any = None,
        empty_statement: bool = False,
    ) -> dict[str, Any]:
        """The checks on rows a script produced; the period and opening come from the host."""
        from fin_agent.importers.checks import check_statement as run_checks

        return run_checks(
            rows,
            opening,
            closing,
            period=ctx.deps.context.period,
            ledger_opening=ctx.deps.context.ledger_opening_paise,
            manual_opening=ctx.deps.context.manual_opening_paise,
            headers=headers,
            expected_fingerprint=ctx.deps.context.expected_fingerprint,
            empty_statement=empty_statement,
        )

    async def transcribe_page(ctx: RunContext[ParseDeps], page: int = 0) -> dict[str, Any]:
        """An isolated VISION call for a page with no text layer. Returns typed rows."""
        ctx.deps.vision_calls += 1
        if ctx.deps.vision_calls > 20:
            raise ValueError("too many vision calls in one parse run")
        return await vision_transcribe(ctx.deps.doc, page)

    async def submit_parser(ctx: RunContext[ParseDeps], code: str) -> dict[str, Any]:
        """Submit a parser script's text. The gate reruns it; rows stay here."""
        ctx.deps.submissions += 1
        if ctx.deps.submissions > 6:
            return {"status": "gave_up", "reason": "too many submissions"}
        result = await run_gate(
            doc=ctx.deps.doc,
            context=ctx.deps.context,
            code=code,
        )
        status = result["status"]
        if status == "not_a_statement":
            ctx.deps.submitted = {
                "status": "not_a_statement",
                "kind": result.get("kind"),
                "script": code,
            }
            return {"status": "not_a_statement", "kind": result.get("kind")}
        if status == "needs_input":
            ctx.deps.submitted = {
                "status": "needs_input",
                "question": result.get("question"),
                "script": code,
            }
            return {"status": "needs_input", "question": result.get("question")}
        if status == "parser_submitted":
            ctx.deps.submitted = {
                "status": status,
                "script": code,
                "accounts": result.get("accounts") or [],
                "checks": result.get("checks") or [],
                "helper_version": result.get("helper_version"),
            }
        return {
            "status": status,
            "counts": result.get("counts") or {},
            "sample": result.get("sample") or [],
            "checks": [
                {
                    key: check.get(key)
                    for key in ("status", "reason", "computed_closing", "chain_breaks")
                }
                for check in result.get("checks") or []
            ],
            "reason": result.get("reason"),
            "replay": result.get("replay"),
        }

    toolset = FunctionToolset(
        tools=[
            doc_info,
            doc_words,
            doc_words_range,
            doc_cells,
            ledger_opening,
            check_statement,
            transcribe_page,
            submit_parser,
        ],
        timeout=120,
    ).with_metadata(code_mode=True)

    skills = Skills(
        "skills",
        workspace=LocalFilesBackend(SKILLS_ROOT.parent),
        include=["parse-statement"],
    )
    from fin_agent.agent.capabilities import spend_limits

    limits = spend_limits(spend_store)
    capabilities: list[Any] = [CodeMode(tools={"code_mode": True}), skills]
    if limits is not None:
        capabilities.append(limits)
    return Agent(
        model or primary_model(),
        deps_type=deps_type,
        output_type=str,
        instructions=INSTRUCTIONS,
        toolsets=[toolset],
        capabilities=capabilities,
        name="parse-run",
    )


async def run_parse(
    doc: Doc,
    context: ScriptContext,
    *,
    account_name: str | None,
    model: Any = None,
    spend_store: Any = None,
) -> ParseResult:
    return await _run_parse(
        doc, context, account_name=account_name, model=model, spend_store=spend_store
    )


async def _run_parse(
    doc: Doc,
    context: ScriptContext,
    *,
    account_name: str | None,
    model: Any = None,
    spend_store: Any = None,
) -> ParseResult:
    from fin_agent.agent.spend import spend_store_scope

    async with spend_store_scope(spend_store) as store:
        return await _parse_with_agent(
            doc, context, account_name=account_name, model=model, spend_store=store
        )


async def _parse_with_agent(
    doc: Doc,
    context: ScriptContext,
    *,
    account_name: str | None,
    model: Any = None,
    spend_store: Any = None,
) -> ParseResult:
    deps = ParseDeps(doc=doc, context=context, account_name=account_name)
    agent = build_parse_agent(model, spend_store=spend_store)
    prompt = PROMPT.format(
        account=account_name or "unknown",
        kind=doc.kind,
        period=f"{context.period[0]} to {context.period[1]}" if context.period else "unknown",
        opening=context.ledger_opening_paise,
        fingerprint=context.fingerprint or "none",
        info=json.dumps(doc.info(), ensure_ascii=False)[:1500],
    )
    try:
        await agent.run(
            prompt,
            deps=deps,
            model_settings=RUN_MODEL_SETTINGS,
            usage_limits=turn_limits(),
        )
    except Exception as exc:  # noqa: BLE001 - a failed run becomes a card, not a crashed job
        from fin_agent.agent.spend import ceiling_name

        ceiling = ceiling_name(exc)
        if ceiling:
            logger.warning("parse run stopped at the %s ceiling", ceiling)
            return ParseResult(status="gave_up", reason=f"spend ceiling hit: {ceiling}")
        logger.warning("parse run failed: %s", type(exc).__name__)
        return ParseResult(status="gave_up", reason=f"parse run failed: {type(exc).__name__}")
    submitted = deps.submitted
    if submitted is None:
        return ParseResult(status="gave_up", reason="the parse run did not submit a parser")
    status = str(submitted.get("status"))
    if status == "not_a_statement":
        return ParseResult(
            status="not_a_statement",
            kind=submitted.get("kind"),
            submissions=deps.submissions,
        )
    if status == "needs_input":
        return ParseResult(
            status="needs_input",
            question=submitted.get("question"),
            submissions=deps.submissions,
        )
    return ParseResult(
        status=status,
        script=submitted.get("script"),
        accounts=submitted.get("accounts") or [],
        checks=submitted.get("checks") or [],
        reason=None if status == "parser_submitted" else "the checks did not pass",
        submissions=deps.submissions,
        helper_version=submitted.get("helper_version"),
    )


__all__ = ["ParseDeps", "ParseResult", "build_parse_agent", "run_parse"]
