# assistant-ui runtime review

Comparative audit of the installed assistant-ui 0.15.32 / `@assistant-ui/ai-sdk` 0.0.16 runtime against the official guide bodies. It records what was adopted, what stayed app-owned, and why.

## Adopted

| Guide capability | Adopted or unchanged | Why |
| --- | --- | --- |
| `createResumableSessionStorage` with a lazy thread key | **Adopted.** Replaced the hand-rolled `sessionStorage` storage, listener set, and alias cleanup in `web/src/chat/runtime.ts` with one storage instance per thread runtime (`web/src/chat/streams.ts`). | The library owns the pending-id cache, owner check, post-mount subscription, and 204/404 clearing. The lazy key is the documented multiple-thread pattern: `remoteId ?? id`, read on every access. |
| `AssistantChatTransport.resumable` header capture | **Adopted.** Removed the manual `setStreamId` from `sendMessages`; the transport now stores `x-resumable-stream-id` when the response arrives. | The header is the library's contract. The Python chat endpoint emits it on start and saved-turn replay responses. The isolated verification wrapper supplies the same header for synthetic streams. |
| `resumeApi` + full replay | **Adopted.** `resumeApi(streamId)` stays `GET /api/chat/{streamId}/stream`, and `resumeStreamIsReplay = true` is retained. | The Python `TurnManager` already replays the whole message from the same URL, including the original `start.messageId`; that is the condition the guide gives for manual Resume. |
| `canResume`, library Resume control, `onResumeError` | **Adopted.** `canResume: true` remains. Transient failures keep the checkpoint and expose `ComposerPrimitive.Resume`; 204/404 clears it; `onResumeError` clears terminal non-retryable failures and reports the transient case. | The library already owns retry-once-per-mount, checkpoint retention, and the Resume control. Replacing them with app state would duplicate the runtime. |
| `Dictate` / `StopDictation` / input lock / session lifecycle | **Adopted (already in use).** The composer uses `ComposerPrimitive.Dictate` and `StopDictation`; `disableInputDuringDictation = true` remains. | The library owns the session, interim/final text, send-time cancel, thread disposal, and the 100 ms status cleanup. |
| Official server-transcription adapter recipe | **Adopted where it applies.** The adapter keeps the recipe's `MediaRecorder` + `POST /api/transcribe` shape. | There is no first-party MediaRecorder session helper. The recipe is the public contract; the remaining code is the app's provenance and retry glue, not a reimplementation of the session controller. |
| `useAuiState` selectors | **Partially adopted.** Removed the duplicate `busy` selector that mirrored `running`. | One selector per value avoids two subscriptions to the same state. |
| `useAui` scopes and per-runtime state | **Adopted.** Storage and dictation are created in the per-thread runtime hook; a small `StreamCheckpoints` registry maps the thread's own storage instance to its Stop control. | Stop must clear the same in-memory storage instance the transport wrote to. This is a per-runtime registry, not a global active-thread store. |

## Deliberately unchanged

| Area | Why it stays app-owned |
| --- | --- |
| Explicit Stop | The app's Stop ends financial-agent work, so it must clear the checkpoint and `POST /api/chat/{id}/stop`. The guide's default Stop only disconnects the reader and retains the checkpoint; copying that would leave the server working after the user stopped it. |
| Navigation / unmount | The existing behavior is preserved: abort the local reader only, keep the detached server run, and let a reopen replay it. No stop request is sent. |
| Python `TurnManager` and the replay endpoint | No JavaScript server or Redis replacement. The Python service owns turn persistence, cancellation, and full-message replay. |
| `/api/transcribe` and attachment provenance | The configured server STT stays. Switching to Web Speech or another provider would change accuracy, language support, and the attachment/retry contract without a product request. |
| Custom dictation glue | Thread-scoped attachment ids, retry cards, status lines, and cancellation on thread switch are app behavior the `DictationAdapter` contract does not model. The glue now also covers unsupported browsers, quick stop before/just after recording starts, cancelled microphone acquisition and aborted STT, stopped tracks, and late results. |
| `aui.on("threads.selectionChanged")` | The subscription is paired with unmount cleanup that also cancels dictation. Splitting it into `useAuiEvent` plus a second effect would add code without changing behavior. |
| Editing, branching, realtime voice, Electron | Not implemented. These are unsupported or outside the requested runtime review. |

## Verification

Synthetic browser checks only; no real STT, provider, auth, or finance data.

- Fixture: read-only `web/scripts/assistant-ui-fixture/server.mjs` on `127.0.0.1:8018`.
- App: Vite dev server on `localhost:5178`, proxying `/api` to a temporary wrapper on `127.0.0.1:8020`.
- Wrapper: adds the resumable header already emitted by the Python endpoint, and can inject a one-shot resume `500`/`204` or a failed transcription. It is test-only and not committed.
- Harness: Node Playwright, real installed `AssistantChatTransport` and `useChatRuntime`; no copied transport logic.

44 delegate checks passed. Parent review reran the suite with two additional recovery assertions: **46/46 passed**, plus **2/2 recorder-construction failure checks**. These cover:

- fresh send, checkpoint captured from the response header, checkpoint cleared on finish;
- reload full replay exactly once, no duplicate parts, idle reload with no replay;
- transient resume `500` retains the checkpoint and offers Resume, manual Resume completes;
- resume `204` and `404` clear the checkpoint and do not loop;
- explicit Stop sends exactly one stop request, clears the checkpoint, and does not replay;
- navigation sends zero stop requests, never replays into the other thread, keeps the checkpoint while away, and the original answer continues on return;
- dictation quick stop before the microphone is ready (no transcription, tracks stopped, no false error);
- dictation transcript and server attachment id on send;
- failed transcription shows the retry card, retry sends with the failed attachment id;
- cancel during STT does not leak text or the attachment into the next thread;
- unsupported browser reports a clear message and makes no transcription request;
- transient reconnect shows one recovery message without a competing Retry;
- recorder-construction failure reports an error and releases microphone tracks.

The parent production build also passed after integration. Not covered: real microphone permissions, real transcription provider, auth, or multi-process replay. The Python start and replay response headers were checked during parent review. Live-provider reconnect behavior remains unverified.

## Limits

- `StreamCheckpoints` is keyed by the thread's local id, matching `threads.mainThreadId`. The app's adapter returns that same id as `remoteId`, so the server stop URL and the storage key agree. A future adapter that returns a different `remoteId` must keep the registry key in sync with the id the Stop control resolves.
- Parent review removed the duplicate reconnect error. The library's `thread.canResume` now selects one recovery message and its native Resume control; a fresh Retry is offered only when no replay is available. Authentication failures retain Sign in again. Recorder-construction failures also stop microphone tracks and report a useful error.
