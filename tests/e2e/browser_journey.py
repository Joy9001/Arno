"""Real-browser journeys against a real Arno, driven by Playwright.

The e2e driver proves the wire; this proves the screen. It starts the same server on a scratch
database, enrolls a passkey through the real WebAuthn UI using Chromium's virtual authenticator,
types in the composer, reads rendered cards, and walks Settings.

    uv run --with playwright==1.64.0 python -m tests.e2e.browser_journey enroll --port 8131

Playwright 1.64.0 is the release whose bundled Chromium revision, 1248, is the one in the local
browser cache. It is not a project dependency; `--with` puts it in the process that runs a
journey. `--dry-run` isolates the server's model endpoints on a closed loopback port. `enroll`
sends its onboarding message and records the expected model error before the ledger assertions.
`tests/e2e/README.md` documents each journey.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import playwright.async_api as pw  # pyrefly: ignore[missing-import]  # optional: --with playwright
from dateutil.relativedelta import relativedelta

from arno.india.recurrence import expand_rule
from tests.e2e.driver import (
    LOG_FILE,
    _require_free_port,
    _utf8_console,
    issue_setup_token,
    prepare_database,
    reset_passkeys,
    server_env,
    start_server,
    state_dir,
    stop_server,
    wait_for_health,
)

JOURNEYS = ("enroll", "home-vs-chat", "walkthrough")
DEFAULT_TIMEOUT = 300.0
SAFE_PHRASE = "safe to spend today"
STOP_BUTTONS = 'button[aria-label="Stop generating"], button[aria-label="Cancel sending"]'
CONFIRM_BUTTON = '[data-slot="approval-card-allow-once"]'
PROSE = '[data-slot="aui_assistant-message-root"] [data-slot="aui_assistant-message-text"]'
RECEIPT = "figure"
RUPEE = re.compile(r"₹[\d,]+(?:\.\d{2})?")

ONBOARDING = (
    "Here is my starting picture: I keep my money in an HDFC savings account, and it has "
    "₹85,000 in it as of today. My salary of ₹60,000 arrives on the 1st of every month. "
    "Please set that up."
)
QUESTION = "How much can I safely spend before my next salary?"
HOME_QUESTION = "How much can I safely spend right now?"


class JourneyFailure(RuntimeError):
    """A journey stopped at a step, with the reason a person needs to fix it."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _one_space(text: str) -> str:
    return " ".join(text.split())


class Report:
    """What a journey saw: passed steps, figures, screenshots, and the thread it used."""

    def __init__(
        self, journey: str, directory: Path, *, base_url: str, database: str, dry_run: bool
    ) -> None:
        self.journey = journey
        self.directory = directory
        self.base_url = base_url
        self.database = database
        self.dry_run = dry_run
        self.current = "start"
        self.steps: list[str] = []
        self.figures: dict[str, Any] = {}
        self.screenshots: list[str] = []
        self.notes: list[str] = []
        self.thread_id: str | None = None
        self.html: Path | None = None
        self.console_errors: list[str] = []

    async def shot(self, page: pw.Page, name: str) -> None:
        path = self.directory / f"{len(self.screenshots) + 1:02d}-{_slug(name)}.png"
        await page.screenshot(path=str(path), full_page=True)
        self.screenshots.append(str(path))

    def figure(self, name: str, value: Any) -> None:
        self.figures[name] = value

    def note(self, text: str) -> None:
        self.notes.append(text)

    def remember_thread(self, response: pw.Response) -> None:
        if response.request.method == "POST" and response.url.endswith("/api/chat"):
            thread = response.headers.get("x-thread-id")
            if thread:
                self.thread_id = thread

    def passes(self, step: str) -> None:
        self.steps.append(step)

    def summary(self) -> dict[str, Any]:
        return {
            "journey": self.journey,
            "database": self.database,
            "base_url": self.base_url,
            "dry_run": self.dry_run,
            "steps_passed": self.steps,
            "figures": self.figures,
            "screenshots": self.screenshots,
            "thread_id": self.thread_id,
            "html": str(self.html) if self.html else None,
            "notes": self.notes,
            "console_errors": self.console_errors,
        }


