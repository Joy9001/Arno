# Assistant UI quote contract

This file records the backend half of assistant-ui quoting: the wire shape the installed client
sends, how the server validates and forwards it, what is persisted, and the checks that were run.
It covers quoting only; `assistant-ui-adoption.md` owns the wider adoption record.

## Wire contract

The installed `@assistant-ui/react` 0.15.32 composer writes a quote to the outgoing user message:

```json
{
  "metadata": {
    "custom": {
      "quote": { "text": "selected plain text", "messageId": "msg_source" }
    }
  }
}
```

- `QuoteInfo` is `{ readonly text: string; readonly messageId: string }`
  (`@assistant-ui/core` `types/quote.d.ts`).
- `setQuote()` stores it on the composer; submit writes it at
  `message.metadata.custom.quote` (`runtime/base/base-composer-runtime-core.js`).
- `getMessageQuote()` and `MessagePrimitiveQuote` read the same path, so a reloaded
  `metadata.custom.quote` renders without a second server lookup.
- `@assistant-ui/ai-sdk` 0.0.16 `toCreateMessage` forwards `message.metadata` unchanged, and
  `AssistantChatTransport` sends `messages` unchanged apart from the app's own last-turn slice.
  The server therefore reads exactly `metadata.custom.quote`; no other shape is guessed.
- The official `injectQuoteContext` helper prepends the quote as a Markdown blockquote. The server
  reproduces that documented transformation in Python because the backend is Pydantic AI, not the
  AI SDK route handler.

The browser does not put the quoted text into message parts. The quote is metadata only, so the
user bubble stays the actual question plus the `MessagePrimitiveQuote` component.

## Validation

`src/fin_agent/agent/quote.py` owns the boundary:

- Only the newest user turn is inspected, and only `metadata.custom.quote`.
- `text` must be a non-empty string of at most 4000 characters.
- `messageId` must be a non-empty string of at most 100 characters.
- The source must resolve to a row in the same `conversation_id` by either `messages.id` (the
  assistant UI id after reload) or `messages.client_id` (the user UI id). This covers both real
  IDs the UI holds.
- The source role must be `user` or `assistant` and the stored content must contain a non-empty
  text part. A data-only message is not a valid quote source.
- The quoted text is a snapshot of plain rendered text. It is never matched against the stored
  Markdown or HTML, because browser selection is plain text.
- `redact_text` runs on the quote before storage and before it reaches the model. A card number,
  for example, is stored and shown as `[redacted:credit_card]`.
- A quote is context, not an instruction and not authorization: the server never turns it into a
  tool call or a write, and it is not placed in the system prompt.

A malformed, missing, cross-thread, data-only, or oversized quote returns `422` with a specific
detail before any statement import, photo capture, or model turn starts. No user message is stored
for a refused quote. The chosen behavior is one consistent refusal rather than silently dropping
the quote or falling back to another message.

Approval continuation requests skip quote parsing and injection. The quote already lives on the
original stored user message and in model history, so re-injecting it would duplicate it.

## Forwarding to the model

`post_chat` builds the adapter with the public constructor path:

```python
adapter = await VercelAIAdapter.from_request(
    request,
    agent=app.state.agent,
    sdk_version=7,
    manage_message_history="server",
    allowed_file_url_schemes=frozenset(),
)
if quote is not None:
    adapter = replace(adapter, run_input=with_quote(adapter.run_input, quote))
```

`with_quote` uses only public types:

- It finds the newest `user` message in `adapter.run_input.messages`.
- It prepends a `TextUIPart` containing the official blockquote (`> line` per line, then a blank
  line) and rebuilds the message with `model_copy`.
- It rebuilds the run input with `run_input.model_copy(update={"messages": ...})`.

There is no `Request._body` mutation, no custom transport or parser, and no invented assistant
message. The adapter's normal `load_messages` path turns the injected part into model context.

With `manage_message_history="server"`, the injected user turn is what the adapter appends to the
stored model history. On the next turn the model sees the quote once, from history, without the
client re-sending it.

