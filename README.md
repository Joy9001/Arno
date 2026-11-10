<p align="center">
  <img src="web/public/favicon.svg" width="96" height="96" alt="The Arno orb">
</p>

<h1 align="center">Arno</h1>

<p align="center">
  A self-hosted personal finance agent for India. It keeps your accounts, cards, loans, and goals
  in a double-entry ledger on your own machine, and answers questions about your money in chat.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-d4ff3a?style=flat-square"></a>
  <a href="https://github.com/Joy9001/Arno/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Joy9001/Arno/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Version 0.2.0" src="https://img.shields.io/badge/version-0.2.0-2b59ff?style=flat-square">
</p>

<p align="center">
  <img src=".impeccable/mocks/final/shots/02-thread.png" alt="An Arno conversation: the question 'Can I buy a ₹68,000 laptop on the Slice card?' answered with a 'Yes, with a change' affordability card, a 91% floor probability, and a 90-day projection band." width="100%">
  <br>
  <sub>Interface design comp; the figures are illustrative.</sub>
</p>

Arno was called Fin-Agent until November 2026. Older links, issues, and backups use that name.

## What you can ask

Chat in English, Hindi, or Hinglish, by text or voice.

| You say | Arno does |
|---|---|
| "Can I buy a ₹68,000 laptop on the Slice card?" | Simulates 1,000 runs of the coming months and answers yes, yes with a change, or no, with the probability of staying above your floor |
| "Where did October go?" | Breaks the month down by category and links the rows behind each total |
| "If I prepay ₹1 lakh on my loan, how much interest do I save?" | Writes a short Python script over your data, runs it in a sandbox, and shows you the script |
| "Paid ₹480 for chai with the team, Ravi owes me half" | Logs the spend and Ravi's share at once, with undo |
| "Every Sunday, tell me what I spent on food" | Schedules a read-only question that answers into your inbox |
| A photo of a receipt or a UPI screenshot | Turns it into one transaction and keeps the image |

It also imports bank and card statements (PDF, spreadsheet, or text, password-protected or not),
learns each new layout once, and sends reminders for bills, EMIs, and card dues. It never moves
money, never sees a statement password, and never recommends an investment.

## How it works

<p align="center">
  <img src="docs/arno/diagrams/readme-how-it-works.png" alt="How an Arno answer is made: your message goes to the agent, which calls plain-Python tools directly or through Code Mode scripts; the tools read the Postgres ledger and run the projection engine, and every reply passes a number check before it reaches you with verdict cards and citations." width="100%">
</p>

- The model decides what to compute, and code computes it. Every number in a reply has to come
  from a tool return or a script the agent ran; a number check flags any that don't.
- Affordability verdicts come from a seeded projection in plain Python. "Yes" means at least a 90%
  chance your balance stays above your floor, with no goal slipping and no bill missed.
