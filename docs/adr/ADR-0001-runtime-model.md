# ADR-0001: Runtime model — bot for chat, launchd for the schedule

**Date:** 2026-09-26
**Status:** Accepted (Steve, 2026-09-26: `CONFIRM runtime-model v3`, which includes accepting the risks marked accepted in the Risk Matrix: #4, #16, #21, #24–#30)
**Context:** meal-planner / runtime-model architecture gate (PLAN.md → Architecture gates), run by the wiring lane

## Context

The meal planner runs on one always-on home Mac: macOS 26 on Apple Silicon, time zone America/Detroit, FileVault
on, no automatic login. The Mac is kept awake. There is one user, one Telegram chat, and a handful of background
runs a day. Several things can overlap: a voice note mid cart fill, a job re-running after a reboot, `/find` while
the Saturday planner runs. PLAN.md → Runtime concurrency fixes the rules: plan status is the lock, the bot never
blocks, one Chrome session at a time, and at most two Claude processes.

This gate decides how those pieces run: the bot process, "on it … here's your answer" background work (the bot
needs it for `/find` on day one), the Saturday/Sunday schedule, the one-Chrome queue, and safe reruns after a
restart. The contracts lane's `claude_runner` is fixed input:
- It is synchronous.
- It holds one of two cross-process `fcntl.flock` slots per call.
- It passes the slot fd to the `claude` child, so an orphaned child keeps the slot.
- It never retries `chrome=True`.
- It strips `ANTHROPIC_API_KEY`.

Evidence gathered for this decision:
- **launchd.plist(5):** a StartCalendarInterval job due while the Mac sleeps runs on wake, and missed intervals
  coalesce into one run. Apple ("Scheduling Timed Jobs"): a job due while the Mac is powered off doesn't run.
  cron skips both, and Apple calls it deprecated in favour of launchd.
- **Tested on this Mac, 2026-09-25:** a launchd one-shot in the gui domain ran `claude -p --safe-mode` with a
  stripped env. Result "ready", rc 0, 3 s, on Claude Code 2.1.280.
  - anthropics/claude-code#77213 ("Not logged in" under launchd) did not reproduce.
  - #95689 (~143 s per call under launchd, caused by user plugins and hooks) is avoided by `--safe-mode`.
- **python-telegram-bot's JobQueue** (APScheduler 3.x) skips a job whose fire time passed (1 s grace) and does not
  persist jobs.
- **Telegram:** only concurrent `getUpdates` pollers conflict, so a separate process may `sendMessage`. Undelivered
  updates are kept for 24 h.
- **Restarts:** PTB awaits `create_task` work on stop, but launchd SIGKILLs after `ExitTimeOut` (20 s), so long
  work inside the bot dies on a restart.
- **Chrome:** `claude --chrome` needs Chrome running in Steve's GUI session and a `/login` credential. A
  `setup-token` or API key keeps Chrome off. A LaunchAgent (gui domain) reaches Chrome; a LaunchDaemon can't.
- **Reboots:** a LaunchAgent starts only after the user logs in. FileVault rules out automatic login.
- **Lid closed:** closing a laptop lid forces sleep, and `caffeinate` can't prevent it (Apple QA1340).

## Decision

**Option B: the bot handles chat; macOS launchd runs the schedule; every scheduled or long job is its own
short-lived, rerun-safe command.**

- **Bot:** LaunchAgent `local.meals.bot` runs `caffeinate -i uv run python -m meals bot`, with `KeepAlive` and
  `ThrottleInterval 30`, and `PYTHONUNBUFFERED=1` in its environment. Chat-sized background work (`/find`, reply
  parsing) runs inside the bot via `meals/background.py`, with at most 2 requests in flight. A third gets "busy,
  try again in a few minutes" and never starts.
- **Jobs:** `python -m meals job <name> [--now ISO] [--week YYYY-MM-DD] [--retry]` for `sat_propose`, `sat_nudge`,
  `sun_autoapprove`, `cart_fill` and `reconcile`. Four calendar LaunchAgents run the scheduled ones, each with
  `RunAtLoad`, which covers boot and power-off catch-up.
  - `cart_fill` runs only on demand, always for an explicit `--week`. It is spawned detached (own session) by the
    bot after an approving reply, by `sun_autoapprove` after approving, and by `reconcile` for any week that needs
    one.
  - `reconcile` finds outstanding work by persisted week, never by recomputing the week from the clock. It
    publishes pending Mealie plans and restarts or redelivers cart fills.
  - On-demand starts meet PLAN's ~10-minute latency, and `reconcile` picks up anything a crash dropped within
    75 min.
- **State:** `weekly_plan.status` stays the lock. `weekly_plan.components` holds a `WeekProposal` JSON in every
  status:
  - while `proposed`, the full proposal;
  - from `approved` on, the narrowed plan (picked `recipe_options` only, swapped `components`, empty
    `pantry_questions`).

  `plan_state` reloads it with `WeekProposal.model_validate_json(stored, context={contracts.TRUSTED: True})`, the only
  place TRUSTED is passed. It is safe because the planner takes `mealie_slug` only from `mealie.get_recipe()`, never
  from Claude's text (the contracts guard rejects a slug on untrusted input).

  A new `job_run` table (the next contracts migration, numbered at merge) records one claim per (job, week) for
  what status can't carry: nudge once, never refill after a crash, retries, and redelivery. All transitions live
  in `meals/plan_state.py`.
- **Mealie:** the Mealie meal plan is published **after approval**, by `reconcile` (within the hour), never at
  proposal. `mealie_plan_ref IS NULL` on an approved-or-later week is the persisted "publication pending" marker,
  and the compare-and-set that sets the ref is the confirmation. Publication retries hourly, independently of the
  cart, and never re-runs a fill. Each pick resolves as `option.mealie_slug or mealie.import_url(option.url)`,
  with no URL matching. The plan Steve cooks from (PLAN.md:52, :63) is always the approved one, including in a
  fallback week.

### Jobs

| Job | launchd fires (local time) | Window (America/Detroit) | Acts when | Action |
|---|---|---|---|---|
| `sat_propose` | Sat 08:00–20:00 hourly + at load | Sat 08:00–20:00 | no `weekly_plan` row for the week | `planner.propose()` → insert a `proposed` row with the full WeekProposal in `components` → Telegram proposal. No Mealie plan at this point. |
| `sat_nudge` | Sat 16:00–21:00 hourly + at load | Sat 16:00–Sun 08:00 | status `proposed`, and `sat_propose` outcome `done` with `finished_at` ≥ 3 h ago | one nudge |
| `sun_autoapprove` | Sun 08:00–20:00 hourly + at load | Sun 08:00–20:00 | status `proposed`, or no row | see below |
| `cart_fill` | on demand, `--week` required | any time | status `approved` and no `cart_fill` claim for that week | see below |
| `reconcile` | hourly at :15 + at load | — | scans `weekly_plan` rows with `week_start` ≥ today − 6 days | see below |

**`sun_autoapprove`.** Per PLAN, no reply by Sunday means last week's plan is reused.
1. Compare-and-set the `proposed` row to `approved`, with last week's approved `components` re-stamped with
   this week's (Sunday) `week_start`. If there was no row, insert an `approved` one instead.
2. `spawn_job('cart_fill', '--week', W)`.
3. Message Steve. The message says "Saturday's proposal didn't reach you" when `sat_propose`'s outcome isn't
   `done`.

First week (no prior week): claim, message Steve once, and finish `failed` ("no prior week").

**`cart_fill`.** If `week_start` is before today, the fill has expired: claim, send "Week of <date> was approved
but its cart never filled. Reply 'retry cart' if you still want it", and finish `failed` ("expired"). Otherwise:
1. `cart.fill(cart_list, hold_fds=(job_lock_fd,))` takes the Chrome lock, saying "waiting behind another fill"
   if it's busy.
2. In one transaction: set status `cart_filled` and put the `CartReport` JSON into `detail`.
3. Send the report, including "Mealie plan: published" or "pending, retrying hourly".

**`reconcile`.** It holds `job-reconcile.lock` and claims nothing. For each scanned week:
- **Status `approved`, `cart_filled` or `ordered`, with `mealie_plan_ref IS NULL`:** publish the approved
  WeekProposal's recipes, then compare-and-set the ref in (`WHERE mealie_plan_ref IS NULL`, rowcount 1). A
  failure is logged and retried next tick.
- **Status `approved` with no `cart_fill` claim, or an unfinished one:** `spawn_job('cart_fill', '--week', W)`.

### Job contract

`reconcile` runs steps 1, 5 and 7 only.

1. **Lock.** Take a per-job `fcntl.flock` on `data/locks/job-<name>.lock` (the runner's `_slot` primitive; lock
   files are never deleted), non-blocking. If it's busy:
   - a user-initiated run (`--retry`, or bot-spawned) messages "<job> is still running (since <started_at>)";
   - otherwise the job exits quietly, unless the unfinished claim is older than the job's wall-clock limit. Then
     it sends a stale alert (at most once per tick) pointing to `deploy/README.md` → Stuck run.
2. **Inspect.** Read this job's claim for the target week.
   - **Outcome already set:** exit 0 (except `--retry`).
   - **Unfinished while we hold the lock, and its effect is recorded:** the last run ended without finishing, and
     only the message is outstanding. "Recorded" means: for `sat_propose`, the row exists; for
     `sun_autoapprove`, status `approved` or later; for `cart_fill`, `cart_filled` or later. Re-send from persisted
     state, then Finish `done`:
     - the proposal from `components`, only while `proposed` and within Sat 08:00–20:00;
     - the cart report from `detail`;
     - the nudge, only while `proposed` and in its window.

     A proposal outside its window is never sent: finish `failed` ("proposal not delivered").
   - **Unfinished while we hold the lock, and its effect is not recorded:** it died mid-act, or couldn't deliver
     its failure notice. Send the job's notice (with `detail`'s reason if there is one), THEN mark `interrupted`.
