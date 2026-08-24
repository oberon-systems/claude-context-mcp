#!/usr/bin/env bash
# Remove one indexed codebase from the database.
#
# Reached through `make unindex`, which passes the selectors in the environment.
# Everything derived hangs off the row in `projects`: graph_nodes and
# file_hashes cascade from it, graph_edges and code_embeddings cascade from
# graph_nodes, so one DELETE takes the whole graph of one project and nothing
# of any other. Plans are not derived and have no foreign key: they carry the
# project as a tag and stay behind.

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

# Values reach SQL as psql variables rather than as interpolated text, so a
# quote in a path cannot end up as a statement of its own. The statement is fed
# on stdin rather than through -c, which does not interpolate variables at all.
psql_query() {
    "${compose[@]}" exec -T postgres psql \
        -U "${pg_user:-user}" -d "${pg_db:-context}" -qtAX -F '|' -f - "$@"
}

indexed_projects() {
    local names
    names="$(psql_query <<< \
        "SELECT string_agg(name, ', ' ORDER BY name) FROM projects" || true)"
    echo "Indexed: ${names:-none}." >&2
}

# Named apart from PROJECT_PATH and PROJECT_NAME, which compose reads out of
# .env for the graphify service: an empty one exported here would reach it as a
# mount of nothing and fail every call in this script.
name="${UNINDEX_NAME:-}"
path="${UNINDEX_PATH:-}"

if [ -n "$name" ] && [ -n "$path" ]; then
    echo "Pass PROJECT= or PROJECT_NAME=, not both." >&2
    exit 1
fi

# `make index` defaults to PROJECT_PATH from .env; this target must not. A bare
# invocation that deletes something is the accident worth designing out.
if [ -z "$name" ] && [ -z "$path" ]; then
    echo "Nothing named. Pass PROJECT=/path/to/tree or PROJECT_NAME=<name>." >&2
    indexed_projects
    exit 1
fi

if [ -n "$path" ]; then
    # root_path is UNIQUE, so resolving through it is exact - and it avoids
    # reimplementing the name derivation the indexer does in Python.
    if ! name="$(psql_query -v path="$path" <<< \
        "SELECT name FROM projects WHERE root_path = :'path'")"; then
        echo "Cannot reach the database. Is the stack up? Try 'make up'." >&2
        exit 1
    fi
    if [ -z "$name" ]; then
        echo "No project is indexed from $path." >&2
        indexed_projects
        exit 1
    fi
fi

if ! report="$(psql_query -v name="$name" <<< "
    SELECT p.root_path,
           coalesce(to_char(p.indexed_at, 'YYYY-MM-DD HH24:MI'), 'never'),
           (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name),
           (SELECT count(*) FROM graph_edges e WHERE e.project = p.name),
           (SELECT count(*) FROM file_hashes f WHERE f.project = p.name),
           (SELECT count(*) FROM code_embeddings c WHERE c.project = p.name),
           (SELECT count(*) FROM graph_nodes g WHERE g.project = '_plans'
              AND g.metadata ->> 'about' = p.name),
           (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name
              AND g.metadata ->> 'summary_source' = 'manual')
      FROM projects p
     WHERE p.name = :'name'")"; then
    echo "Cannot reach the database. Is the stack up? Try 'make up'." >&2
    exit 1
fi

if [ -z "$report" ]; then
    echo "No project named \"$name\"." >&2
    indexed_projects
    exit 1
fi

IFS='|' read -r root indexed nodes edges hashes embeddings plans manual \
    <<< "$report"

# What one `make index` brings back, what nothing brings back, and what the
# drop does not touch are three different fates, never one number.
echo "project \"$name\" ($root, indexed $indexed)"
echo "  rebuilt by one 'make index': $nodes nodes, $edges edges," \
    "$hashes file hashes, $embeddings embeddings"
echo "  not rebuilt, gone for good: $manual manual summaries"
echo "  kept, tagged with the name: $plans plans, still readable with" \
    "get_plans project: \"$name\""

if [ -z "${FORCE:-}" ]; then
    read -r -p "Drop it? [y/N] " reply
    case "$reply" in
        [yY]*) ;;
        *) echo "aborted"; exit 1 ;;
    esac
fi

psql_query -v name="$name" > /dev/null \
    <<< "DELETE FROM projects WHERE name = :'name'"

echo "dropped \"$name\""