- One Postgres database holds the whole state, on a vendored copy of
  [pgledger](https://github.com/pgr0ss/pgledger). Money is stored in integer paise.
- Text from statements and payee notes is treated as data. After a conversation reads it, any
  lasting change (memory, setup, bulk writes) waits for your tap on a confirm card.

The [system design](docs/arno/system-design.md) and [tool reference](docs/arno/tools.md) go
deeper. Decisions live in [`plan.md`](docs/arno/plan.md).

## Results

These sections are written by `arno eval`, `arno backtest`, and `arno hackerrank`, never by hand.

### Agent evals

134 cases against a synthetic persona, three trials each, with a fresh database per trial.
`pass^3` counts a case only if all three trials pass. The suite and its targets are in
[`evals/`](evals/README.md).

<!-- eval-scorecard:start -->
| Model | Answers and what-ifs | Writes | Honesty when data is missing | Injection | Advice boundary | Never moving money / passwords | Long threads after a summary | Injection gate | Fooled | Number flags | Median cost | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `deepseek-v4.1-flash` | 96% (25/26) | 100% (22/22) | 100% (11/11) | 100% (50/50) | 100% (11/11) | 100% (10/10) | 75% (3/4) | 100% (150/150) PASS | 7% (11/150) | 8 (7 untraced) | — | 18.4s |

`pass^3` is the share of cases that passed in all three trials. The injection gate
is the share of trials where no lasting write landed without its confirm card; the
fooled rate is the share where the model acted on the note even when the gate
stopped it. Percentages use all trials shown in the run.

Run notes:
- deepseek-v4.1-flash: Full 134-case suite, repeat=3, after the release-hardening pass and the Pyrefly strict typing pass.
<!-- eval-scorecard:end -->

### Projection backtest

The projection runs from the first day of each month, and its median is compared with the actual
balance 30 days later. This run uses synthetic history, because a real ledger needs an income
schedule before the backtest will publish its result.

<!-- backtest:start -->
Source: synthetic 12-month history
Months: 12
Median 30-day error: ₹88.67
Median absolute 30-day error: ₹233.74
Actual balance inside the P10-P90 band: 75% of months

| Month start | Actual | Median | P10 | P90 | In band |
|---|---|---|---|---|---|
| 2025-10-01 | ₹4,72,551.58 | ₹4,72,811.82 | ₹4,72,478.56 | ₹4,73,145.68 | yes |
| 2025-11-01 | ₹5,25,711.74 | ₹5,26,037.26 | ₹5,25,698.54 | ₹5,26,351.40 | yes |
| 2025-12-01 | ₹5,79,351.36 | ₹5,79,250.32 | ₹5,78,917.09 | ₹5,79,579.22 | yes |
| 2026-01-01 | ₹6,33,277.65 | ₹6,32,310.62 | ₹6,32,000.05 | ₹6,32,652.50 | no |
| 2026-02-01 | ₹6,71,848.12 | ₹6,71,795.66 | ₹6,71,448.90 | ₹6,72,142.88 | yes |
| 2026-03-01 | ₹7,41,344.10 | ₹7,41,516.62 | ₹7,41,123.74 | ₹7,41,859.30 | yes |
| 2026-04-01 | ₹7,94,873.06 | ₹7,94,915.70 | ₹7,94,510.32 | ₹7,95,292.25 | yes |
| 2026-05-01 | ₹8,48,378.18 | ₹8,48,675.72 | ₹8,48,299.18 | ₹8,49,036.35 | yes |
| 2026-06-01 | ₹9,01,466.74 | ₹9,01,601.44 | ₹9,01,291.52 | ₹9,01,930.10 | yes |
| 2026-07-01 | ₹9,54,365.84 | ₹9,54,962.78 | ₹9,54,619.59 | ₹9,55,317.13 | no |
| 2026-08-01 | ₹10,08,513.93 | ₹10,07,290.74 | ₹10,06,926.46 | ₹10,07,648.05 | no |
| 2026-09-01 | ₹10,62,420.57 | ₹10,62,213.33 | ₹10,61,844.64 | ₹10,62,584.24 | yes |
<!-- backtest:end -->

### HackerRank "Buy or Wait"

The scorer replays the challenge's 25 labeled samples through the engine. The dataset has no
license, so it is never committed.

<!-- hackerrank:start -->
HackerRank sample score: not run. the HackerRank sample dataset is not on this machine; set ARNO_HACKERRANK_DATASET to the dataset folder (it has no license and is never committed).
<!-- hackerrank:end -->

## Install

You need Docker with Compose and an encrypted disk (BitLocker, LUKS, or FileVault), which is what
protects the database at rest. To use Arno from your phone, the host also needs
[Tailscale](https://tailscale.com).

```bash
git clone https://github.com/Joy9001/Arno.git && cd Arno
cp .env.example .env      # fill in the secrets and your chat model; .env.example explains each
docker compose pull && docker compose up -d
docker compose logs api   # prints a one-time enrollment link and a QR code
```

Open <http://localhost:8000>, follow the link, and create a passkey. Tell Arno your balances,
salary day, and card dues, and it can answer from the first conversation. To try it on sample data
first, run `docker compose exec api arno demo seed` on an empty instance.

For phone access, run `tailscale serve --bg 8000` on the host, then set `ARNO_RP_ID` and
`ARNO_RP_ORIGIN` in `.env` to the `ts.net` address it prints.

> [!WARNING]
> Keep `RESTIC_PASSWORD` somewhere other than this machine. The daily backups are encrypted with
> it, and without it they can't be read.

The [self-hosting guide](docs/arno/self-hosting.md) covers the rest: models (cloud or local
Ollama), what each provider receives, ports, backups and restore, updates, and
[upgrading from Fin-Agent](docs/arno/self-hosting.md#66-upgrading-from-fin-agent).

<details>
<summary>The <code>arno</code> command</summary>

Inside Compose, prefix each command with `docker compose exec api`.

```bash
arno migrate                    # apply pending SQL migrations
arno chat                       # chat in the terminal
arno export --format json       # json, csv, or sql
arno setup                      # print a fresh enrollment link
arno passkeys reset             # delete every passkey and session, then reopen setup
arno demo seed                  # load the synthetic persona into an empty instance
arno restore                    # restore the newest backup and verify every table
arno repair-openings --dry-run  # for ledgers that imported statements before 0.2.0
arno eval --model X             # run the agent evals (calls the model, so it costs money)
```

</details>

## Security and privacy

- Compose publishes only on `127.0.0.1`, and Tailscale provides HTTPS. Nothing faces the public
  internet.
- One user signs in with a passkey. Exports and passkey changes ask for the passkey again.
- The agent's SQL runs as a read-only database role with a two-second timeout and no access to
  auth tables.
- There are no analytics. Traces stay on the host in Phoenix. Only the model calls you configure
  leave the machine, and with local models nothing does.

The [security posture](docs/arno/self-hosting.md#7-security-and-privacy-posture) and
[what leaves the box](docs/arno/self-hosting.md#4-what-leaves-the-box) list what each provider
receives and keeps.

## Develop

Python 3.14 with [uv](https://docs.astral.sh/uv/); the web app uses Node 24 with pnpm.

```bash
uv sync --dev
docker compose up -d db                       # Postgres on 127.0.0.1:5433
uv run arno migrate && uv run arno serve
pnpm --dir web install && pnpm --dir web dev  # Vite proxies /api to the API
```

Tests need a scratch database whose name ends in `_test`, and they can never call a model.
[`AGENTS.md`](AGENTS.md) lists the checks for each kind of change, and
`uv tool install pre-commit && pre-commit install` runs them as git hooks.

## Status

0.2.0 is the first release under the Arno name. `plan.md` counts the project as
[shipped](docs/arno/plan.md#what-shipped-means) once someone else installs it from the docs alone
and after four weeks of daily use. The [completion checklist](docs/arno/completion-checklist.md)
tracks what's left. India is the only country implemented; everything India-specific is in
`src/arno/india/`.

## License

Arno is [MIT licensed](LICENSE). CI audits every shipped dependency's license, and no GPL, AGPL,
SSPL, or Elastic code ships in the Arno image; Phoenix runs as a separate Elastic 2.0 service.
The double-entry core is [pgledger](https://github.com/pgr0ss/pgledger) (MIT, © 2025 Paul Gross),
vendored unmodified at commit `fee1aa0`, with ULID helpers from
[scoville/pgsql-ulid](https://github.com/scoville/pgsql-ulid) (BSD-3-Clause, © 2021 Scoville). No
endorsement is implied. Full notices are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
