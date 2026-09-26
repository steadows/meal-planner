# Post-PR ultrareview brief: wiring P2, plan state + jobs + the job CLI (GitHub PR #26, steadows/meal-planner)

**Run the `/steadows-ultrareview` skill (`~/.codex/skills/steadows-ultrareview`) against the LOCAL
branch. Do not use GitHub.** GitHub access is not available in this session, and it isn't needed.
The change set is branch `feat/wiring-p2` against `origin/main` in this working tree. HEAD is the PR
head, including this brief's own commit, which is docs only. The PR description is at the bottom of
this file, verbatim.

- **Read the diff from files, not git.** On this Mac `/usr/bin/git` is an xcrun shim that writes a
  cache under `/tmp`, and the sandbox has denied that before. These files are pre-written for you:
  - `.context/reviews/p2-diff.patch`: `git diff origin/main...HEAD` for everything in the PR
  - `.context/reviews/p2-commits.txt`: the commit list
  - `.context/reviews/p2-pr-body.md`: the PR description, also reproduced below

  If `git` works for you, fine, but don't depend on it. Every file named here is in the working
  tree at HEAD, so read it directly.
- **Don't run `uv`, `pytest` or `python` against the repo.** They write caches the sandbox may deny.
  The suite is green (953 passed on Python 3.11; ruff, strict mypy and import-linter clean). Reason
  from the code. Throwaway probes are allowed only under `/tmp` with the system Python and no repo
  imports. **A denied WRITE is a stop-and-report, never a workaround.**
- **Fleet size:** this is the FIRST pass on this PR, so a full fleet is allowed. It is
  **concurrency, process lifecycle and persisted state**:
  - a flock job lock released by close and handed to a child through `hold_fds`
  - SQLite compare-and-set and rowcount-asserted writes across processes
  - SIGTERM and SIGALRM raised as exceptions into a catch-all
  - a claim/redelivery state machine across crashes, Telegram outages and retries

  Per the skill's risk override, size the fleet at the Max tier.
- **REVIEW ONLY:** do not edit, create, commit or push anything in the repo. Don't spawn nested
  sub-fleets beyond the skill's own design.
- **Data boundary (docs/AUTONOMOUS_WORK.md §3):** read only these:
  - `meals/`, `tests/`, `docs/`, `pyproject.toml`, `uv.lock`, `.github/`
  - `CLAUDE.md`, `.context/seams/P2.md`, `.context/reviews/`
  - the installed PTB source under `.venv/lib/python3.11/site-packages/telegram/`

  Never read `.env`, `data/` or `seed/`. Never contact Telegram, Mealie or Meijer.
- **Standards (pre-existing, authoritative):**
  - `docs/adr/ADR-0001-runtime-model.md` (accepted by Steve): Decision :43-80, Jobs :82-113, Job
    contract :114-170, Retries :171-183, Orphaned Chrome child :184-203, Ownership :266-321,
    Integration Test Points :417-444
  - `docs/PLAN.md`, `docs/AUTONOMOUS_WORK.md`
  - `meals/contracts.py`, `meals/db.py` (migration 3), `meals/claude_runner.py` (`run(hold_fds)`)

  The author's own claims (verify them, don't defer to them): every docstring in the diff,
  `.context/seams/P2.md` (the design contract, decisions D1-D22), and the tests.
- **Prior review, and dispositions not to re-raise unless the disposition is wrong:**
  - `.context/reviews/p2-code-review.md` is the full pre-PR report. Every kept finding (#1-#16) and
    every Codex sweep finding (C1-C4) was fixed RED-first. Two were filtered as ADR-prescribed and
    are raised in the PR as design gaps for Steve: a never-importable pick blocking the week's Mealie
    publish, and no slug write-back.
  - `.context/reviews/p2-codex-sweep.md` holds the single-agent sweep's findings.
  - `docs/prompts/wiring-p2-jobs-adversarial-review.md` lists the first-pass findings.
  - The PR's "Decisions for Steve" (custody default, busy messages only on `--retry`, no automatic
    retry for a failed auto-approve, retry cart refusing weeks older than six days) are deliberate
    defaults. Challenge one only if it breaks an ADR rule or loses data.
- **Callers that don't exist yet** (judge the API against them):
  - the bot lane will call `plan_state.get_week`, `transition` and `latest_run`, and spawn
    `cart_fill` (after approval, "retry cart") and `sat_propose --retry` ("resend plan");
  - the cart lane's `cart.fill(cart_list, hold_fds)` will replace `__main__`'s refusing `fill` (P4.1);
  - P3 installs five launchd agents that run these jobs hourly with `RunAtLoad`.
- **Output:** findings with file, line, severity (CRITICAL/HIGH/MEDIUM/LOW), category, evidence
  (cite code; probes only where cheap and sandbox-safe) and a concrete fix. Only report findings
  that survive the skill's verification. Say explicitly if nothing material is found. Also state
  which files you actually read, so it's clear the diff was really reviewed.

---

## PR #26 description (verbatim)

## What this is

This PR adds the scheduled jobs that turn the planner and the bot into a weekly routine. Each is a short, rerun-safe command: `python -m meals job <name>`.
- **`sat_propose`** (Saturday) drafts the week and sends the proposal.
- **`sat_nudge`** sends one reminder if there's no reply.
- **`sun_autoapprove`** (Sunday) reuses last week's plan if there's still no reply, and starts the cart fill.
- **`cart_fill`** fills the Meijer cart for an approved week.
- **`reconcile`** (hourly) puts approved plans on Mealie, and restarts or redelivers anything a crash or an outage dropped.

