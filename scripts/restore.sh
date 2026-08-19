#!/usr/bin/env bash
# Put a file written by `make backup` back into the database.
#
# Reached through `make restore`. The two formats `backup` writes are restored
# by two different tools, so the file decides: a .dump is a pg_dump custom
# archive and replaces the whole database through pg_restore, a .sql.gz is one
# project and is replayed by psql, replacing that project alone.
#
# Destructive either way, so it follows `unindex` and `clean`: it says what is
# about to be lost and asks, unless FORCE=1.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# `docker compose` is two words, and a plain "$COMPOSE" would be looked up as
# one binary of that name.
read -r -a compose <<< "${COMPOSE:-docker compose}"

# The credentials live in .env, which make does not source: reading them here
# is what keeps this working for anyone who changed them from the defaults.
env_value() {
    sed -n "s/^$1=//p" .env 2> /dev/null | tail -1
}

pg_user="$(env_value POSTGRES_USER)"
pg_db="$(env_value POSTGRES_DB)"
pg_user="${pg_user:-user}"
pg_db="${pg_db:-context}"

# Values reach SQL as psql variables rather than as interpolated text, so a
# quote in a path cannot end up as a statement of its own. The statement is fed
# on stdin rather than through -c, which does not interpolate variables at all.
psql_query() {
    "${compose[@]}" exec -T postgres psql \
        -U "$pg_user" -d "$pg_db" -qtAX -F '|' -f - "$@"
}

backup_dir="${BACKUP_DIR:-}"
backup_dir="${backup_dir:-$HOME/.local/share/context-mcp/backups}"

available() {
    echo "Available in $backup_dir:" >&2
    find "$backup_dir" -maxdepth 1 -type f \
        \( -name '*.dump' -o -name '*.sql.gz' -o -name '*.sql' \) \
        -printf '  %f  %TY-%Tm-%Td %TH:%TM  %s bytes\n' 2> /dev/null \
        | sort >&2 \
        || true
}

file="${BACKUP_FILE:-}"

# `make index` defaults to PROJECT_PATH from .env; this target must not have a
# default either. Guessing "the newest backup" is how a restore replaces a
# database nobody meant to touch.
if [ -z "$file" ]; then
    echo "Nothing named. Pass FILE=/path/to/backup." >&2
    available
    exit 1
fi

# A bare name is looked up where `make backup` writes, so the listing above can
# be pasted back verbatim.
if [ ! -f "$file" ] && [ -f "$backup_dir/$file" ]; then
    file="$backup_dir/$file"
fi

if [ ! -f "$file" ]; then
    echo "No such file: $file" >&2
    available
    exit 1
fi

case "$file" in
    *.dump) mode=database ;;
    *.sql.gz) mode=project; reader=(gzip -cd) ;;
    *.sql) mode=project; reader=(cat) ;;
    *)
        echo "Unknown backup format: $file" >&2
        echo "Expected .dump (whole database) or .sql.gz (one project)." >&2
        exit 1
        ;;
esac

# An index job writes the same rows this is about to replace, and neither side
# would notice the other. Idle mcp-server connections are fine: they hold no
# lock and cannot block the restore.
#
# The service name alone does not say what the container is doing: the `graph`
# MCP server is a long-lived `compose run graphify python -m graphify.serve`,
# and matching on the service would block every restore while a client is
# attached. The indexer is the image's own command, `python -m ctxgraph`, and
# the listing truncates well past the point the two differ.
if "${compose[@]}" ps --status running --format '{{.Service}}|{{.Command}}' \
    2> /dev/null | grep '^graphify|.*ctxgraph' > /dev/null; then
    echo "An index job is running. Wait for it to finish, then retry." >&2
    exit 1
fi

confirm() {
    if [ -z "${FORCE:-}" ]; then
        read -r -p "$1 [y/N] " reply
        case "$reply" in
            [yY]*) ;;
            *) echo "aborted"; exit 1 ;;
        esac
    fi
}

