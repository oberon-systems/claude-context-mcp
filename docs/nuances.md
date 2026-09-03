---
layout: default
title: System Nuances
nav_order: 6
---

## Database schema

The schema is owned by [goose](https://github.com/pressly/goose), which
keeps `migrations/` and the `schema_migrations` table in step. The
`migrate` service runs to completion before anything else reaches the
database, so `make up` applies whatever is pending on its own.

```bash
make db version        # what's applied, what's pending
make db migrate         # apply now
make db new NAME=<slug> # write the next numbered migration file
```

Two things a migration cannot do for you: `scripts/backup.sh` and
`scripts/restore.sh` name every column of every table explicitly, so a
migration that adds or renames one has to update them in the same commit;
and a database restored from a backup taken before a migration comes back
without its `schema_migrations` rows - the next `make up` re-applies from
the beginning, which is safe because the migrations are idempotent.

Core tables:

- `graph_nodes` - one row per file, per code entity (`file_path::name`) and
  per unresolved external import or symbol
- `graph_edges` - typed relations between nodes, unique per
  `(source, target, relation)`
- `code_embeddings` - `vector(1536)` chunks with an HNSW cosine index, not
  populated yet
- `project_settings` - one row per level: the two selection documents as
  columns, and everything else as one `settings` JSONB. The indexing schedule
  is the key `indexing`, holding `mode`, `interval_minutes` and
  `debounce_minutes`, any of which may be absent - that is the level
  inheriting it from the one above. A knob added later is another key rather
  than another migration
- `index_jobs` - one row per index run, with a partial unique index that
  refuses a second run of a project while the first is going. The row is
  opened at start rather than queued, so a run interrupted by a restart is
  closed as failed when the worker API comes back up
  Plans, memories and suggestions have no table of their own: they are
  `graph_nodes` rows under the built-in projects `_plans`, `_memory` and
  `_suggestions`, created by a migration rather than by an index run. That is
  why they are searchable like any other node, and why a suggestion's status
  and hit count - or a plan's status and the project it is about - live in the
  node's `metadata` rather than in columns.

A plan id is stored as the node id unchanged. It names a topic and is written
by hand, so it is already unique across the database and needs none of the
`<about>/<id>` scoping a memory id gets.

Dropping a project cascades from the `projects` row, so one `DELETE` there takes
that project's nodes, edges, hashes and embeddings, and nothing of any other
project. Plans are not derived and have no foreign key, so they stay. So do
memories and suggestions about that project: they sit under a built-in
project the drop does not touch, and go on naming a codebase that is gone -
the drop report counts them for exactly that reason. The
data directory is a bind mount, not a volume - `docker compose down -v`
does not remove it, and a regenerated `POSTGRES_PASSWORD` needs
`make clean` to take effect, since the entrypoint skips initialisation
while the directory already holds a database.

## Read-Only Access

The system mounts all indexed codebases as read-only (`:ro`). Containers **never** mutate your source files.

## Pure ASCII Policy

To ensure compatibility across all environments and tools, all documentation, comments, commit messages, and source code must use only ASCII characters. Unicode symbols and emojis are prohibited.

## Database Integrity

- **One Database:** A single database holds the graph of every codebase you index.
- **Corruption Risk:** Running multiple database containers over the same data directory will cause irreversible corruption. Do not attempt to run parallel stacks from this repository.
- **Persistence:** `docker compose down` does not remove the database volume. To completely reset the index, use `make clean`.

## MCP and Security

- **DNS Rebinding:** The server implements basic DNS rebinding protection via `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` environment variables.
- **Trust:** When registering the MCP server for Gemini, `trust: true` is recommended only for codebases you own.
- **Permissions:** The Claude counterpart of that flag is the `mcp__context` allow rule `make install` writes to `~/.claude/settings.json`. It covers every tool of the server except the four `drop_*` ones, which stay behind a prompt; `PERMISSIONS=0` skips it entirely.
