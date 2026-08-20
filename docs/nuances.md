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
- `plans` - one row per plan, keyed on an id unique across the database

`make unindex` cascades from the `projects` row, so one `DELETE` there takes
that project's nodes, edges, hashes and embeddings, and nothing of any other
project. Plans are not derived and have no foreign key, so they stay. The
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