class Step:
    """A named step: screenshots on the way out, records itself only when it passes."""

    def __init__(self, report: Report, page: pw.Page, name: str) -> None:
        self.report = report
        self.page = page
        self.name = name

    async def __aenter__(self) -> Report:
        self.report.current = self.name
        return self.report

    async def __aexit__(self, kind: Any, value: Any, traceback: Any) -> bool:
        if value is None:
            await self.report.shot(self.page, self.name)
            self.report.passes(self.name)
        return False


# --- the browser ------------------------------------------------------------------------------


async def _virtual_authenticator(context: pw.BrowserContext, page: pw.Page) -> Any:
    """A CTAP2 authenticator with a resident key, so the setup button can register one."""
    cdp = await context.new_cdp_session(page)
    await cdp.send("WebAuthn.enable")
    await cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
            }
        },
    )
    return cdp


def _setup_token(base_url: str) -> str:
    """A token the browser can use: first-run when setup is open, otherwise reopen it."""
    state = httpx.get(f"{base_url}/api/auth/state", timeout=10.0).json()
    return issue_setup_token() if state.get("setup_required") else reset_passkeys()


def _cover_card_balances() -> None:
    """State each card's balance as its ledger already has it, so the demo forecast is ready.

    The demo seed states a balance for the bank but not for the Slice card, and the web waits for
    every balance before it shows the projection. This is fixture setup, not a UI step.
    """
    from arno.db.conn import connect
    from arno.ledger.accounts import list_accounts, set_statement_balance

    async def run() -> None:
        async with connect() as conn:
            for account in await list_accounts(conn):
                if account["kind"] == "card" and account["statement_balance_paise"] is None:
                    await set_statement_balance(
                        conn, account["id"], balance_paise=int(account["balance_paise"])
                    )

    asyncio.run(run())


def _seed_notification() -> None:
    """One unread inbox item, so the walkthrough opens a real one: the demo seed has none."""
    from arno.db.conn import connect
    from arno.notifications import create_notification

    key = "browser-journey:backup_stale"

    async def run() -> None:
        async with connect() as conn:
            await create_notification(
                conn,
                kind="backup_stale",
                title="Backups have stopped",
                body="No successful backup is recorded yet. The sidecar has not shipped a dump.",
                data={"seeded_by": "tests.e2e.browser_journey"},
                dedupe_key=key,
            )
            # An earlier run's row keeps the key, so it must be unread again for this walk.
            await conn.execute(
                "UPDATE notifications SET read_at = NULL, resolved_at = NULL WHERE dedupe_key = $1",
                key,
            )

    asyncio.run(run())


def _listen(page: pw.Page, report: Report) -> None:
    def console(message: Any) -> None:
        if message.type == "error":
            report.console_errors.append(_one_space(str(message.text)))

    def page_error(error: Any) -> None:
        report.console_errors.append(str(error))

    page.on("console", console)
    page.on("pageerror", page_error)
    page.on("response", report.remember_thread)


# --- reading the page -------------------------------------------------------------------------


async def _visible_alerts(page: pw.Page) -> list[str]:
    alerts: list[str] = []
    found = page.locator('[role="alert"]')
    for index in range(await found.count()):
        element = found.nth(index)
        if await element.is_visible():
            alerts.append(_one_space(await element.inner_text()))
    return alerts


async def _assert_no_alert(page: pw.Page, report: Report) -> None:
    alerts = await _visible_alerts(page)
    if alerts:
        raise JourneyFailure(report.current, "error banner: " + " | ".join(alerts))


async def _last_prose(page: pw.Page) -> str:
    prose = page.locator(PROSE)
    if not await prose.count():
        return ""
    return _one_space(await prose.last.inner_text())


async def _safe_block(scope: Any, report: Report) -> dict[str, Any]:
    """The SafeToSpend line and its notes, as strings a person reads."""
    paragraph = scope.locator("p", has_text=SAFE_PHRASE).last
    await paragraph.wait_for(timeout=60_000)
    text = _one_space(await paragraph.inner_text())
    notes = [
        _one_space(note)
        for note in await paragraph.locator("xpath=following-sibling::ul[1]/li").all_inner_texts()
    ]
    parts = [part.strip() for part in text.split("·")]
    if len(parts) != 3:
        raise JourneyFailure(
            report.current,
            f"the safe-to-spend line did not read as amount · as of · binding: {text}",
        )
    return {
        "text": text,
        "amount": parts[0].removesuffix(f" {SAFE_PHRASE}").strip(),
        "as_of": parts[1].removeprefix("as of ").strip(),
        "binding": parts[2],
        "notes": notes,
    }


