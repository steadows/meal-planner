# Autonomous Work — meal-planner lane protocol

> **Status: adopted 2026-09-25.** Distilled from the lane protocol Steve uses in other projects, which
> is mostly a record of those projects' own incidents. What carries over is the durable shape: gate
> order, verification standard, stop conditions, and when Codex comes in. Update this header, not
> a separate changelog, as rulings land.

This file governs *how* a lane runs its own build loop day to day. It does not list any lane's
tasks or dependencies. Those live in `docs/PLAN.md` (Implementation plan, Concurrency lanes) and
in each lane's `.brain/presence/<lane>.md`.

---

## 0. Required reading — start every session here

1. **`CLAUDE.md`**: lane rules, code conventions, git.
2. **This file**: the protocol.
3. **`docs/PLAN.md`**: Implementation plan (code layout, shared contracts, definition of done) and
   Concurrency lanes. There is no `ARCHITECTURE.md`. The module map is PLAN's code layout plus
   the `import-linter` layering in `pyproject.toml`, and a change to either is a contracts-lane PR.
4. **`~/.claude/rules/common/development-workflow.md` + `testing.md`**: the gate sequence and the
   TDD risk tier. They are global and apply here unchanged.
5. **Your presence note and any open `.brain/connections/` note touching your surface.** Pull, don't
   duplicate.

---

## 1. How lanes are organized here

One long-lived worktree and branch per lane (`~/meal-planner-<lane>`, `feat/<lane>`). The brain
resolves your lane from the branch name, so don't work on other branches in a lane worktree. There
is no issue tracker: status lives in PLAN.md's checkboxes and your presence note.

**File ownership is in your presence note's `touches`.** The contracts lane alone edits
`meals/contracts.py`, `db.py`, `config.py`, `claude_runner.py` and `meals/fakes/`. Anyone may add
to `pyproject.toml` and `tests/conftest.py`, but only by adding. When a task would touch a file
with no clear owner, ask the lanes that could plausibly own it and wait for an answer. Don't annex
it.

**Coordination:** use live `ListAgents` + `SendMessage` when the peer session is running. When it
isn't, leave a presence or connection note (or `brain dm`). Don't arm a watcher on the DM queue.
The `pm` lane sweeps the brain and relays to Steve.

---

## 2. The loop, per task

1. **Research and reuse first.** Check for an existing helper, library or pattern before writing
   anything new.
2. **Seams pass** (`/steadows-seams`) for any multi-file or new-dependency task. It writes
   `.context/seams/<id>.md` and the task's TDD tier to `.context/tdd-tier`. Skip it only for
   genuinely single-file edits with no new dependency.
3. **TDD, by tier.** High-risk surfaces (concurrency and locking, money, auth and secrets,
   migrations and schema, security) get RED from `test-writer` plus one `spec-watchdog` pass.
   Everywhere else the lane writes RED itself, watches it fail, then implements. RED always comes
   before GREEN. The tier is per task: if any seam in the task is high-risk, the whole task's RED
   goes through the pair, so split the task if you want the rest to stay fast. Here that means
   `db.py`, `claude_runner.py`'s process limit and env handling, the cart's Chrome session, and
   anything touching `.env`.
4. **Verify**: see §4.
5. **`/simplify`**: a reuse, simplification and efficiency pass on the diff. Quality only.
6. **`/steadows-code-review`** before the PR, always in this session, ending with the single-agent
   Codex sweep (§3.3).
7. **Open the PR** once pre-PR review is clean. Run `brain announce "about to PR"` first. A lane
   may open its own PR; **only merging needs Steve's explicit go** (§6).
8. **`/steadows-ultrareview`** after the PR is open (§3.2).
9. **`/steadows-verify`** once at the end of each phase, not per task or per PR.
10. **Update status right away:** tick the PLAN.md checkbox the task closes, and keep your presence
    note's `status`, `phase` and `touches` honest. When your lane merges, set `status: done`; that's
    what unblocks the lanes waiting on you.

---

## 3. Codex — where it comes in

Lanes write their own GREEN. There is no autonomous Codex implementation loop. Codex enters at the
three points below, always through the global dispatch contract
(`~/.claude/rules/common/codex-dispatch.md`: run it yourself via Bash, the model it names,
`--effort xhigh`, **never `ultra`**, and no `--write` for reviews).

