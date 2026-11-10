"""Detached chat turns: a run of its own, a replayable buffer, and Stop through a token.

The response reads from the buffer; a client that leaves stops reading, not running. Every ending
saves the same two records, so a finished, stopped, or failed turn is always visible next time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import weakref
from collections.abc import Callable, Coroutine
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import DeferredToolRequests, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import DeferredToolResults, ToolDenied
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai_harness.subagents import DelegationReports, DelegationTasks

from arno.agent import capabilities as caps
from arno.agent.cards import cards_from_messages, data_part
from arno.agent.deps import AgentDeps
from arno.agent.history import join_text, merge_text_parts, save_records
from arno.agent.models import opencode_conversation
from arno.agent.number_check import check_messages
from arno.agent.presentation import keep_last_answer
from arno.agent.wake import KeepRetryMark, WakePolicy
from arno.db.conn import connect
from arno.engine.money import money

logger = logging.getLogger("arno.chat")

DENIED_MESSAGE = (
    "The user declined this change, so nothing was written. Stop here: don't offer it again "
    "unless they ask for it."
)


def declined_results(results: DeferredToolResults | None) -> DeferredToolResults | None:
    """Give a declined call the message the model should act on, not a bare one-liner."""
    if results is None or not any(value is False for value in results.approvals.values()):
        return results
    return DeferredToolResults(
        approvals={
            tool_call_id: (ToolDenied(message=DENIED_MESSAGE) if value is False else value)
            for tool_call_id, value in results.approvals.items()
        },
        calls=results.calls,
        metadata=results.metadata,
    )


UNREAD_STATEMENT_NOTE_PREFIX = "Nothing was imported: the attached statement"


def _plain(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _rupees(value: Any) -> str | None:
    """The amount in the app's own money format, or None when it is not a number."""
    try:
        amount = int(Decimal(str(value)) * 100)
    except ArithmeticError, TypeError, ValueError:
        return None
    return money(amount)["text"]


def _setup_sentence(action: str, data: dict[str, Any], entity: str) -> str:
    floor = _rupees(data.get("floor_rupees"))
    if entity == "settings":
        if not floor:
            return "Approve changing your settings."
        return f"Approve setting your floor to {floor}."
    if entity == "balance":
        balance = _rupees(data.get("balance_rupees"))
        if balance and data.get("account"):
            return f"Approve setting the {_plain(data['account'], 40)} balance to {balance}."
        return "Approve setting a balance."
    noun = {
        "account": "account",
        "schedule": "schedule",
        "goal": "goal",
        "category": "category",
        "payee": "payee",
        "scheduled_question": "scheduled question",
        "baseline": "baseline",
    }.get(entity, entity.replace("_", " "))
    verb = {"add": "adding", "update": "changing", "archive": "archiving"}.get(action, "changing")
    name = data.get("name") or data.get("account") or data.get("category") or data.get("prompt")
    return f"Approve {verb} the {noun} {_plain(name, 40)}." if name else f"Approve {verb} a {noun}."


def approval_sentence(tool_name: str, arguments: dict[str, Any]) -> str:
    """One plain sentence for a confirm card, from the call's own name and arguments.

    The model is asked to write this itself; when it doesn't, the card still arrives with words.
    """
    if tool_name == "preference":
        action = str(arguments.get("action") or "remember")
        subject = _plain(arguments.get("text") or arguments.get("id") or "this memory").rstrip(".")
        if action == "forget":
            return f"Approve forgetting this: {subject}."
        if action == "classify":
            return f"Approve reclassifying this memory: {subject}."
        return f"Approve saving this {_plain(arguments.get('kind') or 'memory', 20)}: {subject}."
    if tool_name == "update_setup":
        data = arguments.get("data")
        return _setup_sentence(
            str(arguments.get("action") or "update"),
            data if isinstance(data, dict) else {},
            str(arguments.get("entity") or ""),
        )
    return f"Approve running {tool_name.replace('_', ' ')}."


