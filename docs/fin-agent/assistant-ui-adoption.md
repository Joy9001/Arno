# Assistant UI adoption

This file records how the web chat renders on top of assistant-ui, what owns persistence, and the
checks that were run. It covers the migration only. `plan.md` owns product decisions.

## Rendered library components

The chat is composed from the installed assistant-ui packages. The app supplies the styling,
server calls, and finance-specific parts; it does not fork the primitives.

| Area | Library component | App wrapper |
|---|---|---|
| Thread and viewport | `ThreadPrimitive.Root`, `Viewport`, `ViewportFooter` | `web/src/components/thread.aui.tsx` |
| Messages | `MessagePrimitive`, `ActionBarPrimitive`, `BranchPickerPrimitive` | `thread.aui.tsx` |
| Composer | `ComposerPrimitive.Root`, `Input`, `Send`, `Cancel`, `Dictate`, `StopDictation` | `thread.aui.tsx`, `web/src/chat/Chat.tsx` |
| Attachments | `ComposerAttachments`, `UserMessageAttachments` | `web/src/components/attachment.aui.tsx` |
| History | `ThreadListPrimitive` through `useRemoteThreadListRuntime` | `web/src/components/thread-list.aui.tsx` |
| Markdown | `MarkdownTextPrimitive` from `@assistant-ui/react-markdown` with `remark-gfm` | `web/src/components/markdown-text.tsx` |
| Reasoning and tools | `Reasoning`, `ToolFallback`, tool groups | `reasoning.aui.tsx`, `tool-fallback.aui.tsx`, `tool-group.aui.tsx` |
| Data parts | `useAssistantDataUI` registrations for card, chart, statement, photo, model, flags, ending | `web/src/chat/parts.tsx` |
| Approval | App `ApprovalCard` rendered from the tool call's `approval` state | `web/src/components/approval-card.tsx`, `parts.tsx` |
| Charts | `recharts` inside the app card | `web/src/chat/cards.tsx` |

The assistant is a rail on wide screens and a sheet below the compact breakpoint. Both use the same
`Assistant` tree, so the composer, history popover, approval card, and status line are shared.

## Adapters and persistence

The browser never becomes the source of truth for a conversation. The adapters map the library's
contracts onto the existing API.

| Adapter | Behavior |
|---|---|
| `ChatTransport` | Sends only the new turn (`messages.slice(-1)`) to `POST /api/chat` and carries `voice_attachment_id` when dictation produced one. |
| Resumable stream storage | Keeps a pending stream id in `sessionStorage` under `fin-agent-stream:<threadId>`, so a reconnect is keyed to one thread and cannot resume into another. |
| History adapter | `load()` fetches `GET /api/threads/:id/messages`; `append()` is deliberately empty because the server already persists every turn. |
| Thread list adapter | Lists, renames, and fetches through `/api/threads`; the server creates a row on the first chat, photo, or voice request. Archive and delete are not app features. |
| Title generation | The server titles a conversation as its first turn settles; the adapter returns an empty stream. |

The history `append()` no-op is what prevents duplicate writes. A replay after reload is one
`GET /api/chat/:id/stream` full replay, not a replay plus a second persisted turn.

## Stop versus detach

An explicit Stop is the only action that calls `POST /api/chat/:id/stop`. It first clears the
resumable stream id, then asks the server to end the detached run. If the request fails, the local
stream is still ended and the composer reports that the server may keep finishing the answer.

An abort caused by unmount, navigation, or switching threads is local only. It cancels the browser
reader and leaves the server run detached, so reopening the thread can replay it. Reload uses the
same replay endpoint. This distinction is enforced in `web/src/chat/runtime.ts`.

## Photos and speech to text

Photo and voice work is per thread, not per component instance.

- The composer no longer has a separate Photo control. Receipt photos are queued through the one
  attachment control described below. The share-target path still posts its already-captured image
  to `POST /api/chat/:id/photo`, and a failed capture still retries through
  `POST /api/chat/:id/photo/:attachmentId`; both keep the legacy immediate-capture behavior.
