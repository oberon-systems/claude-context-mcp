#!/usr/bin/env bash
# Write the database, or one indexed codebase of it, to a file on the host.
#
# Reached through `make backup`. Two modes, and two formats, because pg_dump
# selects by table and never by row while every table here is scoped by a
# `project` column: the whole database is a pg_dump custom archive, and a
# single project is generated plain SQL - COPY blocks in foreign-key order
# wrapped in one transaction, the shape `pg_dump --format=plain` emits, which
# `psql -f` restores.
#
# The dump is redirected on the host rather than written inside the container,
# so the file belongs to the user running make and not to the postgres uid.

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

indexed_projects() {
    local names
    names="$(psql_query <<< \
        "SELECT string_agg(name, ', ' ORDER BY name) FROM projects" || true)"
    echo "Indexed: ${names:-none}." >&2
}

# What one `make index` brings back and what nothing brings back are different
# losses, so they are never added into one number. Same split `unindex` prints,
# for the same reason: it is what tells the reader whether the file matters.
report_project() {
    local row root indexed nodes edges hashes embeddings plans manual
    row="$(psql_query -v name="$1" <<< "
        SELECT p.root_path,
               coalesce(to_char(p.indexed_at, 'YYYY-MM-DD HH24:MI'), 'never'),
               (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name),
               (SELECT count(*) FROM graph_edges e WHERE e.project = p.name),
               (SELECT count(*) FROM file_hashes f WHERE f.project = p.name),
               (SELECT count(*) FROM code_embeddings c WHERE c.project = p.name),
               (SELECT count(*) FROM project_plans l WHERE l.project = p.name),
               (SELECT count(*) FROM graph_nodes g WHERE g.project = p.name
                  AND g.metadata ->> 'summary_source' = 'manual')
          FROM projects p
         WHERE p.name = :'name'")"
    IFS='|' read -r root indexed nodes edges hashes embeddings plans manual \
        <<< "$row"
    echo "project \"$1\" ($root, indexed $indexed)"
    echo "  rebuilt by one 'make index': $nodes nodes, $edges edges," \
        "$hashes file hashes, $embeddings embeddings"
    echo "  not rebuilt, gone for good: $plans plans, $manual manual summaries"
}

if [ -n "${KEEP:-}" ] && ! [[ "$KEEP" =~ ^[1-9][0-9]*$ ]]; then
    echo "KEEP must be a positive number, got \"$KEEP\"." >&2
    exit 1
fi

# Named apart from PROJECT_PATH and PROJECT_NAME, which compose reads out of
# .env for the graphify service: an empty one exported here would reach it as a
# mount of nothing and fail every call in this script.
name="${BACKUP_NAME:-}"
path="${BACKUP_PATH:-}"

if [ -n "$name" ] && [ -n "$path" ]; then
    echo "Pass PROJECT= or PROJECT_NAME=, not both." >&2
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

if [ -n "$name" ]; then
    if ! found="$(psql_query -v name="$name" <<< \
        "SELECT name FROM projects WHERE name = :'name'")"; then
        echo "Cannot reach the database. Is the stack up? Try 'make up'." >&2
        exit 1
    fi
    if [ -z "$found" ]; then
        echo "No project named \"$name\"." >&2
        indexed_projects
        exit 1
    fi
fi

backup_dir="${BACKUP_DIR:-}"
backup_dir="${backup_dir:-$HOME/.local/share/context-mcp/backups}"
stamp="$(date +%Y%m%d-%H%M%S)"
suffix="$([ -n "$name" ] && echo "sql.gz" || echo "dump")"
dest="${BACKUP_FILE:-$backup_dir/${name:-context}-$stamp.$suffix}"

mkdir -p "$(dirname "$dest")"

# Half a dump must never look like a backup, so the file takes its final name
# only after it has been read back.
part="$dest.part"
trap 'rm -f "$part"' EXIT

if [ -z "$name" ]; then
    "${compose[@]}" exec -T postgres pg_dump -U "$pg_user" -d "$pg_db" \
        --format=custom --no-owner --no-privileges > "$part"
    # Owner and privileges are left out so the archive survives a changed
    # POSTGRES_USER, which is otherwise the one difference that makes a
    # restore fail on a machine that is not the one the dump came from.
    if ! entries="$("${compose[@]}" exec -T postgres pg_restore --list \
        < "$part" | grep -vc '^;')" || [ "$entries" -eq 0 ]; then
        echo "The archive does not read back, not keeping it." >&2
        exit 1
    fi
