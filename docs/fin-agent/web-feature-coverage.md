# Web feature coverage after the final review pass

Status key: **Verified** means the listed check ran against the isolated fixture build described
below. **Implemented** means the code path exists and has a focused API or component-level check,
but the live authenticated flow was not exercised. **Deferred** names the decision that owns the
gap; **Design mismatch** means the audited expectation does not match the product decision.

Evidence boundary: the API suite used a fresh scratch Postgres database whose name ends `_test`.
The browser pass served the built `web/dist` from an isolated Node fixture with synthetic data,
then checked layout and interactions at 1864 x 1052 and 390 x 844. No production auth, live model,
real ledger, phone, passkey hardware, or external Phoenix instance was used. These checks do not
prove real-device or live-provider behavior.

## Parent review findings

- [x] **`run_code` args saved as a JSON string no longer lose the chart script — Verified.**
      `cards._run_scripts` now calls the installed PydanticAI `ToolCallPart.args_as_dict()`, which
      normalizes dict and JSON-string arguments and turns malformed legacy JSON into a harmless
      invalid-args map. Regression tests cover both a real JSON-string call and malformed input.
- [x] **A validated source thread loads even when it is older than the first `/threads` page —
      Verified.** Shell still validates the thread before focusing it. Assistant loads that exact
      id directly, shows the real thread title, does not substitute the newest thread, and renders
      an explicit unavailable notice with Retry and Start a new chat when the fetch fails.
      `New chat` and history selection still work.
- [x] **A movement's originating message opens, not just its thread — Verified.** The ledger detail
      sends `{threadId, messageId}`; Shell carries `#home/<thread>/<message>` and Assistant selects
      the exact thread and focuses the article whose DOM id uses `metadata.server_id`. The raw
      message id is no longer printed. Source navigation lands on the message, not the bottom; the
      normal streaming scroll is unchanged.
- [x] **Legacy backup badge is hidden when an authoritative reply-model part exists — Verified.**
      New assistant messages carrying `data-reply-model` render only `Model: <actual model>`.
      A saved message with only the legacy `data-model-badge` still renders it. The server's
      actual reply label is untouched.
- [x] **Radix dialog element contract preserved.** Settings archive and Passkeys keep Radix
      `AlertDialog.Cancel` / `AlertDialog.Action` wrapping Radix `Button` children. The archive
      confirmation was opened and cancelled in the browser with no runtime error.
- [x] **Article bubble overlap fix preserved.** `.bubble.agent` is still the assistant role class and
      no longer collides with the panel's `assistant` layout class. Six long turns measured at both
      widths: minimum vertical gap 18 px, zero negative gaps, zero bubble or document horizontal
      overflow.

## Audited surfaces

- [x] **Inline Undo on write cards — Implemented and API-tested.** Movement, correction, setup,
      baseline and preference cards now carry their real undo group ID. No-op operations expose
      no Undo action. Covered by `test_agent_card_undo.py`.
- [x] **Retry creates a fresh turn — Verified in the SDK fixture.** It sends a new user message
      containing `Try again`, preserving the earlier transcript instead of regenerating a reused
      message ID. Session-expiry errors offer sign-in.
- [x] **Calculation scripts and chart citations — Implemented and API-tested.** Code Mode charts
      carry their originating script and calculation timestamp through live and persisted paths.
      Scripts remain fully readable in bounded scroll regions; a data-as-of date is distinct from
      the calculation timestamp. Covered by `test_agent_chart_citation.py`.
- [x] **Output-cap notice on the current reply — Implemented and API-tested.** The finish signal
      emits one ending notice and preserves it on reload. Covered by `test_agent_output_cap.py`.
- [x] **Actual replying model and fallback — Implemented and API-tested.** The final provider
      response supplies the model label; fallback uses recorded failed attempts. Live and saved
      replies agree. Historical replies without this information are not assigned a guessed model.
      Covered by `test_agent_reply_model.py`.
- [x] **Category and merchant-rule editing — Implemented and API-tested.** Category rename,
      archive/restore and rule upsert preserve existing contracts and disclose rule ownership.
      Covered by `test_settings_contracts.py`; archive confirmation also browser-checked below.

- [x] **Chat transcript, tool details, model label — Verified.** Long multi-turn chat, expanded
      `run_code` input and output, and `Model: primary-model`; the legacy backup badge stayed hidden.
- [x] **Ledger movement details and source navigation — Verified.** Details dialog, recorded source,
      older thread not present in the first history response, exact message focused, bounded scroll.
- [x] **Account details open/close — Verified.** The account dialog showed the real account row and
      closed cleanly.
- [x] **Projection assumptions — Verified.** The assumption block showed the horizon and simulation
      runs from the payload.
- [x] **Settings category archive confirmation — Verified open + Cancel.** Also confirms the
      Radix runtime path.
- [x] **Scheduled question edit, pause, resume — Verified.** Save changed the prompt, pause showed
      its notice, resume restored the active state.
- [x] **Agent memory edit/save — Verified.** Memory rows remain saved preferences scoped to this
      instance, now editable through `PATCH /api/preferences/{id}/content`. No semantic
      fact/constraint schema was introduced.
- [x] **Current session — Verified.** Passkeys shows the signed-in synthetic user, freshness, and
      expiry text.
- [x] **Jobs runtime facts — Verified.** Configured chat model and host render; unconfigured roles
      say so; the Phoenix button appears only from the explicit operator URL.
- [x] **Generic chart direct call — Implemented.** A `show_chart` return outside `run_code` has no
      script source, so the card honestly omits it. No CodeMode source is invented.
- [ ] **Tappable per-number citations — Deferred.** Waiting for the L2 number check to stay quiet;
      see `system-design.md:1355`.
- [ ] **Read vs resolved — Design mismatch, not a defect.** Marking a notification read is not
      treated as completing the underlying task; no task lifecycle was added.
- [ ] **Money movement editors — Deferred / parked.** The audited brief did not include them, so no
      editor UI was added.

## Runtime and rollout

- Phoenix uses only an explicitly configured `FIN_AGENT_PHOENIX_UI_URL`; this work added no
  observability wiring and edited no protected observability file.
- The running Docker API image still carries an older baked `dist`. Do not treat the container as
  this build.
- Safe rollout after review: rebuild the web bundle, run the API suite against a fresh `_test`
  database, then rebuild and redeploy the API image; verify in-browser auth, one live model turn,
  and one real passkey device. No `.env` file was read for this pass.