async def _state_busy(page: pw.Page) -> bool:
    buttons = page.locator(STOP_BUTTONS)
    for index in range(await buttons.count()):
        if await buttons.nth(index).is_visible():
            return True
    return False


async def _settle(page: pw.Page, report: Report, timeout: float = DEFAULT_TIMEOUT) -> None:
    """Wait for a streamed turn to finish: no stop button visible, twice in a row."""
    deadline = time.monotonic() + timeout
    await page.wait_for_timeout(250)
    while time.monotonic() < deadline:
        if not await _state_busy(page):
            await page.wait_for_timeout(400)
            if not await _state_busy(page):
                return
        await page.wait_for_timeout(300)
    raise JourneyFailure(report.current, f"the stream did not finish within {timeout:.0f}s")


async def _send(page: pw.Page, report: Report, text: str) -> None:
    field = page.get_by_label("Message Arno")
    await field.fill(text)
    await page.get_by_label("Send message").click()
    await _settle(page, report)


async def _answer_confirm_cards(page: pw.Page, report: Report, limit: int = 4) -> int:
    """Click every confirm card a turn offered, through the UI, and let it run."""
    answered = 0
    for _ in range(limit):
        confirm = page.locator(CONFIRM_BUTTON)
        if not await confirm.count():
            break
        await report.shot(page, "confirm-card")
        await confirm.first.click()
        await confirm.first.wait_for(state="detached", timeout=15_000)
        answered += 1
        await _settle(page, report)
    if await page.locator(CONFIRM_BUTTON).count():
        raise JourneyFailure(report.current, "a confirm card was still waiting after four answers")
    return answered


async def _json(page: pw.Page, report: Report, url: str) -> Any:
    """A read through the app's own API, with the browser's session."""
    response = await page.request.get(url)
    if not response.ok:
        raise JourneyFailure(report.current, f"GET {url} returned {response.status}")
    return await response.json()


# --- the journeys -----------------------------------------------------------------------------


async def _onboarding_ui(page: pw.Page, report: Report) -> None:
    """The first-run home and the composer, with the model closed: the dry run's step 2."""
    async with Step(report, page, "first-run home and composer"):
        region = page.get_by_role("region", name="Get started")
        await region.wait_for(timeout=60_000)
        await page.get_by_text(re.compile(r"I don.t know your money yet")).wait_for(timeout=60_000)
        await report.shot(page, "first-run-home")

        field = page.get_by_label("Message Arno")
        await field.fill(ONBOARDING)
        await page.get_by_label("Send message").click()
        await _settle(page, report, timeout=45.0)
        sent = page.locator('[data-slot="aui_user-message-root"]').first
        await sent.wait_for(timeout=15_000)
        if "85,000" not in _one_space(await sent.inner_text()):
            raise JourneyFailure(report.current, "the composer did not send the onboarding message")
        error = page.get_by_role("alert").filter(has_text="The model failed.")
        await error.wait_for(timeout=15_000)
        report.figure("dry_run_model_error", _one_space(await error.inner_text()))
        report.note("--dry-run: stopped after the onboarding message, before the writes")