This is ADR-0001 phase **P2**, and PLAN **Phase 5 / Lane G**. Nothing is scheduled yet: the launchd agents come in P3. `cart_fill` refuses to fill until the cart lane's `cart.fill(hold_fds)` lands (P4.1), which is the ADR's ship gate. There are no new dependencies and no migration; `job_run` came in contracts #21.

## What it adds

**`meals/plan_state.py`** is the only code that reads or writes `weekly_plan` and `job_run`.
- A compare-and-set returns whether it matched. Every other write must change exactly one row, or it rolls back and raises `PlanStateError`.
- Timestamps are SQLite's UTC text, read back as aware datetimes.
- Stored proposals reload with `TRUSTED`, the one place it's passed.
- For the bot: `get_week`, `transition(from, to, proposal=)` and `latest_run(job, outcomes)`, for approval, "ordered" and "retry cart".

**`meals/jobs.py`** holds the five jobs, each following the ADR's job contract:
1. a non-blocking `flock` job lock, released by closing the fd and passed to `fill` through `hold_fds`;
2. Inspect, which settles a dead run's claim first;
3. Decide: half-open America/Detroit windows, and not acting writes nothing;
4. Claim;
5. Act;
6. Finish, and only after the message is delivered;
7. a catch-all for any failure. SIGTERM and a 45-minute alarm go through it too.

A result that has committed is never rewritten. A message that fails to deliver leaves the claim open, and the next tick delivers it.

**`meals/__main__.py`** gives you `python -m meals job <name> [--now ISO] [--week YYYY-MM-DD] [--retry]`, composed from the merged planner, Mealie client and pantry.
- An unknown job name is refused, which closes P1's `spawn_job` allowlist LOW.
- A failure that escapes a job is still logged to `data/logs/jobs.log` and sent to Steve.

**`meals/background.py`** gains the Telegram plumbing the bot and the jobs share: `split_message`, `first_line`, and `TelegramSend` for a job process. `TelegramSend` sends plain text and retries transient errors for at most 2 minutes in total. It never forwards PTB's error text, because `InvalidToken`'s message contains the token.

## Decisions for Steve

None of these block the merge; each is a default you can change.
1. **First-week custody guess:** `wed+sat_sun`. After that, each week starts from the last stored week's custody, and the proposal names the kid nights, so a wrong guess is a one-word fix (PLAN :666).
2. **"Still running" messages** go out only for a `--retry` ("retry cart", "resend plan"). A bot-spawned fill that finds the lock busy stays quiet, and reconcile starts it within about 75 minutes. The ADR also names bot spawns, but the CLI has no way to tell one from reconcile's hourly spawn; a `--notify-busy` flag would be a one-line ADR amendment.
3. **A failed or interrupted Sunday auto-approve** leaves the week `proposed`. Steve is told, and can still approve by replying. The ADR defines no automatic retry for it.
4. **"Retry cart" refuses a week more than six days old,** so an old shopping list can't go into the live cart. The ADR said "the latest week", with no age limit.
5. **ADR gaps the reviews surfaced (not built here):**
   - a recipe Mealie can never import blocks that week's Mealie plan, with only a log line, and ages out after 6 days;
   - imported recipe slugs aren't saved back, so a fallback week re-imports last week's web recipes into Mealie as copies.

## How it was built (AUTONOMOUS_WORK.md gates)

1. **Seams:** `/steadows-seams`, tier **HIGH** (locks, compare-and-set, signal handling, redelivery). Decisions D1–D22 are in the local seam map.
2. **RED:**
   - `test-writer` wrote 189 tests;
   - one `spec-watchdog` pass followed, with verdict fix-then-freeze: 52 of 60 wrong implementations were caught, and 8 repairs closed the survivors, including a ready handshake that makes the two-process claim race real.
3. **GREEN,** then **`/simplify`:** one home for `first_line`, a single status list, and a per-job "effect recorded" table.
4. **`/steadows-code-review`:**
   - `/code-review` plus an observability/dependency/rollback lens gave 16 findings. Each was confidence-scored: 14 kept, 2 filtered out as ADR-prescribed.
   - The Codex single-agent sweep (gpt-6-astra, xhigh) found 4 more.
   - **Every kept finding was fixed, RED-first** (tests by `test-writer`, then the fixes). The highest: a failed cart fill's message now says to empty the cart before "retry cart" (ADR risk #16).
   - Other fixes:
     - the nudge's 3 hours is measured in real time across DST;
     - a proposal planned past Sat 20:00 isn't sent;
     - an `ordered` week's undelivered report is still delivered;
     - a missing MEALIE_TOKEN no longer blocks jobs that don't use Mealie;
     - the Telegram budget covers the sends as well as the waits;
     - reconcile isolates an unreadable week;
     - `job_run` reads survive a future column.
   - Where the rounds landed: 20 product findings, 0 test-infrastructure.

## Test plan

- [x] 953 passed, 7 deselected (integration), on Python 3.11. This includes 218 new P2 tests covering every ADR-0001 Integration Test Point for `plan_state` and `jobs.run_job`, among them the window edges on both 2026 DST weekends, the crash and Telegram-outage redelivery sequences, and a real two-process claim race.
- [x] `ruff check`, `ruff format --check`, strict `mypy` (40 files) and `lint-imports` (2 contracts kept) are all clean.
- [ ] CI green.
- [ ] `/steadows-ultrareview` (Codex fleet), then convergence.
- [ ] `/steadows-verify` phase gate.

**Merge: Steve's call.**

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01P939VSTVLAxVqo9FkZ2zKx