- `pendingPhoto` is keyed by the live thread id, so a share-target photo that arrives during a run
  waits in the thread that queued it.
- `VoiceAttachments` stores a transcript attachment id by thread and `take()` consumes it once, so
  the next send cannot inherit another thread's audio.
- Dictation uses `ServerDictationAdapter`: tap the microphone to record, tap again to stop. The
  `MediaRecorder` blob goes to `POST /api/transcribe`; the returned transcript is delivered through
  the library's speech callbacks and lands in the composer for review before sending.
- Cancel stops the microphone track and clears the session. A transcript that arrives after cancel
  is discarded and never attaches to the current or next message. Thread changes and unmount call
  `cancelActiveDictation()`, so the microphone cannot stay on.

## Attachments in chat

The composer renders exactly one existing registry control, `ComposerAddAttachment`
(`web/src/components/attachment.aui.tsx`), labelled `Attach files`. It uses the installed
`ComposerPrimitive.AddAttachment`, `ComposerAttachments`, and `ComposerPrimitive.AttachmentDropzone`
primitives. There is no Statement/Photo choice before the picker: one file picker accepts both
families, and the library owns multi-file selection, drag/drop, paste, thumbnails, previews,
removal, preparation, cancellation, and per-file errors.

The runtime uses `CompositeAttachmentAdapter([statementAttachmentAdapter, photoAttachmentAdapter])`:

- Statements (`web/src/chat/statement-attachment.ts`) accept PDF, CSV/TSV, XLSX, XLSB, ODS, and XLS
  up to 25 MB.
- Receipt photos (`web/src/chat/photo-attachment.ts`) accept PNG, JPEG, WebP, and GIF up to 25 MB.

Both adapters validate before Send and queue the file as `requires-action`; `send()` uploads only
when the user sends. The statement adapter posts `purpose=statement`; the photo adapter posts
`purpose=photo` and returns a `data-photo` app reference. `POST /api/attachments` validates the
image from its bytes, stores `kind='photo'` without extracting or logging, and returns the ref.
No document or image bytes enter the message, model, or a browser cloud.

`POST /api/chat` validates every `data-statement` and `data-photo` ref before mutating anything.
It then starts each statement import once through the existing review pipeline
(`create_import` dedupes by file hash) and runs the existing `log_photo` capture helper once per
photo with `record_history=False`. That option skips only the helper's `cap_` UI card
(`_save_card`); the helper still records the movement with the existing idempotency key, appends
the marked extraction and "One movement was logged" to `model_history`, and sets
`outside_text_at`. The model therefore sees the extracted facts and does not log the same receipt
again, while the UI transcript keeps exactly one user message and the initial capture card. The
user message is stored once with its text and refs; the receipt/retry and import cards flow through
the same initial-card mechanism and are saved on the assistant turn. A reload therefore shows one
photo receipt, not a second copy. The message-id replay check still returns the saved turn for a
resend, and a busy thread is rejected before any import or capture starts.

Preparation is also guarded per thread. `POST /api/chat` and the legacy photo/retry endpoints
acquire a public `TurnManager` preparation guard through an authenticated FastAPI yield dependency
with `scope="function"`. The guard is a standard `asyncio.Lock`; it covers ref validation, statement
imports, and photo capture, and releases when the endpoint function returns, before the streamed
response body is consumed. A second request on the same thread during that window gets a 409
("still being prepared") instead of importing, capturing, or starting a model turn; another thread
is unaffected. The guards live in a `WeakValueDictionary`, so finished requests do not accumulate
entries. Message-id replay, the Stop endpoint, and navigation detach keep their existing behavior.

The legacy `POST /api/attachments` path without a `purpose` still accepts HTML and every other
statement format the importers support, which keeps Activity Imports working. Only an explicit
`purpose=statement` applies the chat attachment restriction (PDF, CSV/TSV, XLSX, XLSB, ODS, XLS).
`test_legacy_statement_upload_still_accepts_html` asserts the legacy upload returns 201 with
`detected_kind="html"` and the explicit statement upload returns 422.