async def _onboarding_state(page: pw.Page, base_url: str, report: Report) -> None:
    """The account and salary the onboarding turn wrote, read back through the app's API."""
    accounts = await _json(page, report, f"{base_url}/api/accounts")
    bank = next(
        (row for row in accounts["accounts"] if row["kind"] == "bank" and row["active"]), None
    )
    if bank is None:
        raise JourneyFailure(report.current, "onboarding wrote no active bank account")
    report.figure(
        "onboarding_account",
        {"name": bank["name"], "statement_balance_paise": bank["statement_balance_paise"]},
    )
    if int(bank["statement_balance_paise"] or 0) != 8_500_000:
        raise JourneyFailure(
            report.current, f"the bank account has no ₹85,000 stated balance: {bank['name']}"
        )

    schedules = await _json(page, report, f"{base_url}/api/schedules")
    salary = next(
        (row for row in schedules["items"] if row["kind"] == "income_fixed" and row["active"]),
        None,
    )
    if salary is None:
        raise JourneyFailure(report.current, "onboarding wrote no active income schedule")
    report.figure(
        "onboarding_schedule",
        {"name": salary["name"], "amount_paise": salary["amount_paise"], "rule": salary["rule"]},
    )
    starts = date.fromisoformat(salary["starts"])
    first_month = starts.replace(day=1)
    if first_month < starts:
        first_month += relativedelta(months=1)
    expected = [first_month + relativedelta(months=month) for month in range(12)]
    dates = expand_rule(
        salary["rule"],
        starts,
        start=starts,
        end=first_month + relativedelta(months=12, days=-1),
        ends=date.fromisoformat(salary["ends"]) if salary["ends"] else None,
        holiday_adjust=salary["holiday_adjust"],
    )
    if salary["amount_paise"] != 6_000_000 or dates != expected:
        raise JourneyFailure(
            report.current,
            f"expected ₹60,000 on the 1st of each month: {salary['name']} {salary['rule']}",
        )


async def _enroll(page: pw.Page, base_url: str, token: str, report: Report) -> None:
    """The first-run ceremony, from the enrollment link to the home the app expects."""
    await page.goto(f"{base_url}/setup#t={quote(token)}")
    await page.get_by_role("heading", name=re.compile(r"^(Set up Arno|Add this device)$")).wait_for(
        timeout=30_000
    )
    field = page.get_by_label("Enrollment token")
    prefilled = await field.input_value()
    if len(prefilled) < 8:
        raise JourneyFailure(report.current, "the enrollment link did not fill the token")
    report.figure("token_prefilled_from_link", True)
    await report.shot(page, "setup-link")

    button = page.get_by_role("button", name=re.compile(r"^(Create passkey|Add this device)$"))
    await button.click()
    composer = page.get_by_label("Message Arno")
    try:
        await composer.wait_for(timeout=90_000)
    except pw.TimeoutError:
        alerts = await _visible_alerts(page)
        raise JourneyFailure(
            report.current,
            "the passkey ceremony did not finish; "
            + (" | ".join(alerts) if alerts else f"still at {page.url}"),
        ) from None
    if page.url.rstrip("/") != base_url.rstrip("/"):
        raise JourneyFailure(report.current, f"setup left the browser at {page.url}")
    await page.get_by_role(
        "region", name=re.compile("What I noticed|Get started|Needs you")
    ).first.wait_for(timeout=60_000)


async def _home_safe(page: pw.Page, report: Report) -> dict[str, Any]:
    region = page.get_by_role("region", name="What I noticed")
    await region.wait_for(timeout=60_000)
    return await _safe_block(region, report)


async def _open_home(page: pw.Page) -> None:
    await (
        page.locator('nav[aria-label="Primary"]')
        .first.get_by_role("button", name="Ask", exact=True)
        .click()
    )
    await page.get_by_text("Ask me anything about your money.").wait_for(timeout=30_000)


async def _first_answer(page: pw.Page, report: Report) -> None:
    """C01 box 4: onboarding words, any confirm card, then the first answer."""
    async with Step(report, page, "complete first-run onboarding in chat"):
        await _send(page, report, ONBOARDING)
        answered = await _answer_confirm_cards(page, report)
        report.figure("onboarding_confirm_cards", answered)
        report.figure("onboarding_reply", (await _last_prose(page))[:400])
        await _assert_no_alert(page, report)
        await _onboarding_state(page, report.base_url, report)

    async with Step(report, page, "ask for the safe-to-spend figure"):
        await _send(page, report, QUESTION)
        await _answer_confirm_cards(page, report)
        await _assert_no_alert(page, report)
        prose = await _last_prose(page)
        if not prose:
            raise JourneyFailure(report.current, "the assistant sent no answer text")
        figure = RUPEE.search(prose)
        if figure is None:
            raise JourneyFailure(
                report.current, f"the answer stated no rupee figure: {prose[:200]}"
            )
        report.figure("answer", prose[:800])
        report.figure("answer_rupees", figure.group(0))
        await report.shot(page, "answer")

    async with Step(report, page, "compare the chat card with the home page"):
        card = page.locator(RECEIPT, has_text=SAFE_PHRASE).last
        chat = await _safe_block(card, report) if await card.count() else None
        if chat:
            report.figure("chat_safe_to_spend", chat["text"])
        await _open_home(page)
        home_region = page.get_by_role("region", name="What I noticed")
        try:
            await home_region.wait_for(timeout=20_000)
        except pw.TimeoutError:
            report.note("The empty ledger still shows first-run, so home has no safe-to-spend line")
        else:
            home = await _safe_block(home_region, report)
            report.figure("home_safe_to_spend", home["text"])
            if chat and chat["amount"] != home["amount"]:
                raise JourneyFailure(
                    report.current,
                    f"chat says {chat['amount']} but home says {home['amount']}",
                )