else
    # ON_ERROR_STOP matters more here than usual: without it psql reports
    # success over a file whose middle COPY produced nothing.
    #
    # \qecho writes the literal lines and COPY writes the data, both to the
    # query output stream, so they interleave in order. A backslash inside a
    # single-quoted \qecho argument is an escape, which is why the COPY
    # terminator is written doubled.
    "${compose[@]}" exec -T postgres psql -U "$pg_user" -d "$pg_db" \
        -qAtX -v ON_ERROR_STOP=1 -v name="$name" -f - << 'SQL' \
        | gzip -9 > "$part"
BEGIN ISOLATION LEVEL REPEATABLE READ;

SELECT root_path AS root,
       coalesce(to_char(indexed_at, 'YYYY-MM-DD HH24:MI'), 'never') AS indexed
  FROM projects WHERE name = :'name' \gset

\qecho '-- claude-context-mcp single-project backup'
\qecho '-- project:' :name
\qecho '-- root_path:' :root
\qecho '-- indexed_at:' :indexed
SELECT format('-- created: %s', to_char(now(), 'YYYY-MM-DD HH24:MI:SS'));
\qecho ''
\qecho 'BEGIN;'
SELECT format('DELETE FROM projects WHERE name = %L;', :'name');

\qecho 'COPY projects (name, root_path, indexed_at) FROM stdin;'
COPY (SELECT name, root_path, indexed_at
        FROM projects WHERE name = :'name') TO STDOUT;
\qecho '\\.'

\qecho 'COPY graph_nodes (project, id, name, type, file_path, content,'
\qecho '                  summary, metadata, created_at) FROM stdin;'
COPY (SELECT project, id, name, type, file_path, content, summary, metadata,
             created_at
        FROM graph_nodes WHERE project = :'name') TO STDOUT;
\qecho '\\.'

\qecho 'COPY graph_edges (project, source_id, target_id, relation_type,'
\qecho '                  metadata) FROM stdin;'
COPY (SELECT project, source_id, target_id, relation_type, metadata
        FROM graph_edges WHERE project = :'name') TO STDOUT;
\qecho '\\.'

\qecho 'COPY code_embeddings (project, node_id, content_chunk, embedding,'
\qecho '                      created_at) FROM stdin;'
COPY (SELECT project, node_id, content_chunk, embedding, created_at
        FROM code_embeddings WHERE project = :'name') TO STDOUT;
\qecho '\\.'

\qecho 'COPY project_plans (project, id, title, content, status, metadata,'
\qecho '                    created_at, updated_at) FROM stdin;'
COPY (SELECT project, id, title, content, status, metadata, created_at,
             updated_at
        FROM project_plans WHERE project = :'name') TO STDOUT;
\qecho '\\.'

\qecho 'COPY file_hashes (project, file_path, hash, updated_at) FROM stdin;'
COPY (SELECT project, file_path, hash, updated_at
        FROM file_hashes WHERE project = :'name') TO STDOUT;
\qecho '\\.'

\qecho ''
\qecho 'COMMIT;'
COMMIT;
SQL
    # The serial ids of graph_edges and code_embeddings are left out of the
    # column lists on purpose: nothing references them, so the sequence
    # reassigns on restore and no sequence has to be reset afterwards.
    if ! gzip -t "$part" 2> /dev/null \
        || [ "$(gzip -cd "$part" | tail -1)" != "COMMIT;" ]; then
        echo "The dump is incomplete, not keeping it." >&2
        exit 1
    fi
fi

mv "$part" "$dest"

if [ -n "$name" ]; then
    report_project "$name"
else
    projects="$(psql_query <<< "SELECT name FROM projects ORDER BY name")"
    if [ -z "$projects" ]; then
        echo "The database holds no project."
    else
        while read -r one; do report_project "$one"; done <<< "$projects"
    fi
fi

echo "wrote $dest ($(du -h "$dest" | cut -f1))"

# Rotation applies to the naming scheme this script owns, so an explicit FILE=
# is never pruned and never counts. Each family rotates alone: keeping two
# whole-database archives must not delete a project's only backup.
if [ -n "${KEEP:-}" ] && [ -z "${BACKUP_FILE:-}" ]; then
    # The stamp sorts lexically, so "newest" is the tail of a plain sort.
    mapfile -t family < <(find "$backup_dir" -maxdepth 1 -type f \
        -name "${name:-context}-*.$suffix" | sort)
    if [ "${#family[@]}" -gt "$KEEP" ]; then
        for old in "${family[@]:0:${#family[@]}-KEEP}"; do
            rm -f "$old"
            echo "pruned $old"
        done
    fi
fi