One send may contain images, documents, and caption text; file-only turns receive a short prompt
("Import the attached statement." / "Log the attached receipt or payment photo." / both). Failed
photos leave a `capture-retry` card with the existing Retry button. Retry uses the legacy
`POST /api/chat/:id/photo/:attachmentId` path, keeps the chat turn's text and tool UI, and logs the
capture once. Unsupported, empty, and oversized files keep the file in the composer with a visible,
specific error.

The import card polls `GET /api/imports/:id` only while the import is `parsing`,
`needs_password`, or `needs_input`; polling stops on completion or unmount. It shows the real
status and error, never a fabricated percentage, and links to the exact
`#activity/imports/<import-id>` route with status-specific labels (`Open review`, `Enter
password`, `Resolve details`). The review screen remains the only place for a password and the
only place with Commit.

Deep links flow through `Shell` -> `ActivityPage` -> `ImportsPanel`. The panel fetches the exact
import even when it is outside the first history page, handles initial load and same-page
back/forward, and on a missing id clears the detail and shows a retryable error instead of choosing
another import. The existing `#home/<thread>/<message>` route is unchanged.

## Reasoning presentation

The parent review also corrected reasoning/tool presentation without changing the data contract:
reasoning is ghost/collapsed by default even while streaming, manual expansion persists, and the
tool trigger reads `1 step` / `N steps` with an icon. This is independent of the statement flow.

## Dependencies and license generation

New runtime packages are `@assistant-ui/react` 0.15.32, `@assistant-ui/ai-sdk` 0.0.16,
`@assistant-ui/react-markdown` 0.14.24, and the existing `assistant-stream`, `ai` 7.0.159, and
`@ai-sdk/react` 4.0.163. The four assistant-ui packages and `assistant-stream` are MIT. The AI SDK
packages are Apache-2.0.

`pnpm --dir web build` runs `node scripts/collect-licenses.mjs` before Vite. The script reads
`pnpm licenses list --prod --json` and writes `web/public/THIRD_PARTY_NOTICES.txt`; the bundle then
ships that file. The production graph currently groups into 10 Apache-2.0, 273 MIT, 18 ISC, 4 BSD-3-Clause, 2 MPL-2.0,
1 BSD-2-Clause, 1 0BSD, 1 AFL-2.1-or-BSD-3-Clause, and 1 mixed MIT/ISC/BSD package.

## Approval continuation

A pending approval renders the app card with Confirm and Cancel. Confirming calls the library's
`respondToApproval`, which sends the continuation request. The card disables itself while that is in
flight, so a double click cannot queue a second continuation.

If the continuation is accepted and the network then fails, the library's SDK error path surfaces
the error under the composer; the app does not append or persist a second copy of the turn. The
server owns the transcript, and the history adapter's empty `append()` keeps a retry from
duplicating writes.

## Checks run

All checks ran on November 6, 2026 against the fixture or a disposable test database, not
production.

| Check | Command | Result |
|---|---|---|
| Lint | `pnpm --dir web lint` | Exit 0; ESLint reported no findings |
| Typecheck | `pnpm --dir web typecheck` | Exit 0; `tsc --noEmit` reported no errors |
| Build | `pnpm --dir web build` | Exit 0; Vite 8.3.5 transformed the app and generated the PWA service worker |
| Scoped backend lint | `ruff check` on the changed API, turn, capture, and test files | Exit 0, no findings |
| Focused backend API tests | `pytest tests/test_chat_photo_attachment.py tests/test_chat_statement_attachment.py tests/test_capture.py tests/test_agent_turns.py tests/test_agent_history.py tests/test_import_api.py` | 37 passed on one disposable `preparation_review_<id>_test` database inside a temporary Postgres 18.6 container |
| Preparation race regression | The new concurrent test with `TurnManager.prepare` stubbed out | Failed on the second request returning 200 instead of 409; passes with the guard |
| Unified attachment browser probe | `unified-attachments-probe.mjs` | One control, both file families, mixed/image-only sends, reload, retry, errors, no overflow at 1366/390/320 |