async def _home_vs_chat(page: pw.Page, report: Report, *, dry_run: bool) -> None:
    """C28 box 4: the same amount, as-of date, and notes on both surfaces."""
    async with Step(report, page, "read the home safe-to-spend block"):
        home = await _home_safe(page, report)
        report.figure("home_safe_to_spend", home["text"])
        report.figure("home_data_quality_notes", home["notes"])
    if dry_run:
        report.note("--dry-run: stopped after the home block, before the chat message")
        return

    async with Step(report, page, "ask for the figure in chat"):
        await _send(page, report, HOME_QUESTION)
        await _assert_no_alert(page, report)
        prose = await _last_prose(page)
        figure = RUPEE.search(prose)
        if figure is None:
            raise JourneyFailure(report.current, "the answer stated no rupee figure")
        report.figure("answer_rupees", figure.group(0))

    async with Step(report, page, "compare the chat card with the home page"):
        card = page.locator(RECEIPT, has_text=SAFE_PHRASE).last
        await card.wait_for(timeout=DEFAULT_TIMEOUT * 1000)
        chat = await _safe_block(card, report)
        report.figure("chat_safe_to_spend", chat["text"])
        report.figure("chat_data_quality_notes", chat["notes"])
        for field in ("amount", "as_of", "notes"):
            if chat[field] != home[field]:
                raise JourneyFailure(
                    report.current,
                    f"{field} differs: chat {chat[field]!r} vs home {home[field]!r}",
                )
        await report.shot(page, "chat-card")


async def _walkthrough(page: pw.Page, report: Report) -> None:
    """Completion checklist B: Home's notifications surface, then every Settings section."""
    async with Step(report, page, "open home and what it noticed"):
        noticed = page.get_by_role("region", name="What I noticed")
        await noticed.wait_for(timeout=60_000)
        report.figure("noticed_cards", await noticed.get_by_role("article").count())
        await _assert_no_alert(page, report)

    async with Step(report, page, "open the notifications surface"):
        await (
            page.locator('nav[aria-label="Primary"]')
            .first.get_by_role("button", name=re.compile(r"^Activity"))
            .click()
        )
        await page.get_by_role("tab", name=re.compile(r"^Inbox")).click()
        await page.get_by_role("heading", name="Activity", level=2).wait_for(timeout=30_000)
        await page.wait_for_timeout(750)
        rows = page.locator("article.inbox-item")
        count = await rows.count()
        report.figure("notifications", count)
        if count:
            report.figure(
                "notification_title",
                _one_space(await rows.first.locator("strong").first.inner_text()),
            )
            await rows.first.get_by_role(
                "button", name=re.compile(r"^(Open in chat|Decide in chat|Review)$")
            ).click()
            await page.wait_for_url(re.compile(r"#chats"), timeout=30_000)
            opened = re.search(r"#chats/([A-Za-z0-9_-]+)", page.url)
            if opened and not report.thread_id:
                report.thread_id = opened.group(1)
            await report.shot(page, "notification-open")
            await (
                page.locator('nav[aria-label="Primary"]')
                .first.get_by_role("button", name="Ask", exact=True)
                .click()
            )
            await page.get_by_text("Ask me anything about your money.").wait_for(timeout=30_000)
        else:
            report.note(
                "The seeded database starts with an empty inbox, so no notification was opened"
            )
        await _assert_no_alert(page, report)

    async with Step(report, page, "walk every Settings section"):
        await (
            page.locator('nav[aria-label="Primary"]')
            .first.get_by_role("button", name="Settings", exact=True)
            .click()
        )
        nav = page.get_by_role("navigation", name="Settings sections")
        await nav.wait_for(timeout=30_000)
        labels = [_one_space(label) for label in await nav.get_by_role("link").all_inner_texts()]
        if not labels:
            raise JourneyFailure(report.current, "the Settings nav listed no sections")
        for label in labels:
            await nav.get_by_role("link", name=label, exact=True).click()
            await page.get_by_role("heading", level=3).first.wait_for(timeout=30_000)
            await page.wait_for_timeout(800)
            await _assert_no_alert(page, report)
            await report.shot(page, f"settings-{label}")
        report.figure("settings_sections", labels)

    if report.console_errors:
        raise JourneyFailure(
            "console", "browser console errors: " + " | ".join(report.console_errors[:3])
        )


