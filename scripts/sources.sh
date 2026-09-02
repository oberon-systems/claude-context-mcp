#!/usr/bin/env bash
# List what each project reads: one line per mounted directory.
#
# Reached through `make sources`. The listing comes from `ctxgraph.mounts`, the
# same one `scripts/mounts.sh` writes the compose override from, so what is
# printed here is what would be mounted. Whether a directory still exists is
# checked on the host, for the reason that check lives there: the container
# sees the mounts it was started with, not the tree behind them.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

read -r -a compose <<< "${COMPOSE:-docker compose}"

errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT
if ! listing="$("${compose[@]}" --profile index run --rm -T graphify \
        python -m ctxgraph.mounts 2> "$errors")"; then
    sed 's/^/  /' "$errors" >&2
    echo "Cannot list what the projects read. Is the stack up? Try 'make up'." >&2
    exit 1
fi

# A project mounted whole is one line, project and path. A project reading
# named directories is a header and one indented line per directory, because
# the alias is what its node ids open with and that is what a reader is here
# for.
shown=0
current=""
while IFS=$'\t' read -r name alias root; do
    [ -n "$name" ] || continue
    # The listing comes from inside the graphify container, so an image older
    # than this script prints the two columns it used to and the host path
    # lands in $alias, leaving $root empty. Say so rather than reporting every
    # directory as missing.
    if [ -z "$root" ]; then
        echo "The graphify image is older than this script: its listing has" >&2
        echo "no alias column. Run 'make build' first." >&2
        exit 1
    fi
    # The unnamed source travels as "-": a tab is IFS whitespace, so an empty
    # column would be swallowed by the read above.
    [ "$alias" != "-" ] || alias=""
    if [ -n "${PROJECT_NAME:-}" ] && [ "$name" != "$PROJECT_NAME" ]; then
        continue
    fi
    note=""
    [ -d "$root" ] || note="  (no such directory on this host)"
    if [ -z "$alias" ]; then
        printf '%-24s %s%s\n' "$name" "$root" "$note"
    else
        if [ "$name" != "$current" ]; then
            echo "$name"
        fi
        printf '  %-22s %s%s\n' "$alias" "$root" "$note"
    fi
    current="$name"
    shown=$((shown + 1))
done <<< "$listing"

if [ "$shown" -eq 0 ]; then
    if [ -n "${PROJECT_NAME:-}" ]; then
        echo "$PROJECT_NAME reads no directory yet." >&2
        echo "Add one with 'make source-add PROJECT=<host path>" \
            "PROJECT_NAME=$PROJECT_NAME ALIAS=<alias>'." >&2
    else
        echo "No project reads anything yet." >&2
    fi
fi
