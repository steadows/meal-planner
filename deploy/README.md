# Deploying the meal planner with launchd

The planner runs on this Mac as five launchd agents (ADR-0001). The bot answers Telegram all the
time, and four calendar agents run the weekend schedule. Each scheduled run is a short
`python -m meals job <name>` command that is safe to rerun.

| Agent | Runs | Command |
|---|---|---|
| `local.meals.bot` | always; restarted 30 s after it exits | `caffeinate -i uv run python -m meals bot` |
| `local.meals.sat_propose` | Saturday, hourly 08:00–20:00 | `uv run python -m meals job sat_propose` |
| `local.meals.sat_nudge` | Saturday, hourly 16:00–21:00 | `uv run python -m meals job sat_nudge` |
| `local.meals.sun_autoapprove` | Sunday, hourly 08:00–20:00 | `uv run python -m meals job sun_autoapprove` |
| `local.meals.reconcile` | every hour at :15 | `uv run python -m meals job reconcile` |

Every agent also runs once when it's loaded (at install and at login), which catches up on
anything missed while the Mac was off. A run due while the Mac was asleep happens on wake. Each
job decides for itself whether it's in its window, so an extra run does nothing. `cart_fill` isn't
an agent: the bot, `sun_autoapprove` and `reconcile` start it when a week needs its cart filled.

## Before activating

Activation is the last step of the build (plan task P4.8), after the verify and security gates.
Until then, use `--dry-run` only.

- Nothing runs after a reboot until Steve logs in: FileVault rules out automatic login.
- Keep the lid open. A closed lid forces sleep, and `caffeinate` can't prevent it.
- Set macOS updates to download but not install automatically, so a restart doesn't strand the
  weekend.
- `.env` must have the Telegram settings. A job without them exits with a message in its launchd
  log and can't tell you on Telegram.

## Install

```sh
deploy/install.sh --dry-run    # render and lint the five plists; changes nothing
deploy/install.sh --activate   # the real install
```

Run it from a normal Terminal shell, where `uv` and `claude` are on your `PATH`. The install
writes their locations into the plists, because launchd starts agents with a bare `PATH`. It also
checks that the Mac's time zone is America/Detroit: launchd fires on the Mac's clock, and the jobs
judge their windows in Detroit time.

`--dry-run` writes the rendered plists to `data/launchd/`, lints them with `plutil`, and prints
what `--activate` would do. `--activate` first runs `uninstall.sh`, so a rerun waits for running
work just as a code switch does. It then copies the plists to `~/Library/LaunchAgents/` and loads
each one with `launchctl bootstrap gui/<uid>`. Running it twice leaves the same five agents.

**Rerun `--activate` after upgrading Node or uv.** The plists hold the old paths until you do, and
every run fails in the meantime with "job … failed" on Telegram or a line in the launchd log.

## Uninstall, and switching code

```sh
deploy/uninstall.sh
```

It boots out all five agents and deletes their plists, so nothing new starts, then waits:

1. until no `meals bot` or `meals job` process is left. This includes a cart fill the bot started
   a moment before it stopped;
2. until it can take every lock in `data/locks` at one instant. An orphaned `claude --chrome` child
   still filling the cart after its job died keeps its locks, so this step waits for it too.

While it waits, it names each process or lock holder. When it prints **safe to switch code**, you
can change branches or pull, then run `deploy/install.sh --activate` again.

To roll back wiring's own changes, uninstall, revert them, and run jobs by hand with
`uv run python -m meals job <name>` if needed. Database migrations are never reverted.

## Logs

- `data/logs/jobs.log`: every job run, in order. This is the record.
- `data/logs/launchd-<agent>.log`: each agent's stdout and stderr. It holds failures from before a
  job's logging starts, such as a missing `uv` or an import error.

## Stuck run

A job that holds its lock longer than it should sends "`<job>` has been running since …, longer
than it should. It may be stuck". Usually the job or its `claude` child is still at work. Only an
uncatchable death (SIGKILL, power loss) leaves a `claude` child running on its own, holding the
job's lock and, for a cart fill, the Chrome lock.

1. Find the holder:

   ```sh
   lsof data/locks/job-<name>.lock
   ```

2. Look up its process group, then stop the whole group (note the minus sign):

   ```sh
   ps -o pid,pgid,command -p <pid>
   kill -TERM -<pgid>
   ```

3. **Never delete a lock file.** A running process keeps the lock on the file it opened, so a new
   file would let a second run start beside it.
4. The next scheduled tick reports the run as interrupted on Telegram. For `cart_fill`, that's the
   next `reconcile`. Empty the cart in the Meijer app, then reply "retry cart".