async def _drive(
    args: argparse.Namespace,
    page: pw.Page,
    base_url: str,
    token: str,
    report: Report,
) -> None:
    async with Step(report, page, "enroll a passkey through the setup UI"):
        await _enroll(page, base_url, token, report)

    if args.journey == "walkthrough":
        await _walkthrough(page, report)
    elif args.journey == "enroll":
        if args.dry_run:
            await _onboarding_ui(page, report)
        else:
            await _first_answer(page, report)
    else:
        await _home_vs_chat(page, report, dry_run=args.dry_run)


# --- start, run, stop -------------------------------------------------------------------------


async def _browse(args: argparse.Namespace, base_url: str, token: str, report: Report) -> None:
    async with pw.async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        _listen(page, report)
        cdp = await _virtual_authenticator(context, page)
        try:
            await _drive(args, page, base_url, token, report)
        except pw.Error as exc:
            raise JourneyFailure(report.current, str(exc).splitlines()[0]) from None
        finally:
            report.html = report.directory / f"{args.journey}.html"
            report.html.write_text(await page.content(), encoding="utf-8")
            await browser.close()
            del cdp


def run(args: argparse.Namespace) -> dict[str, Any]:
    journey = args.journey
    database = prepare_database(empty=journey == "enroll", seed=journey != "enroll")
    if journey != "enroll":
        _cover_card_balances()
    if journey == "walkthrough":
        _seed_notification()
    os.environ.update(server_env(args.port))
    if args.dry_run:
        os.environ.update(
            CHAT_FALLBACK_BASE_URL="", CHAT_FALLBACK_MODEL="", BATCH_MODEL="", ARNO_JOBS_ENABLED="0"
        )
        for role in ("CHAT", "PARSE", "VISION", "STT"):
            os.environ.update(
                {
                    f"{role}_PROVIDER": "openai",
                    f"{role}_BASE_URL": "http://127.0.0.1:9/v1",
                    f"{role}_API_KEY": "dry-run",
                    f"{role}_MODEL": "dry-run",
                    f"{role}_BILLING_MODE": "local",
                }
            )
    _require_free_port(args.port)

    base_url = f"http://localhost:{args.port}"
    directory = state_dir() / "browser" / journey
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.png"):
        stale.unlink()
    report = Report(journey, directory, base_url=base_url, database=database, dry_run=args.dry_run)

    log_path = state_dir() / LOG_FILE
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    process = start_server(args.port, log_path)
    try:
        wait_for_health(base_url, args.timeout, process)
        token = _setup_token(base_url)
        asyncio.run(_browse(args, base_url, token, report))
    finally:
        stop_server(process.pid)

    summary = report.summary()
    (directory / f"summary-{journey}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tests.e2e.browser_journey",
        description="Drive real Arno UI journeys in Chromium against a scratch database.",
    )
    parser.add_argument("journey", choices=JOURNEYS, help="which journey to run")
    parser.add_argument("--port", type=int, default=8131, help="the server's port")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="use closed model endpoints and run the model-free steps",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for health")
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except JourneyFailure as exc:
        payload = {"journey": args.journey, "failed_step": exc.step, "error": exc.detail}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"FAILED at {exc.step}: {exc.detail}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
