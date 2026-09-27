#!/bin/bash
# Stop the meal planner's launchd agents, then wait until nothing of it is still running or holding
# a lock, so its code can be switched (ADR-0001, Consequences, rollback). See deploy/README.md.
set -euo pipefail

[ $# -eq 0 ] || {
    echo "usage: deploy/uninstall.sh" >&2
    exit 2
}

DEPLOY="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(dirname "$DEPLOY")"
AGENTS_DIR="$HOME/Library/LaunchAgents"

die() {
    echo "uninstall.sh: $*" >&2
    exit 1
}

# The agents run from the main checkout (ADR-0001). Their labels are shared by every checkout, so from a
# linked worktree this would stop production's agents and then drain the worktree's own locks.
[ ! -f "$ROOT/.git" ] || die "$ROOT is a linked git worktree; run deploy/ from the main checkout, where the agents run"
uid="$(id -u)"
[ "$uid" -ne 0 ] || die "don't run this as root: the agents live in your own gui domain, not gui/0"
DOMAIN="gui/$uid"
uv="$(type -P uv)" || die "uv is not on PATH; run this from a shell where uv is on PATH"

# The agents are the only things that start work, so stop them all before draining. Removing the
# plist keeps the next login from loading it again. `launchctl print` exits 113 for a service that
# isn't loaded; any other failure means we can't tell, so stop rather than report safe.
for template in "$DEPLOY"/local.meals.*.plist.template; do
    [ -e "$template" ] || die "no plist templates in $DEPLOY"
    label="$(basename "$template" .plist.template)"
    if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$label" || die "launchctl bootout $DOMAIN/$label failed"
        echo "stopped $label"
    else
        status=$?
        [ "$status" -eq 113 ] || die "launchctl print $DOMAIN/$label failed (exit $status)"
        echo "$label was not loaded"
    fi
    rm -f "$AGENTS_DIR/$label.plist"
done

"$uv" run --no-project python "$DEPLOY/drain.py" "$ROOT/data/locks"
echo "safe to switch code"