3. **Decide.** Check the time window (America/Detroit, `zoneinfo`) and the preconditions. **A job that decides not
   to act writes nothing**, so a later tick can still act. The claim is the last check before acting.
4. **Claim.** `INSERT INTO job_run(job, week_start)`, rowcount 1. A conflict here is a failure. With `--retry`,
   the reset replaces this INSERT (see Retries).
5. **Act.**
   - A status compare-and-set with rowcount 0 means re-read. If the status is already past the target, that's a
     quiet no-op; in any other state, it's a failure.
   - `weekly_plan` inserts use `ON CONFLICT(week_start) DO NOTHING` plus a re-read.
   - A state change and its result commit in one transaction. For `cart_fill`, that's `cart_filled` plus the
     CartReport in `detail`.
   - The job's message is the last step of Act. Sends retry transient Telegram errors (3 attempts, ≤ 2 min).
6. **Finish.** Set `finished_at` and `outcome` (`done`/`failed`), rowcount 1. **No claim is finished before its
   message is delivered.** If a send exhausts its retries, the claim stays unfinished, and the next tick
   redelivers it through Inspect (`cart_fill`'s through `reconcile`).
7. **Always report.** A catch-all handles any failure; the per-job `signal.alarm` (45 min; 20 min for `cart_fill`
   once it holds the Chrome lock) and SIGTERM both go through it.
   - **While the job's effect is NOT recorded:** write the reason to `detail`, send "job <name> failed:
     <reason>", then finish `failed`.
   - **Once the effect IS recorded:** write nothing to `job_run`. Log the reason to `data/logs/jobs.log` and leave
     the claim unfinished for Inspect.
   - If the failure message itself can't be sent, the claim also stays unfinished.

   The runner kills its child's process group on any exception (a contracts fix already in flight, see Ownership), so neither the alarm nor
   SIGTERM can orphan a `claude` child.

**Result preservation.** Once a job's result transaction commits, nothing rewrites that attempt's `detail`: not
the catch-all, a failed send, SIGTERM or the alarm. Later diagnostics go to the log only. A `--retry` reset clears
`detail` only after Decide confirms the job will act. For `cart_fill`, that requires status `approved`, so no
result can be recorded yet.

**Notices:**
- `cart_fill`: "The cart fill stopped partway. The cart may already hold some or all items. Empty it in the Meijer
  app, then reply 'retry cart'."
- `sat_propose`: "Saturday's proposal may not have gone out. Reply 'resend plan'."

### Retries

- **"retry cart"** runs `job cart_fill --week W --retry`, where W is the latest week whose `cart_fill` claim is
  `interrupted` or `failed`.
- **"resend plan"** runs `job sat_propose --retry`. When the row exists, it re-sends the stored proposal (no new
  planner run), and it keeps the Sat 20:00 window.
- **`--retry` rules:**
  - It requires W's claim to be `interrupted` or `failed`, then runs Decide as normal.
  - Only if the job will act does the reset replace Claim: write the prior row to `data/logs/jobs.log`, reset
    `started_at`, and clear `finished_at`, `outcome` and `detail` (rowcount 1).
- **Retrying a cart fill assumes an emptied cart**, which the notice asks for. That holds until the cart-path gate
  decides whether `fill` sets quantities to target.

### Orphaned Chrome child

Raised by the contracts lane; it's the same bug Codex found in the runner. If the `cart_fill` process dies while
its `claude --chrome` child keeps filling, a job lock held only by the job process would be released, and a retry
would double the cart.

- **The fix:** contracts adds `claude_runner.run(..., hold_fds: tuple[int, ...] = ())`, appended to the child's
  `pass_fds`. The job-lock fd crosses the jobs/cart boundary explicitly:
  - `cart.fill(cart_list: CartList, hold_fds: tuple[int, ...] = ()) -> CartReport`
  - `cart.py` calls `claude_runner.run(..., chrome=True, hold_fds=hold_fds + (chrome_lock_fd,))`

  Both locks then live as long as the child.
- **A second runner fix:** contracts also makes `_run_once` kill the child's process group on **any** exception
  from `communicate()`. Today only `TimeoutExpired` does (claude_runner.py:191–195), so the plan's own alarm would
  have orphaned children.
- **The remaining orphan case:** after those two fixes, only an uncatchable death of the job process (SIGKILL,
  power loss) leaves an orphan. The stale-lock alert surfaces it. `deploy/README.md` → Stuck run gives the
  procedure: `lsof data/locks/job-<name>.lock`, then `kill -TERM -<pgid>`, and never delete lock files.
- **`cart_fill` ships only after `hold_fds`, `cart.fill(hold_fds=…)` and the P4.4 lock-retention test are all
  in.**

## Rationale

Option B lets launchd do what it already does reliably: on-time runs, on-wake catch-up after sleep, restarts, and
per-job logs. It also keeps the one long, side-effecting run, the cart fill, out of the bot's process. Each job is
a plain function with `now` injected, testable without a scheduler. The recovery rules (Inspect before Decide, no
finish before delivery, result preservation) are ordering rules inside those functions, not extra machinery. There
is no queue, no outbox table, and no second always-on process.

## Alternatives Considered

| Option | Pros | Cons | Rejected because |
|---|---|---|---|
| A. Everything in the bot (PTB JobQueue + in-bot tasks) | One process; simplest to stand up | JobQueue drops missed jobs and forgets schedules on restart; a bot restart kills an in-flight cart fill | Rebuilds sleep/wake catch-up by hand; ties Saturday to bot health |
| C. Durable SQLite queue + worker process | Nothing lost across restarts; queue visible | Queue, claiming and polling loop; second always-on process; bigger contracts change | Machinery out of proportion to a handful of runs a week |
| cron instead of launchd | Familiar | Skips jobs due while asleep; deprecated on macOS; keychain login fails under cron (third-party logs) | launchd is the macOS scheduler and catches up on wake |
| Outbox table / second alert channel / detached reaper | Stronger delivery and orphan guarantees | More tables and processes | Rejected in debate: Telegram plus the log is enough for one user; the stale-lock alert surfaces orphans |

## Consequences

**Positive**
- Saturday still happens when the bot is down, and a missed-while-asleep job runs on wake.
- A crashed job never takes the bot down, and a bot restart never cuts a cart fill in half.
- A crash can't strand an approved week (`reconcile`), and Mealie always shows the approved plan.
- Messages are at-least-once across crashes, SIGTERM, and Telegram outages that end while the job's ticks still
  run: no claim finishes before its message is delivered. Duplicates are possible.
- Replaces Steve's hand-run keep-awake script.

**Negative / trade-offs**
- **Five plists** (bot, sat_propose, sat_nudge, sun_autoapprove, reconcile). Scheduled jobs are activated only as
  the last step, after the verify and security gates.
- **Rollback takes a set sequence:**
  1. `deploy/uninstall.sh` boots out all five agents, which are the only things that dispatch work.
  2. It waits until `pgrep -f 'meals (bot|job)'` finds nothing, naming each process it's waiting on. This step
     catches a job spawned in the last instant before the bot exited, before that job takes its lock.
  3. It polls until it can take **every lock file in `data/locks` at once**: all `job-*.lock`, the Chrome lock and
     both `claude-slot-*.lock`. Each attempt is non-blocking and releases everything between attempts, so it never
     holds one lock while waiting on another. While waiting, it names each holder (`lsof`).

     Every `claude` child inherits its runner slot fd, and a cart fill's child also inherits the job and Chrome
     locks (`hold_fds`). So an orphaned `claude --chrome` child still mutating the cart after its job process died
     keeps step 3 waiting until it exits. A process check alone would miss it.
  4. Only then does it report "safe to switch code".

  Then revert wiring's own PRs; jobs can still be run by hand. The `job_run` migration is never reverted: migrations are
  append-only (db.py:15), and `get_db()` refuses a database newer than the code (db.py:73–79).
- **A proposal whose window (Sat 20:00) closes during an outage is never sent, by design.** Sunday's message says
  so and reuses last week's plan. Telegram is the only alert channel; `data/logs/jobs.log` is the record.
- **In-bot `/find` work is lost on a bot restart** (the update is already acknowledged), and Steve resends it.
  `background.start` gives up with a message after 15 min. Work abandoned at that deadline may still start one
  Claude run later, because Python threads can't be cancelled. That is bounded by the in-flight cap and the runner
  timeout.
- **Nothing runs after a reboot until Steve logs in** (FileVault, no auto-login). Recommended: set macOS updates to
  download but not install automatically, and keep the lid open.
- **The `claude` binary is under nvm** (`~/.nvm/versions/node/v24.14.1/bin`). `install.sh` bakes the absolute PATH,
  so a Node upgrade breaks runs loudly (the catch-all message) until `install.sh` is rerun.
- **The runner and the schema need changes** from contracts before wiring code: three items gated on this ADR, plus
  one runner fix already in flight (see Ownership).

## Ownership

- **wiring:**
  - `meals/background.py`: `start` with the in-flight cap of 2, and `spawn_job`.
  - `meals/plan_state.py`, `meals/jobs.py`, and `meals/__main__.py` (`bot` and `job` subcommands).
  - `deploy/`: plist templates, `install.sh`, `uninstall.sh`, and `README.md` with a Stuck-run section.

  `background.py` ships **first and early**, because bot depends on it. PLAN.md → Concurrency lanes must record
  that edge (a pm-lane edit).
- **contracts** (one PR, gated on this ADR's confirmation, three items only):
  1. The next migration (numbered at merge order): `job_run` (DDL below). No new columns and no `weekly_plan`
     change.
  2. `hold_fds` on `claude_runner.run`.
  3. Layers contract: `"(__main__)"` > `"(bot) | (jobs) | (mcp_tools) | (seed_loader)"` > `"(pantry) | (mealie_client) | (search)
     | (planner) | (cart) | (background) | (plan_state)"` > `"(db) | (claude_runner) | (rollup)"` >
     `"contracts | (config)"`. `__main__` becomes the composition root; today (pyproject.toml:95) it's an
     independent sibling of bot and jobs, so it can't import either.

  Separately, already under way and not gated on this ADR: `_run_once` kills the child's process group on any
  exception (contracts' follow-up PR), `RecipeOption.mealie_slug` (for search), and Sunday-only `week_start`
  validation. This ADR relies on the first.
- **bot:**
  - approval → `spawn_job('cart_fill', '--week', W)`
  - "retry cart" → `spawn_job('cart_fill', '--week', <latest interrupted/failed week>, '--retry')`
  - "resend plan" → `spawn_job('sat_propose', '--retry')`
  - "2 and 4" is resolved from `weekly_plan.components`
  - the chat-ID allowlist sits in one pre-dispatch handler (PTB `TypeHandler`, group -1, raising
    `ApplicationHandlerStop`), so every handler inherits it
- **search:** `planner.propose()` is pure (no DB, no Telegram), and `recent` is the last 3 cooked weeks. It will
  add `planner.apply_reply(proposal, intents) -> WeekProposal` as a follow-up, after bot's typed Intent args land.
  The approval path depends on it.
- **cart:**
  - `fill(cart_list, hold_fds=()) -> CartReport`, with the Chrome lock inside `cart.py`, passed on via `hold_fds`.
  - Input to the cart-path gate: whether `fill` sets quantities to target (a safe retry over a partial cart), or
    retry keeps requiring an emptied cart.
- **mealie:** implement `set_meal_plan` as replace-the-week, so retries can't duplicate entries. This is consistent
  with the contract text (contracts.py:173–174), so no contracts change.

### `job_run` (next contracts migration, numbered at merge)

```sql
CREATE TABLE job_run (
    job          TEXT NOT NULL,
    week_start   DATE NOT NULL,
    started_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at  DATETIME,
    outcome      TEXT CHECK (outcome IN ('done','failed','interrupted')),  -- NULL while running
    detail       TEXT,       -- the job's result (e.g. CartReport JSON) or its failure reason; never rewritten after a result commits
    PRIMARY KEY (job, week_start)
)
```

`plan_state` public types:
- `transition(...) -> bool` (the compare-and-set result)
- `claim(...) -> Claim`, where `Claim = Literal['claimed', 'finished', 'running', 'interrupted']`
- reads return a frozen pydantic `JobRun` model

## Risk Matrix Summary

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | A time-gated job spends its one claim on a too-early check | CRITICAL → fixed | Decide before claiming; not acting writes nothing |
| 2 | Late wake proposes just before Sunday auto-approve | CRITICAL → fixed | `sat_propose` window closes Sat 20:00 |
| 3 | A job crashes without telling Steve | CRITICAL → fixed | Catch-all + alarm + SIGTERM path; no finish before delivery |
| 4 | Orphaned `claude` holds a slot or lock | HIGH → mitigated | Runner kills its child on any exception; residual (SIGKILL/power loss) surfaced by the stale-lock alert + Stuck-run procedure |
| 5 | Orphaned Chrome child + retry doubles the cart | HIGH → mitigated | `hold_fds`, `cart.fill(hold_fds)`, P4.4 test; `cart_fill` gated on all three |
| 6 | Benign races raise false alerts | HIGH → fixed | CAS false → re-read → quiet no-op |
| 7 | Two jobs insert the same week | HIGH → fixed | `ON CONFLICT DO NOTHING` + re-read |
| 8 | First week: nothing to reuse (and hourly repeats) | HIGH → fixed | Claim, one message, finish `failed` |
| 9 | Layering blocks `__main__` / new modules | MED → fixed | `__main__` its own top layer; new modules in the feature layer |
| 10 | nvm/uv path drift breaks launchd runs | MED | Loud catch-all message; rerun `install.sh` |
| 11 | Mac time zone ≠ America/Detroit | MED | `install.sh` asserts it (today: America/Detroit) |
| 12 | `spawn_job` fails after approval | MED → fixed | Catch `OSError`, message Steve; `reconcile` retries |
| 13 | "retry cart" check-then-act race | MED → fixed | Retry resets under the job's own lock |
| 14 | An unallowlisted chat triggers approve or cart fill | MED | Central chat-ID gate in the bot |
| 15 | Crash between approval and cart dispatch strands the week | HIGH → fixed | `reconcile` scans persisted weeks hourly |
| 16 | Retry over a partly filled cart doubles items | HIGH → mitigated | Retry requires an emptied cart (the notice says so); cart-path gate may switch to set-to-quantity |
| 17 | Crash after the row is written, before the proposal is sent | HIGH → fixed | Inspect before Decide; redeliver from `components` |
| 18 | Mealie stale or missing for the week | HIGH → fixed | Ref-NULL pending marker; `reconcile` publishes hourly |
| 19 | Rollback while a job or an orphaned `claude` child still runs | HIGH → fixed | Boot out all five agents → wait for `meals` processes → take every lock in `data/locks` (inherited by every `claude` child) → switch code |
| 20 | RunAtLoad acts on a pre-acceptance install | HIGH → fixed | Activation is the last P4 step |
| 21 | Bot work piles up while both Claude slots are busy | MED | In-flight cap of 2; abandoned-work residual accepted |
| 22 | Cook-day-evening approval recovered after midnight | MED → fixed | Persisted-week scan + explicit `--week`; expired → one message |
| 23 | Telegram outage or SIGTERM after the result commits | HIGH → fixed | Claim stays unfinished; redelivery without re-acting; result preservation |
| 24 | Duplicate Mealie entries after an ambiguous publish timeout | LOW → fixed | Mealie lane's `set_meal_plan` replaces the week idempotently (PR #11, tested); needs a single publisher per week, which `reconcile`'s job lock gives |
| 25 | Mac powered off through a whole window | accepted | PLAN fallback (reuse last week / manual loop) |
| 26 | Reboot with no login (FileVault) → nothing runs | accepted | Updates set to manual install; log in after restarts |
| 27 | Which Chrome profile an unattended run drives | accepted until the cart spike | The cart spike must prove it |
| 28 | In-bot `/find` lost on a bot restart | accepted | Steve resends |
| 29 | Claude Desktop rotates the shared login (anthropics/claude-code#94464) → auth errors | accepted | Catch-all message; Steve runs `/login` (no `.credentials.json` today, so not currently happening) |
| 30 | No proposal after Sat 20:00 | accepted | Sunday message says so; last week reused |

Checked and ruled out:
- **Shared DB across worktrees:** a relative `PANTRY_DB` is anchored per checkout, and production runs from
  `~/meal-planner` on `main`.
- **Secrets in logs:** `config.py` uses `SecretStr`.
- **A cloud-synced DB path:** `~/meal-planner` isn't in iCloud Desktop or Documents.
- **Sunday auto-approve discarding the fresh proposal:** that is PLAN's stated behaviour.

## Pattern Adherence

| Convention | Status | Notes |
|---|---|---|
| Repository Pattern | ✓ | `plan_state` is the single home for `weekly_plan`/`job_run` access; tests use real schema on tmp_path |
| API Envelope | N/A | No HTTP API |
| Immutability | ✓ | `JobRun` frozen; state changes are persisted CAS; result preservation |
| Simplicity First | ✓ | No queue, outbox, reaper or scheduler library; `doctor` and heartbeat deferred |
| Pydantic at boundaries | ✓ | `JobRun`, `WeekProposal` in `components`, `Claim` literal |
| Silent-write rule | ✓ | rowcount asserted on every `job_run` and `weekly_plan` write |
| Layering / ownership | ✓ | Layer change requested from contracts; no contracts-locked file edited by wiring |

## Debate Log

**Rounds:** 4 of 4 (plan-defender, Claude Opus, vs codex-critic relaying GPT-6 Astra at `--effort xhigh`).
**Outcome:** max rounds reached with no open disagreement. Round 4's one HIGH finding was accepted and fixed; see the
post-debate check below.

**Changes made during the debate**
- **Round 1** (11 findings: 10 HIGH, 1 MED):
  - `reconcile` added to catch approved weeks with no cart fill and to publish Mealie after approval
  - explicit `--week` on `cart_fill`
  - `cart.fill(hold_fds)` interface
  - the runner kills its child on any exception
  - activation moved to the last step
  - `__main__` as composition root
  - in-bot in-flight cap
  - lifecycle acceptance test (P4.4)
  - rejected: a rollback that reverts the `job_run` migration (migrations are append-only)
- **Amendment:** no new `weekly_plan` column and no `attempts` column. `components` already holds the full
  proposal while `proposed`, as the search lane confirmed.
- **Round 2** (4 findings, 7 resolved):
  - no claim finishes before its message is delivered, with redelivery from persisted state
  - `reconcile` keyed on persisted weeks, with an expiry message
  - uninstall drains before switching code
- **Round 3** (1 finding, 3 resolved): result preservation. A committed `CartReport` in `detail` is never rewritten,
  and later diagnostics go to the log.
- **Round 4** (1 finding, everything else resolved): the late-simplified rollback checked processes only and would
  miss an orphaned `claude --chrome` child. Fixed: uninstall also takes every lock in `data/locks`, which every
  `claude` child inherits.

**Post-debate check (2026-09-26):** the lead ran a single targeted GPT-6 Astra review of the round-4 fix at
`--effort xhigh` (single agent, read-only). It checked for deadlock or livelock, lock coverage of every orphan, and
state mutation after the drain. Verdict: **closed, no findings.**

**Rebuttals the critic accepted:**
- no outbox table or second alert channel
- no detached reaper
- no cancellation of abandoned in-bot work
- no migration-aware rollback release
- no automated launchd install in tests

## Integration Test Points

- **`plan_state` against a real-schema tmp DB:** claim, finish, interrupt, retry reset, CAS and insert-conflict
  paths, and two processes racing a claim. **High tier:** `test-writer` + one `spec-watchdog` pass.
- **`jobs.run_job(name, now, …)`:**
  - every window edge (Sat 07:59/08:00/20:00, Sun 07:59/08:00/20:00, and a DST weekend)
  - inspect before decide; not acting writes nothing
  - death after sending the interrupted notice but before marking it → re-sent, then marked
  - Telegram fails through all retries → the claim stays unfinished → the next tick redelivers from persisted
    state, and `cart.fill` isn't called again
  - through the real catch-all after the result commits, both (a) Telegram exhaustion and (b) SIGTERM → the
    CartReport in `detail` is byte-identical afterwards, the next reconcile-spawned run re-sends it, and
    `cart.fill` ran exactly once
  - a catch-all send failure → the next tick sends "interrupted" with the reason
  - a proposal still pending after Sat 20:00 isn't sent, and Sunday's message says so
  - approval Sat 23:50 + crash → `reconcile` Sun 00:15 fills; approval Sun 23:20 + crash → `reconcile` Mon 00:15
    sends one "expired" message and no fill; "retry cart" then fills that week via `--week`
  - Mealie fails on the first `reconcile` and recovers on the second → the approved plan is published, the ref is
    set, and `cart.fill` ran exactly once
  - a fallback week publishes last week's plan
  - `--retry` on an already `cart_filled` week writes nothing
  - busy-lock messages (user-initiated vs stale)
  - the first week sends exactly one message
  - SIGTERM → catch-all
  - a pantry read raising `PantryRowError` (a hand-corrupted row) → the Telegram failure message carries the
    exception text (row id, item name, "re-run the seed loader"), not a generic "plan failed"

  These use fakes for planner, cart, Mealie and Telegram send.
- **`background.start`:**
  - ack → task → render
  - an exception → a one-line reply
  - the 15-min deadline
  - a second update is acknowledged while a first `work` is blocked
  - a request beyond 2 in flight gets "busy", and its work never starts

  These use a fake PTB `Update`/`Context`.
- **`spawn_job`:** the child runs detached in a new session; `OSError` → a message.
- **Plists:** `plutil -lint` on the rendered templates; `install.sh --dry-run`; uninstall waits for `meals
  bot|job` processes, including a spawned job that hasn't taken its lock yet.
- **P4.4 lifecycle acceptance** (real subprocesses, a stub `claude_bin` that sleeps, no launchd, no real
  services):
  - kill -9 a running `job cart_fill` → the job and Chrome locks stay held until the stub exits, and `--retry`
    answers "still running"; afterwards the next run reports `interrupted`
  - a `spawn_job` child survives `killpg` of its spawner's group
  - SIGTERM mid-run → the child is killed and the failure reported
  - uninstall with a job running waits, then reports safe
  - kill -9 a running `job cart_fill` while its stub child still holds the job, Chrome and slot locks → uninstall
    keeps waiting (naming the child) until the stub exits, and only then reports safe
- **M4 end to end (manual, real services, via the CLI with no agents installed):** `job sat_propose --now <Sat
  08:00>` → reply → `cart_fill` into the real Meijer cart. Gated on the cart spike and the contracts items.