current_projects() {
    psql_query <<< "
        SELECT coalesce(string_agg(
                   p.name || ' (' || c.nodes || ' nodes, ' ||
                   c.plans || ' plans)', ', ' ORDER BY p.name), 'none')
          FROM projects p, LATERAL (
               SELECT (SELECT count(*) FROM graph_nodes g
                        WHERE g.project = p.name) AS nodes,
                      (SELECT count(*) FROM project_plans l
                        WHERE l.project = p.name) AS plans) c"
}

echo "file: $file ($(du -h "$file" | cut -f1), $(date -r "$file" '+%Y-%m-%d %H:%M'))"

if [ "$mode" = database ]; then
    if ! "${compose[@]}" exec -T postgres pg_restore --list < "$file" \
        > /dev/null 2>&1; then
        echo "This is not a readable pg_dump archive." >&2
        exit 1
    fi
    echo "restores: the whole database, replacing everything in it"
    echo "now holds: $(current_projects)"
    confirm "Replace the database?"
    # --exit-on-error is the point of this call: pg_restore treats errors as
    # non-fatal by default and would report success over a half-restored
    # database. --single-transaction then leaves the old one intact on failure.
    "${compose[@]}" exec -T postgres pg_restore -U "$pg_user" -d "$pg_db" \
        --clean --if-exists --no-owner --no-privileges \
        --single-transaction --exit-on-error < "$file"
else
    # `head` closing the pipe kills the reader with SIGPIPE, which pipefail
    # would otherwise report as a failed read.
    header="$("${reader[@]}" "$file" 2> /dev/null | head -20 || true)"
    name="$(sed -n 's/^-- project: //p' <<< "$header" | head -1)"
    root="$(sed -n 's/^-- root_path: //p' <<< "$header" | head -1)"
    if [ -z "$name" ]; then
        echo "This file carries no project header, so it is not one of ours." >&2
        exit 1
    fi
    echo "restores: project \"$name\" ($root)"

    if ! held="$(psql_query -v name="$name" -v root="$root" <<< "
        SELECT coalesce((SELECT 'name' FROM projects WHERE name = :'name'), '')
               || '|' ||
               coalesce((SELECT name FROM projects
                          WHERE root_path = :'root' AND name <> :'name'),
                        '')")"; then
        echo "Cannot reach the database. Is the stack up? Try 'make up'." >&2
        exit 1
    fi
    IFS='|' read -r exists collision <<< "$held"

    # root_path is UNIQUE, so a second project already claiming this path would
    # fail the insert halfway through. Say so before anything is deleted rather
    # than letting the transaction roll back with a constraint name.
    if [ -n "$collision" ]; then
        echo "Project \"$collision\" is already indexed from $root." >&2
        echo "Drop it first: make unindex PROJECT_NAME=$collision" >&2
        exit 1
    fi

    if [ -n "$exists" ]; then
        row="$(psql_query -v name="$name" <<< "
            SELECT (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name),
                   (SELECT count(*) FROM project_plans l WHERE l.project = p.name),
                   (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name
                      AND g.metadata ->> 'summary_source' = 'manual')
              FROM projects p WHERE p.name = :'name'")"
        IFS='|' read -r nodes plans manual <<< "$row"
        echo "replaces: the copy in the database now -" \
            "$nodes nodes, $plans plans, $manual manual summaries"
    else
        echo "replaces: nothing, no project of that name is indexed"
    fi
    confirm "Restore \"$name\"?"
    # The file carries its own BEGIN, the DELETE that cascades the old copy
    # away, and COMMIT, so this is atomic without anything added here.
    "${reader[@]}" "$file" \
        | "${compose[@]}" exec -T postgres psql -U "$pg_user" -d "$pg_db" \
            -qAtX -v ON_ERROR_STOP=1 -f - > /dev/null
fi

echo "restored. database now holds: $(current_projects)"
