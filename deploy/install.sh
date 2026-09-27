#!/bin/bash
# Render the meal planner's launchd agents and, with --activate, install them (ADR-0001 P3).
# --dry-run renders and lints only, and is the only mode used before P4.8. See deploy/README.md.
set -euo pipefail

usage() {
    echo "usage: deploy/install.sh --dry-run | --activate" >&2
    exit 2
}
[ $# -eq 1 ] || usage
case "$1" in
    --dry-run) activate=0 ;;
    --activate) activate=1 ;;
    *) usage ;;
esac

DEPLOY="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEPLOY")"
DOMAIN="gui/$(id -u)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
STAGE="$ROOT/data/launchd"

die() {
    echo "install.sh: $*" >&2
    exit 1
}

# launchd fires on the Mac's own time zone, and the jobs judge their windows in America/Detroit.
zone="$(readlink "${MEALS_LOCALTIME:-/etc/localtime}" || true)"
case "$zone" in
    */America/Detroit) ;;
    *) die "this Mac's time zone is ${zone#*/zoneinfo/}, not America/Detroit; the schedule would fire at the wrong hours" ;;
esac

# launchd starts agents with a bare PATH, so bake in where uv and claude (under nvm) live now.
uv="$(command -v uv)" || die "uv is not on PATH; run this from a shell where uv is on PATH"
claude="$(command -v claude)" || die "claude is not on PATH; run this from a shell where claude is on PATH"
path="$(dirname "$uv"):$(dirname "$claude"):/usr/bin:/bin:/usr/sbin:/sbin"
for value in "$ROOT" "$uv" "$path"; do
    case "$value" in
        *[\&\<\>\|\\]* | *$'\n'*) die "unsupported character in path: $value" ;;
    esac
done

mkdir -p "$ROOT/data/logs" "$ROOT/data/locks" "$STAGE"
labels=()
for template in "$DEPLOY"/local.meals.*.plist.template; do
    [ -e "$template" ] || die "no plist templates in $DEPLOY"
    label="$(basename "$template" .plist.template)"
    plist="$STAGE/$label.plist"
    sed -e "s|@@ROOT@@|$ROOT|g" -e "s|@@UV@@|$uv|g" -e "s|@@PATH@@|$path|g" "$template" >"$plist"
    ! grep -q '@@' "$plist" || die "$plist still has a placeholder"
    plutil -lint "$plist" >/dev/null || die "$plist failed plutil -lint"
    labels+=("$label")
done

if [ "$activate" -eq 0 ]; then
    echo "dry run: rendered and linted ${#labels[@]} agents in $STAGE"
    echo "would run: $DEPLOY/uninstall.sh"
    for label in "${labels[@]}"; do
        echo "would copy $STAGE/$label.plist to $AGENTS_DIR/"
        echo "would run: launchctl bootstrap $DOMAIN $AGENTS_DIR/$label.plist"
    done
    exit 0
fi

# Stop and drain whatever an earlier install left running, so a rerun is as safe as a code switch.
"$DEPLOY/uninstall.sh"
mkdir -p "$AGENTS_DIR"
for label in "${labels[@]}"; do
    cp "$STAGE/$label.plist" "$AGENTS_DIR/$label.plist"
    launchctl bootstrap "$DOMAIN" "$AGENTS_DIR/$label.plist" || die "launchctl bootstrap $label failed"
    echo "installed $label"
done