**Data boundary: code yes, data no** *(Steve, 2026-09-25)*.
Codex may read `meals/`, `tests/`, `docs/`, `pyproject.toml` and `.github/`. It must not read
`.env` (bot token, chat ID, Mealie token), anything under `data/` (the pantry database), `seed/`
(real purchase history), or real Telegram, Mealie or Meijer content. Describe the shape of the
data, never the data. State the boundary in the dispatch brief itself.

### 3.1 Thought partnership — a question, not a build

For a judgment call with several defensible answers and no documented standard. If the answer
*is* a documented standard, a research agent handles it instead
(`~/.claude/rules/common/research-before-asking.md`).

- **Single agent, no fan-out.** Say so in the prompt, or Codex may spawn its own fleet.
- **Make the prompt self-contained.** Embed the artifacts being judged verbatim when they live
  outside the repo. A summary is not the artifact.
- **Ask it to disagree.** Say that the goal is an independent read, and name who is arguing what.
- **Verify its claims against the artifacts, not just its reasoning.** Diff anything it says it
  preserved.
- **Hangs:** if it's silent for 5 minutes, cancel and re-dispatch with a tighter prompt. Cancel at a
  10-minute ceiling. After 3 strikes on one task, the lane does it directly.

### 3.2 Post-PR ultra-review

`/steadows-ultrareview`, Codex-only, dispatched explicitly after the PR opens. **Only the first
pass on a PR is a full multi-agent fleet.** Every re-check after a fix is a single-agent
convergence pass on one named finding. A second full fleet needs Steve to rule it necessary.

### 3.3 Pre-PR adversarial sweep

This is the single-agent Codex second opinion at the end of `/steadows-code-review`. It is one
agent reading the diff, never a fleet.

---

## 4. Per-task verification standard

- **The five CI commands pass locally under Python 3.11** before a PR opens: `uv sync --locked`,
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`. CI
  (`.github/workflows/ci.yml`) runs the same five and must be green before merging.
- **Coverage: the global 80% bar applies** *(Steve, 2026-09-25)*.
  `/steadows-verify` measures it with `pytest --cov --cov-report=term-missing`,
  which needs `pytest-cov` in the dev group and `[tool.coverage.run] source = ["meals"]` in
  `pyproject.toml`. Scale effort to the surface's risk, and say so if you're deliberately not
  chasing a number on a file.
- **Real services stay out of unit tests.** Mealie, Telegram, Chrome/Meijer and `claude -p` run only
  in `@pytest.mark.integration` tests, which are excluded by default and must skip cleanly without
  credentials. Unit tests run against `meals/fakes/`.
- **A test that exercises a write must assert the write changed something**: rows affected > 0, or
  the row read back. A statement that matches zero rows succeeds silently.
- **Every verification claim cites the test that proves it**, not the symbol that implements it.
- **A live end-to-end check catches what unit tests can't.** For anything with more than one exit
  path (the Saturday proposal, the reply parser, the cart report), run the real flow before calling
  it done.

---

## 5. Stop conditions — pause and surface, don't push through

- The task needs a change to the shared contracts, the module layering, or another lane's files.
  Message the owning lane (the contracts lane for contracts) and wait.
- A genuine design decision with no obvious answer. Research first; use §3.1 if it's judgment
  rather than a documented standard.
- `/steadows-verify` fails 3 times on one task.
- The task needs credentials or infra nobody has set up yet (Mealie, Tailscale, the Chrome
  profile). Flag it to `pm` and wait.

**A review round landing mostly in test-infrastructure findings is a signal to narrow the
instrument, not to stop the loop.** Keep going, and change what the next fix targets (see
`testing.md`'s proportionality section).

---

## 6. Boundaries

- **Merging is Steve's, always, explicitly and separately from any other approval.** A lane stops
  at a clean, mergeable PR. Never use `gh pr merge --admin` to get past a failing check.
- **The cart lane stops at a filled cart.** Claude in Chrome must never complete a purchase, and
  must never open a non-meijer.com URL in the logged-in session. That's the prompt-injection
  boundary in PLAN.md → Risks, edge cases and costs.
- **Secrets never leave `.env`.** Secret scanning with push protection is on for this public repo,
  but treat it as a backstop, not the control.
- **Everything runs on the home machine.** No cloud deployment pipeline exists. If that changes,
  this section needs a rewrite, not a patch.

---

## 7. Research and judgment calls — don't ask cold

A technical question with 2+ defensible answers that depends on an external standard gets a
research agent *before* it reaches Steve. A judgment call with no documented standard goes to
§3.1. Never bring either to Steve cold with option cards and pros and cons; that's the tell that
research should have run first. Present the evidence and a recommendation, then ask.