def approval_text(output: Any, response: Any) -> str | None:
    """The host's sentence for a confirm-card turn the model left wordless.

    Returns None when the model wrote text itself, and when the run ended on anything but an
    approval. Money is written the way tool returns write it.
    """
    if not isinstance(output, DeferredToolRequests) or not output.approvals:
        return None
    if isinstance(response, ModelResponse) and any(
        isinstance(part, TextPart) and part.content.strip() for part in response.parts
    ):
        return None
    sentences: list[str] = []
    for call in output.approvals:
        arguments: dict[str, Any]
        try:
            arguments = call.args_as_dict()
        except TypeError, ValueError:
            arguments = {}
        sentence = approval_sentence(call.tool_name, arguments)
        if sentence and sentence not in sentences:
            sentences.append(sentence)
    return " ".join(sentences) if sentences else None


def pending_approvals(messages: list[Any], answered: set[str]) -> list[ToolCallPart]:
    """The confirm cards the last run offered that no tool result has closed yet."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.tool_calls:
            return [call for call in message.tool_calls if str(call.tool_call_id) not in answered]
    return []


def unread_statement_note(names: list[str]) -> str:
    """What the reply says when the model answered without handing the upload to the parser."""
    listed = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    if len(names) == 1:
        return (
            f"{UNREAD_STATEMENT_NOTE_PREFIX} file ({listed}) wasn't handed to the parser. "
            'Say "read the statement" and I\'ll hand it over.'
        )
    return (
        f"{UNREAD_STATEMENT_NOTE_PREFIX} files ({listed}) weren't handed to the parser. "
        'Say "read the statement" and I\'ll hand them over.'
    )


def _delegated_to_parser(messages: list[Any]) -> bool:
    """Whether this turn handed the files over, whatever the parser has written so far."""
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart) or part.tool_name != "delegate_task":
                continue
            try:
                args = part.args_as_dict()
            except TypeError, ValueError:
                continue
            if str(args.get("agent_name") or "") == "parser":
                return True
    return False


async def _unread_statements(turn: ChatTurn, messages: list[Any]) -> list[str]:
    """The filenames of this turn's statement attachments that no import references.

    A model that forgot to delegate leaves the file orphaned, so the reply must say so, and name
    the file: a retry that names nothing invites "no statement came through". A delegation still
    starting has no row yet, so the call itself is the first answer.
    """
    attachments = tuple(dict.fromkeys(turn.deps.statement_attachments))
    if not attachments or _delegated_to_parser(messages):
        return []
    async with connect() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id, a.filename,
                   EXISTS (SELECT 1 FROM imports i WHERE i.attachment_id = a.id) AS imported
            FROM attachments a
            WHERE a.id = ANY($1::text[])
            """,
            list(attachments),
        )
    unread = {
        str(row["id"]): str(row["filename"] or "statement") for row in rows if not row["imported"]
    }
    return [unread[attachment] for attachment in attachments if attachment in unread]


class TurnBusy(RuntimeError):
    """One running turn per thread."""


class PreparationBusy(RuntimeError):
    """Another request is still preparing a turn on this thread."""


class Preparation:
    """The per-thread guard for the window before a turn is registered.

    `TurnManager.start` marks a thread busy only at the end of preparation, so without this guard
    a second request could also import, capture, or start a turn while the first is still waiting
    on a slow import or a vision call.
    """

    __slots__ = ("_lock", "__weakref__")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._lock.locked():
            raise PreparationBusy(
                "Another message is still being prepared in this thread. Wait a moment and retry."
            )
        await self._lock.acquire()

    def release(self) -> None:
        if self._lock.locked():
            self._lock.release()


@dataclass
class ChatTurn:
    thread_id: str
    message_id: str
    run_id: str
    history: list[Any]
    deps: AgentDeps
    title: str | None = None
    token: Any = None
    chunks: list[str] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    saved: bool = False
    error: str | None = None
    task: asyncio.Task[Any] | None = None
    model_name: str | None = None
    used_fallback: bool = False
    number_flags: list[str] = field(default_factory=list)
    initial_cards: list[dict[str, Any]] = field(default_factory=list)
    reply_id: str | None = None
    note: str | None = None
    ending: str | None = None
    # A confirm-card sentence the host wrote because the model wrote none.
    host_text: str | None = None
    # Set on a turn the server started for a waiting task report; no user message opened it.
    wake: WakePolicy | None = None

    async def push(self, chunk: str) -> None:
        async with self.condition:
            self.chunks.append(chunk)
            self.condition.notify_all()

    async def finish(self) -> None:
        async with self.condition:
            self.done.set()
            self.condition.notify_all()

    async def follow(self, start: int = 0) -> Any:
        index = start
        while True:
            async with self.condition:
                while index >= len(self.chunks) and not self.done.is_set():
                    await self.condition.wait()
                while index < len(self.chunks):
                    chunk = self.chunks[index]
                    index += 1
                    yield chunk
                if self.done.is_set() and index >= len(self.chunks):
                    return