The build prints its existing chunk-size warning for the main JavaScript bundle (over 500 kB). That
warning predates this migration and does not fail the build.

The ten photo/attachment tests cover image-only, image plus text, mixed image plus statement,
replay/resend, missing refs, failed capture plus Retry, the upload type/size guards, the
preparation race, replay during an active stream, and Stop during a photo turn. The concurrency
test delays `extract_payment_photo`, sends a second chat to the same thread, and asserts a 409 with
no message or capture for it, one capture and one outside-history entry after the first request
finishes, a different thread still streaming, and a later replay returning the saved card. It fails
if the guard is stubbed out. The streaming test opens the first reply, waits for its first delta,
and resends the same message id: the replay returns 200 and one saved turn, proving the guard is
released before the body is consumed. The six existing statement tests still cover file plus text,
file-only, replay/resend, stale refs, unsupported uploads, and a busy chat. The capture tests still
cover legacy immediate photo capture, retry cards, outside-text marking, and the share/retry
endpoints. The 37-test focused run used one unique `preparation_review_<id>_test` database inside a
temporary Postgres 18.6 container; the container was removed after the run.

The local Playwright probe at
`C:\Users\joymr\AppData\Local\Temp\opencode\aui-browser\unified-attachments-probe.mjs` exercised
the single `Attach files` control, the combined accept list, a queued image and document with
remove, a mixed send (one upload of each purpose, one chat post, zero legacy photo posts), reload
persistence (one receipt card, one import card, one image preview, one statement chip), image-only
sending with the fallback prompt, unsupported/empty/oversized errors, a failed capture plus Retry,
and 1366x900, 390x844, and 320x844 layouts. Results recorded no horizontal overflow and kept the
composer inside the viewport. Screenshots and `results.json` are under
`C:\Users\joymr\AppData\Local\Temp\opencode\aui-evidence\unified-attachments`.

Screenshots and layout metrics are in
`C:\Users\joymr\AppData\Local\Temp\opencode\aui-evidence`. Each of 1366x900, 390x844, and 320x844
was exercised for the long Markdown table, message cards and chart, history popover, pending
approval, busy/Stop composer, money page, and settings page. At every capture,
`document.documentElement.scrollWidth` equaled `window.innerWidth`, `body.scrollWidth` equaled the
same value, and the composer stayed between the viewport edges. The 320 px table scrolls inside its
own wrapper instead of widening the page. The 1366 captures include the assistant rail.

## Limits

The parent review corrected two visual regressions after inspecting the screenshots: restored the
shared Today horizon-selector and compact-chart styles removed during chat cleanup, and replaced
financial-card Undo/Retry buttons with the installed shadcn Button component. Lint, the build
(including typecheck), and the screenshot matrix passed again after these changes. The parent
personally inspected the refreshed desktop and phone captures. The existing server CSP still
restricts images to the app origin, blobs and data; Markdown links render as text and Markdown
images remain disabled.

- The checks used the synthetic fixture at `web/scripts/assistant-ui-fixture/server.mjs`, not a live
  model provider. No real completion, tool run, or provider error was exercised.
- No live passkey, device microphone, or hardware camera was used. Dictation and photo paths ran
  under Playwright with synthetic media; the browser probe's 1x1 PNG is not a representative
  receipt, so its image scale is not a visual approval of real photos.
- The legacy share-target path still posts an already-captured image immediately to
  `POST /api/chat/:id/photo`; only composer-selected photos follow the queued chat turn.
- No deployment or commit was made. The app remains uncommitted, and the fixture server is a local
  test script, not a production endpoint.
- Browser persistence was checked through the fixture's in-memory thread store. The focused backend
  tests used a real disposable `*_test` database, never the production database.
- The preparation guard is in-process `asyncio` state, matching the existing single-process
  `TurnManager` design. It is not a distributed lock and does not coordinate separate app workers.
- The regression proof ran only the new concurrency test with `TurnManager.prepare` stubbed out on
  the same disposable container; it did not rerun the full suite without the guard.
- The 320 px and 390 px captures use emulated viewports; real mobile browser chrome and safe-area
  insets were not part of the run.
