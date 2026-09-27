#!/bin/bash
# Stop the meal planner's launchd agents, then wait until nothing of it is still running or holding
# a lock, so its code can be switched (ADR-0001, Consequences, rollback). See deploy/README.md.
set -euo pipefail

[ $# -eq 0 ] || {
    echo "usage: deploy/uninstall.sh" >&2
    exit 2
}

DEPLOY="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEPLOY")"
DOMAIN="gui/$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"

die() {
    echo "uninstall.sh: $*" >&2
    exit 1
}

uv="$(command -v uv)" || die "uv is not on PATH; run this from a shell where uv is on PATH"

# The agents are the only things that start work, so stop them all before draining. Removing the
# plist keeps the next login from loading it again.
for template in "$DEPLOY"/local.meals.*.plist.template; do
    [ -e "$template" ] || die "no plist templates in $DEPLOY"
    label="$(basename "$template" .plist.template)"
    if launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$label" || die "launchctl bootout $DOMAIN/$label failed"
        echo "stopped $label"
    fi
    rm -f "$AGENTS_DIR/$label.plist"
done

"$uv" run --no-project python "$DEPLOY/drain.py" "$ROOT/data/locks"
echo "safe to switch code"