## Persistence

`record_user_message` stores the validated quote on the initial UI user message:

```json
{
  "id": "client-id",
  "role": "user",
  "parts": [{ "type": "text", "text": "the actual question" }],
  "metadata": { "custom": { "quote": { "text": "...", "messageId": "..." } } }
}
```

`save_records` then:

- keeps the stored `body_text` for the transcript, so the injected model blockquote never becomes
  a second visible quote;
- restores `data-statement` and `data-photo` refs from the stored message;
- merges the stored `metadata.custom.quote` back into the dumped user message without dropping the
  adapter's own metadata (for example the `pydantic_ai.timestamp` entry).

The parent review also covers the adapter's fallback user-message ID and an empty question:
both restore the original stored text and quote metadata, so neither case exposes the injected
model blockquote as a duplicate user message.

Before the adapter loads the turn, its quote metadata is also replaced with the validated,
redacted snapshot. Sanitizing only the injected text would leave the original metadata in model
history. A regression checks the model request and persisted model history as well as the UI.

`GET /api/threads/{id}/messages` already merges metadata, so a reload shows the quote component and
the question exactly once. A second turn keeps the quote in model history and does not repeat any
statement import or photo capture.

Turns without a quote keep their old shape: no `metadata.custom` entry, unchanged transcript, and
unchanged model context.

## Resumable stream header

Every chat stream response now includes:

```text
x-resumable-stream-id: <conversation id>
```

This matches `assistant-stream`'s `RESUMABLE_STREAM_ID_HEADER` value, so the official client
storage can capture the checkpoint without app-side bookkeeping. It is set on:

- `_stream_response` (new turns, reattach, and `GET /api/chat/{id}/stream`);
- the saved-turn replay response for a reused message id.

`X-Thread-Id` remains for compatibility. No other stream behavior changed.

## Checks run

All checks ran on November 6, 2026 against one disposable Postgres 18.6 container with a unique
`quote_review_<id>_test` database. The container was removed after the run. No production or shared
database, frontend build, or browser probe was used.

| Check | Command | Result |
|---|---|---|
| Scoped Ruff | `ruff check` on `agent/quote.py`, `agent/history.py`, `api/chat.py`, `tests/test_chat_quote.py` | Exit 0, no findings |
| New quote tests | `pytest tests/test_chat_quote.py` | 9 passed |
| Focused existing tests | `pytest tests/test_chat_quote.py tests/test_chat_photo_attachment.py tests/test_chat_statement_attachment.py tests/test_capture.py tests/test_agent_turns.py tests/test_agent_history.py tests/test_import_api.py` | 46 passed |

After the parent review fixes, 33 tests passed across quoting, agent history, statement attachments,
and photo attachments on a fresh disposable database. A further focused redaction regression
passed on another fresh database after sanitizing adapter metadata. Both containers were removed.

The new tests assert:

- the quote reaches the model as a user blockquote and not as a system instruction;
- the persisted user text is the actual question, with `metadata.custom.quote` on reload;
- the source resolves by either the server row id or the live assistant client id;
- card-number quote text is redacted before storage and model context;
- a second turn keeps one copy of the quote in history and no quote metadata on the new turn;
- cross-thread, missing, malformed, and oversized quotes return 422 with no message written;
- a legacy turn has no quote metadata and keeps both stream headers;
- a quote plus a statement keeps both refs and imports the statement once;
- `save_records` preserves quote metadata while keeping the typed question.

## Limits

- The frontend must use the installed `setQuote`/`Quote` component so the wire stays
  `metadata.custom.quote`. This backend does not accept a top-level `metadata.quote` shape.
- Only one quote is active at a time, matching the library.
- A quote source must already be persisted in the conversation. A brand-new thread has no valid
  source, so a quote sent there is refused with 422.
- No end-to-end approval-continuation test was added; the approval path skips quote handling by
  construction and existing approval/history tests still pass.
- The quote is text context only. It cannot authorize a write and is not a tool input.
