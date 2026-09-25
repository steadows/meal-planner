# DM Protocol — the full reference (on-demand)

The always-read rules live in the `navigation-standards` skill; this file is the detail you pull
when you're actually in a DM exchange. Read it before your first dialog, not every session.
The **absolute engine path printed in SessionStart** is the canonical launcher; substitute it for
`<brain>` in every command below, never a sibling worktree's tracked `.brain/bin/brain`.

## The three tiers — division of labour

| Tier | Mechanism | Reaches | Job |
|---|---|---|---|
| **Fast** | `<brain> dm` → per-message queue, watched live | a **running** lane, in seconds | the nudge |
| **Journal heads-up** | `<brain> announce` → journal | explicitly mentioned lanes, from today's journal at boot | the heads-up |
| **Permanent** | `connections/` note | anyone reading the vault, forever | the **record** |

A DM is a signal, never the record — a decision that exists only in an inbox did not happen.
For "main moved", **git is the real source of truth** (every lane rebases at start); the broadcast
is a courtesy that saves a lane from finding out mid-gate. Do not build correctness on it.

`<brain> inbox` prints your `pending/` directory. Watch that directory for activity; when it changes,
run `<brain> dm take` to print and archive up to 40 messages. Repeat while `pending/` still contains
messages; leftovers stay queued and are never discarded.

**Delivery is at-least-once, never-lost.** A crash at the wrong instant can replay a message at the
next boot — that is the deliberate trade: a duplicate costs a re-read, a loss breaks the feature's
only promise. Every digest line carries the message `id`; before re-acting on a repeat, treat side
effects as idempotent or check the durable record (git, presence notes, `connections/`) — a decision
that exists only in an inbox did not happen anyway.

## The six shapes (measured from a live vault, not invented)

status broadcast · intake/handoff · heads-up/warning · unblock (`waiting-on`) ·
merge coordination · dialog

## Rules of engagement — triage by cost, not authority

1. Read everything. **Ack everything, even when deferring** — an unacked message is
   indistinguishable from a lost one.
2. Do it now if small, **or if it invalidates your current work** (the "main moved" case).
3. Otherwise finish your current task first.
4. **Write it down when you defer** — punch list or connection note, never only in context,
   which dies at compaction.

## Merge coordination — the handshake

The one shape where **silence means NO agreement**. An unacked "hold your merge until mine
lands" is *not agreed*. DM = the signal; the existing `waiting-on` connection note = the record.

## Dialog mode — two lanes converging on a technical call

- Both ends opt in. Set `dialog_with: <lane>` in your presence note while engaged; clear it after.
  `<brain> status` surfaces open dialogs, so a conversation still open an hour later is visible.
- **Push toward convergence; do not go forever** — converge / deadlock / escalate.
- Convergence **is** writing the shared conclusion to a `connections/` note.

### Anti-sycophancy — and what actually enforces it

Be honest about which of these is machinery and which is compliance: the **convergence artifact**
and the **go-get-evidence step** are structural; "cite evidence" and "critique first" are rules
nothing enforces. Lanes are technical experts in their own surface and *critical but helpful
consultants*:

- You have standing to say "that breaks X" about **your** surface — that is why you were asked.
- **Cite evidence** — file, line, presence note, prior decision. An assertion with no referent
  doesn't count, the same bar lanes already apply to inbound claims.
- The responding lane's **first turn must be a critique, not an endorsement** — but never a
  fabricated one: on a sound proposal, say "nothing contested" explicitly.
- The convergence note records **what was contested and the tradeoff accepted** — and when nothing
  was contested, it says so explicitly. Open dialogs are visible in `<brain> status` (via
  `dialog_with:`); the *nothing contested* tell is read in the PM's periodic vault sweep — v1 has
  **no** automatic surfacing of convergence notes, so do not rely on one.

### Research first — never assert or ask cold

A mid-dialog question with multiple defensible answers that depends on an external standard →
**dispatch a research agent before taking a position**. Bring file paths / issue numbers / doc
URLs, not opinions. Check the vault's `research/` notes first — it may already be answered; drop a
self-sufficient note when it isn't. *This is the other half of anti-sycophancy: two lanes trading
unevidenced opinions converge on whoever is more confident.*

**Name the split:** *research finds what others do; thought partnership stress-tests what we
should do.* Research when there's a documented answer; thought partnership when it's judgment.

### Deadlock protocol — go get evidence, then reconvene

When two lanes are stuck, they **agree to go do research and/or thought partnership**, **share
what they produce** (documents, reviews, research write-ups), **discuss it**, and **converge**.
That is the entire procedure — a protocol *between the two lanes*, not an escalation to a referee
and not an escalation to the human.

## Project hooks

<!-- Deployed copies append project-specific referents here (e.g. the project's dispatch-discipline
     doc, its research-note corpus conventions). The generic template stays project-agnostic. -->
