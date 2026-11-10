# Owner to-do list

Things left for you to run or decide. Each item has the exact steps. Run commands from the repo root
(`J:\Development\Fin-Agent`) in Git Bash unless noted.

## 1. Rebuild the running app (do this first)

The Docker app still serves an older build. Rebuilding ships the latest web fixes and applies any
new migrations (0017 memory kinds, 0018 paused imports).

```bash
docker compose up -d --build api
docker compose ps                         # api should say "healthy" after ~30 s
docker compose logs api --since 5m | grep -iE "migrat|backup|error"
```

You should see `pre-migration dump written to ...` and `applied migrations [17]` the first time a
new migration runs. Build one image at a time; parallel builds have crashed Docker before.

After the rebuild, any chat whose parser report from the last 24 hours was never read gets a reply
from the agent on its own: one model call, plus one parser retry if the report was a fixable
failure. Your SBI chat may be one of them. That is the new behavior, not a fault.

## 2. End-to-end journeys (about 20 minutes, by hand)

Do these in the real app (`http://<your-tailscale-host>:8000`) on the desktop and once on your
phone. Note anything that looks wrong with a screenshot.

1. **Import → review → commit → undo**
   - Activity → Imports → upload one real statement.
   - Wait for it to reach review. Resolve every uncertain row.
   - Commit. Check Activity → Ledger shows the new rows and Money → Accounts balances moved.
   - Undo the commit from the import card. Check the rows and balances go back.
2. **Passkey enrollment**
   - Settings → Passkeys → add a passkey on a second device or browser.
   - Sign out, sign back in with the new passkey. Then remove it and confirm the list updates.
3. **Chat interruption and reconnect**
   - Ask something that runs tools, for example "Can I afford a ₹40,000 phone in December?".
   - While it is still working, switch off Wi-Fi (or close the tab) for 10 seconds, then reconnect.
   - The answer should resume or show a clear retry; the thread must not duplicate or lose turns.
   - Press stop mid-answer once and confirm the composer becomes usable again.
4. **The agent speaks when a parser run ends**
   - Upload a statement, then close the tab while the card says "Reading your statement".
   - Reopen the chat after the run ends: the agent's own reply sits under the card and says what
     happened. With the tab open, the reply appears without a reload.
   - If it paused, press Continue once. If it failed, it may retry once by itself, never twice.

## 3. Agent eval for schedule payments (costs money)

`arno eval` is the only command that calls real models. Check the case first for free, then
run only that case:

```bash
uv run arno eval --dry-run --case hn_schedule_confirmed_not_paid
uv run arno eval --case hn_schedule_confirmed_not_paid --repeat 3
```

Pass means the agent says a confirmed schedule is not proof of payment. The README scorecard
updates itself; do not edit it by hand. Once memory kinds land, also run the memory case the change
adds (its ID is in `evals/cases.py`, search for "flatmate").

## 4. Decide: link payments from the Schedules page?

Today a payment is linked or unlinked only through chat ("Find a payment", "Ask to unlink" in a
schedule's Details). That keeps writes chat-first with undo. If you want a direct button, ask an
agent:

> In `web/src/panels/SchedulePaymentDetails.tsx`, add a "Link a movement" form: a searchable list of
> recent movements from `/api/movements` (same account as the schedule, outgoing for bills,
> incoming for income), an optional partial amount, and a submit that POSTs
> `/api/schedules/{schedule_id}/occurrences/{date}/payments` with `{movement_id, amount_paise}`.
> Add an "Unlink" button per payment calling the DELETE route. Show the returned undo in a toast.
> Keep the existing chat options. Run `pnpm --dir web lint && pnpm --dir web typecheck`.

## 5. Drop the leftover scratch test databases (optional)

They hold only synthetic test data and are harmless. To remove them:

```bash
docker compose exec -T db sh -c 'for d in arno_payments_test arno_payments_gw0_test arno_payments_gw1_test; do psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS $d WITH (FORCE)"; done'
```

Never drop `arno` (your real data) or anything not ending in `_test`.

## 6. Check that Cash on hand includes every wallet you spend from

Cash on hand adds only accounts marked liquid. In Money → Accounts, compare the Cash on hand card
with the bank, cash, and wallet tiles. If a wallet you spend from is missing, ask in chat: "Mark my
<wallet name> as a liquid account", or check directly:

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d arno -c "SELECT name, kind, liquid, active FROM accounts WHERE kind IN ('"'"'bank'"'"','"'"'cash'"'"','"'"'wallet'"'"') ORDER BY kind, name"'
```

## 7. Still parked

Voice mode (mock `18-voice`) stays parked until you decide to build it.