def _event_chunk(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _plain_errors(chunk: str) -> str:
    if not chunk.startswith("data: "):
        return chunk
    try:
        payload = json.loads(chunk[6:].strip())
    except json.JSONDecodeError:
        return chunk
    if isinstance(payload, dict) and payload.get("type") == "error":
        # The browser gets plain words; the log keeps the real error for diagnosis.
        logger.warning("chat stream error: %s", str(payload.get("errorText", ""))[:2000])
        payload["errorText"] = "The model failed. Nothing was lost; try again."
        return _event_chunk(payload)
    return chunk


def encode_saved_turn(ui_message: dict[str, Any]) -> list[str]:
    """Replay a saved assistant message as a Vercel AI data stream."""
    message_id = str(ui_message.get("id") or uuid.uuid4())
    chunks = [_event_chunk({"type": "start", "messageId": message_id})]
    for index, part in enumerate(merge_text_parts(list(ui_message.get("parts") or []))):
        kind = part.get("type")
        if kind == "text":
            text_id = f"{message_id}-text-{index}"
            chunks.append(_event_chunk({"type": "text-start", "id": text_id}))
            chunks.append(
                _event_chunk({"type": "text-delta", "id": text_id, "delta": part.get("text", "")})
            )
            chunks.append(_event_chunk({"type": "text-end", "id": text_id}))
        elif isinstance(kind, str) and kind.startswith("data-"):
            # Older stored parts may carry null optionals; the chunk schema forbids null.
            payload = {
                key: value
                for key, value in part.items()
                if not (key in {"id", "transient"} and value is None)
            }
            chunks.append(_event_chunk(payload))
    chunks.append(_event_chunk({"type": "finish"}))
    return chunks


def _this_turn(
    messages: list[Any], history: list[Any], run_id: str, ui_message_id: str
) -> list[Any]:
    """The turn's messages, found by run tag plus the client's message id.

    Compaction may shorten the history, so a length slice can lose the user's message. The run id
    tags every message the run adds, and the adapter keeps the client's UI id on its request.
    """
    from pydantic_ai.ui import _utils

    tagged = [message for message in messages if getattr(message, "run_id", None) == run_id]
    user = next(
        (message for message in messages if _utils.get_ui_message_id(message) == ui_message_id),
        None,
    )
    if tagged:
        if user is not None and user not in tagged:
            tagged.insert(0, user)
        return tagged
    if len(messages) >= len(history):
        return messages[len(history) :]
    return messages


class TurnManager:
    def __init__(
        self,
        agent: Any,
        *,
        drain_seconds: float = 20.0,
        tasks: DelegationTasks | None = None,
    ):
        self.agent = agent
        self.drain_seconds = drain_seconds
        self.tasks = tasks
        self.turns: dict[str, ChatTurn] = {}
        # Starts a turn for a thread whose task report is waiting; the app sets it.
        self.waker: Callable[[str], Coroutine[Any, Any, Any]] | None = None
        self._waking: set[asyncio.Task[Any]] = set()
        self.closing = False
        self._guard = asyncio.Lock()
        # Weak so a finished request's guard is collected; a missing thread id is request-local.
        self._preparing: weakref.WeakValueDictionary[str, Preparation] = (
            weakref.WeakValueDictionary()
        )

    def get(self, thread_id: str) -> ChatTurn | None:
        return self.turns.get(thread_id)

    def prepare(self, thread_id: str | None) -> Preparation:
        """The per-thread preparation guard, held from request validation until turn start."""
        if not thread_id:
            return Preparation()
        guard = self._preparing.get(thread_id)
        if guard is None:
            guard = Preparation()
            self._preparing[thread_id] = guard
        return guard

    async def start(
        self,
        *,
        adapter: VercelAIAdapter[Any, Any],
        thread_id: str,
        message_id: str,
        history: list[Any],
        deps: AgentDeps,
        title: str | None = None,
        run_id: str | None = None,
        initial_cards: list[dict[str, Any]] | None = None,
        reply_id: str | None = None,
        wake: WakePolicy | None = None,
    ) -> ChatTurn:
        from pydantic_ai import CancellationToken

        async with self._guard:
            existing = self.turns.get(thread_id)
            if existing is not None and not existing.done.is_set():
                raise TurnBusy("A reply is already running in this thread.")
            run_id = run_id or f"run_{uuid.uuid4().hex}"
            deps.run_id = run_id
            turn = ChatTurn(
                thread_id=thread_id,
                message_id=message_id,
                run_id=run_id,
                history=history,
                deps=deps,
                title=title,
                initial_cards=initial_cards or [],
                reply_id=reply_id,
                token=CancellationToken(),
                wake=wake,
            )
            self.turns[thread_id] = turn
            turn.task = asyncio.create_task(self._run(turn, adapter))
            return turn

    def schedule_wake(self, thread_id: str) -> None:
        """Ask the app to start a turn for this thread's waiting report, off the caller's path."""
        if self.waker is None or self.closing:
            return
        task = asyncio.create_task(self.waker(thread_id))
        self._waking.add(task)
        task.add_done_callback(self._waking.discard)

    def stop(self, thread_id: str) -> bool:
        turn = self.turns.get(thread_id)
        if turn is None or turn.done.is_set():
            return False
        turn.token.cancel()
        return True

    async def _save(
        self,
        turn: ChatTurn,
        messages: list[Any],
        *,
        ending: str,
        note: str | None = None,
        model_name: str | None = None,
        failed_attempts: list[str] | None = None,
        all_messages: list[Any] | None = None,
    ) -> None:
        reply = ""
        finish_reason = None
        reply_fallback = False
        for message in reversed(messages):
            if isinstance(message, ModelResponse):
                reply = join_text(
                    [part.content for part in message.parts if isinstance(part, TextPart)]
                )
                model_name = model_name or message.model_name
                finish_reason = getattr(message, "finish_reason", None)
                reply_fallback = bool(message.failed_attempts)
                break
        if finish_reason == "length" and note is None:
            note = "The reply hit the output cap. Ask for the rest."
        # Real use notes money and percentages only; bare counts are an eval-only check
        # (system-design step 9), so a day or a list size never earns a "couldn't trace" note.
        # Trace against what the model could see, history included: a pinned snapshot carries
        # figures the current turn never repeats. Old assistant replies are not sources.
        context = all_messages or [*turn.history, *messages]
        flags = check_messages(reply, context, kinds=("money", "percent")) if reply else []
        badge = None
        primary = getattr(getattr(self.agent, "model", None), "model_name", None)
        if failed_attempts or (primary and model_name and model_name != primary):
            badge = model_name or "backup"
        async with connect() as conn:
            async with conn.transaction():
                await save_records(
                    conn,
                    thread_id=turn.thread_id,
                    run_id=turn.run_id,
                    ui_message_id=turn.message_id,
                    history=turn.history,
                    turn_messages=messages,
                    ending=ending,
                    number_flags=flags,
                    model_badge=badge,
                    reply_model=model_name,
                    reply_fallback=reply_fallback,
                    save_note=note,
                    title=turn.title,
                    all_messages=all_messages,
                    extra_cards=turn.initial_cards,
                    reply_id=turn.reply_id,
                )
                if failed_attempts:
                    await conn.execute(
                        """
                        UPDATE settings
                        SET last_model_error_type = $1, last_model_error_at = now(),
                            updated_at = now()
                        WHERE id = 1
                        """,
                        ", ".join(sorted(set(failed_attempts)))[:200],
                    )
        turn.number_flags = flags
        turn.model_name = model_name
        turn.used_fallback = reply_fallback
        turn.note = note
        turn.ending = ending
        turn.saved = True

    async def _save_failed(self, turn: ChatTurn, captured: list[Any], *, note: str) -> None:
        """Save what a failed run added, never the history it started from.

        The captured list is every message of the run, history included, so it is cut to this
        turn the same way a finished turn is.
        """
        messages = list(captured)
        await self._save(
            turn,
            _this_turn(messages, turn.history, turn.run_id, turn.message_id),
            ending="failed",
            note=note,
            all_messages=messages or None,
        )

    async def _run(self, turn: ChatTurn, adapter: VercelAIAdapter[Any, Any]) -> None:
        captured: list[Any] = []
        captured_ref = [captured]
        try:

            async def on_complete(result: Any) -> None:
                messages = list(result.all_messages())
                turn_messages = _this_turn(messages, turn.history, turn.run_id, turn.message_id)
                failed: list[str] = []
                # Every model call in this turn, not only the last one: a fallback in an
                # earlier tool-loop request must still be counted once.
                for message in turn_messages:
                    if not isinstance(message, ModelResponse):
                        continue
                    for item in message.failed_attempts or []:
                        if isinstance(item, dict):
                            failed.append(str(item.get("model_name") or item.get("model")))
                        else:
                            failed.append(str(getattr(item, "model_name", item)))
                # Only this turn's messages: a delegate call in the history is another turn's.
                unread = await _unread_statements(turn, turn_messages)
                # A confirm card with no words is a broken confirm: the model was asked for one
                # sentence and did not write it, so the host writes it from the call's arguments.
                # It goes into the saved text too, so a reload shows the same words.
                response = next(
                    (
                        message
                        for message in reversed(messages)
                        if isinstance(message, ModelResponse)
                    ),
                    None,
                )
                text = approval_text(result.output, response)
                if text is not None and response is not None:
                    turn.host_text = text
                    patched = replace(response, parts=[*response.parts, TextPart(text)])
                    messages = [patched if item is response else item for item in messages]
                    turn_messages = [
                        patched if item is response else item for item in turn_messages
                    ]
                await self._save(
                    turn,
                    turn_messages,
                    ending="complete",
                    note=unread_statement_note(unread) if unread else None,
                    failed_attempts=[name for name in failed if name] or None,
                    all_messages=messages,
                )

            async def on_cancel(exc: RunCancelled) -> None:
                messages = list(exc.all_messages())
                turn_messages = _this_turn(messages, turn.history, turn.run_id, turn.message_id)
                await self._save(turn, turn_messages, ending="cancelled", all_messages=messages)

            with capture_run_messages() as run_messages:
                captured_ref[0] = run_messages
                # A server-started turn has no prompt: its waiting reports are the first request.
                reports: list[Any] = (
                    [
                        DelegationReports(
                            self.tasks,
                            conversation_id=turn.thread_id,
                            priority="asap" if turn.wake is not None else "when_idle",
                        )
                    ]
                    if self.tasks is not None
                    else []
                )
                if self.tasks is not None:
                    reports.append(KeepRetryMark(self.tasks))
                if turn.wake is not None:
                    reports.append(turn.wake)
                bound = self.tasks.bind() if self.tasks is not None else nullcontext()
                with bound, opencode_conversation(turn.thread_id):
                    stream = adapter.run_stream(
                        message_history=turn.history,
                        conversation_id=turn.thread_id,
                        run_id=turn.run_id,
                        cancellation_token=turn.token,
                        model_settings=caps.MODEL_SETTINGS,
                        usage_limits=caps.turn_limits(),
                        deps=turn.deps,
                        capabilities=reports,
                        deferred_tool_results=declined_results(adapter.deferred_tool_results),
                        on_complete=on_complete,
                        on_cancel=on_cancel,
                    )
                    async for chunk in self._encoded(adapter, stream, captured_ref, turn):
                        await turn.push(_plain_errors(chunk))
            if not turn.saved:
                async with connect() as conn:
                    await conn.execute(
                        """
                        UPDATE settings
                        SET last_model_error_type = 'ModelStreamError',
                            last_model_error_at = now(), updated_at = now()
                        WHERE id = 1
                        """
                    )
                await self._save_failed(
                    turn,
                    captured_ref[0],
                    note="Couldn't answer: the model failed. Nothing was lost; try again.",
                )
                await turn.push(
                    _event_chunk(
                        {
                            "type": "data-ending",
                            "data": {"note": "Couldn't answer. Retry sends a new message."},
                        }
                    )
                )
        except asyncio.CancelledError:
            if not turn.saved:
                await self._save_failed(
                    turn,
                    captured_ref[0],
                    note="The turn stopped before it could save normally.",
                )
            raise
        except Exception as exc:  # noqa: BLE001 - every failure must save
            logger.error("chat turn failed (%s)", type(exc).__name__)
            turn.error = str(exc)
            async with connect() as conn:
                await conn.execute(
                    """
                    UPDATE settings
                    SET last_model_error_type = $1, last_model_error_at = now(),
                        updated_at = now()
                    WHERE id = 1
                    """,
                    type(exc).__name__[:200],
                )
            if not turn.saved:
                await self._save_failed(
                    turn,
                    captured_ref[0],
                    note="Couldn't answer: the model failed. Nothing was lost; try again.",
                )
            await turn.push(
                _event_chunk(
                    {
                        "type": "error",
                        "errorText": "The model failed. Nothing was lost; try again.",
                    }
                )
            )
        finally:
            await turn.finish()
            async with self._guard:
                if self.turns.get(turn.thread_id) is turn:
                    del self.turns[turn.thread_id]
            # A report that arrived after this run read its last queued message waits for the
            # next one. A server-started turn that failed or was stopped does not try again.
            if turn.wake is None or turn.ending == "complete":
                self.schedule_wake(turn.thread_id)

    async def _encoded(
        self,
        adapter: VercelAIAdapter[Any, Any],
        stream: Any,
        captured_ref: list[list[Any]],
        turn: ChatTurn,
    ) -> Any:
        """Encode the adapter's stream, adding the cards nested inside a `run_code` return."""
        imports_sent = False
        started_imports = {
            str(card.get("import_id")) for card in turn.initial_cards if card.get("import_id")
        }
        async for chunk in adapter.encode_stream(stream):
            if chunk.startswith("data: ") and '"type":"finish"' in chunk:
                if turn.host_text:
                    text_id = f"{turn.reply_id or 'reply'}-host"
                    yield _event_chunk({"type": "text-start", "id": text_id})
                    yield _event_chunk(
                        {"type": "text-delta", "id": text_id, "delta": turn.host_text}
                    )
                    yield _event_chunk({"type": "text-end", "id": text_id})
                for card in keep_last_answer(cards_from_messages(captured_ref[0])):
                    data = card.data if isinstance(card.data, dict) else {}
                    if (
                        data.get("card") == "import"
                        and str(data.get("import_id")) in started_imports
                    ):
                        continue
                    yield _event_chunk(data_part(card))
                if turn.number_flags:
                    yield _event_chunk(
                        {
                            "type": "data-number-flags",
                            "data": {"amounts": turn.number_flags},
                        }
                    )
                if turn.model_name and turn.model_name != getattr(
                    getattr(self.agent, "model", None), "model_name", None
                ):
                    yield _event_chunk(
                        {"type": "data-model-badge", "data": {"model": turn.model_name}}
                    )
                if turn.model_name:
                    yield _event_chunk(
                        {
                            "type": "data-reply-model",
                            "data": {
                                "model": turn.model_name,
                                "fallback": turn.used_fallback,
                            },
                        }
                    )
                if turn.ending == "complete" and turn.note:
                    yield _event_chunk({"type": "data-ending", "data": {"note": turn.note}})
            yield chunk
            # Start the SDK message before adding its import cards. Each upstream
            # event must pass through once, with the finish event still last.
            if not imports_sent:
                imports_sent = True
                for card in turn.initial_cards:
                    yield _event_chunk({"type": "data-card", "data": card})

    async def drain(self) -> None:
        """A deploy waits for running turns; then the token stops the rest and saves them."""
        self.closing = True
        running = [turn for turn in list(self.turns.values()) if not turn.done.is_set()]
        if not running:
            return
        waiter = asyncio.gather(*(turn.done.wait() for turn in running), return_exceptions=True)
        try:
            await asyncio.wait_for(waiter, timeout=self.drain_seconds)
            return
        except TimeoutError:
            pass
        for turn in running:
            turn.token.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*(turn.done.wait() for turn in running), return_exceptions=True),
                timeout=5,
            )
        except TimeoutError:
            for turn in running:
                if turn.task is not None:
                    turn.task.cancel()


__all__ = [
    "ChatTurn",
    "DENIED_MESSAGE",
    "Preparation",
    "PreparationBusy",
    "TurnBusy",
    "TurnManager",
    "declined_results",
    "encode_saved_turn",
]
