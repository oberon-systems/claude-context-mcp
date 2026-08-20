---
layout: default
title: Deployment
nav_order: 4
---

## Deployment

All services are defined in `docker-compose.yaml`, run as a **singleton
stack**: one database container serves every indexed codebase. The compose
project name is pinned (`name: claude-context-mcp`) and the data directory
is fixed at `~/.local/share/context-mcp/db` - neither is configurable, on
purpose. Running a second stack from this compose file corrupts the data
directory; there is no per-stack path to keep two apart.

## Configuration

Copy `.env.example` to `.env`. `make up` and `make index` refuse to run
without it.

| Variable            | Default   | Purpose                                                                                         |
| ------------------- | --------- | ----------------------------------------------------------------------------------------------- |
| `PROJECT_PATH`      | required  | Absolute host path of the codebase to index, mounted read-only at `/project`                    |
| `PROJECT_NAME`      | derived   | Name the codebase is stored and addressed under; defaults to the last segment of `PROJECT_PATH` |
| `POSTGRES_PASSWORD` | required  | Database password; compose fails fast when unset                                                |
| `POSTGRES_USER`     | `user`    | Database user                                                                                   |
| `POSTGRES_DB`       | `context` | Database name                                                                                   |
| `MCP_PORT`          | `3000`    | Host port the MCP server is published on                                                        |
| `VIEWER_PORT`       | `3001`    | Host port the graph page is published on                                                        |
| `WEB_PORT`          | `3002`    | Host port the dashboard is published on, bound to loopback                                      |
| `TAG`               | `latest`  | Tag applied to images built by `make build`; compose only ever runs `:latest`                   |

`PROJECT_PATH`/`PROJECT_NAME` are arguments to the indexing job only - they
decide what gets mounted and under which name, and never reach the compose
project or the containers themselves.

Two optional variables, read by the MCP server, implement DNS rebinding
protection (MCP SDK 0.6 has none of its own - GHSA-w48q-cv73-mx4w):

| Variable          | Default                  | Purpose                                                              |
| ----------------- | ------------------------ | -------------------------------------------------------------------- |
| `ALLOWED_HOSTS`   | loopback names on `PORT` | Comma-separated `Host` header allowlist, or `*` to disable the check |
| `ALLOWED_ORIGINS` | empty                    | Comma-separated `Origin` header allowlist                            |

## Backup and restore

```bash
make backup                                  # the whole database
make backup PROJECT_NAME=api                 # one codebase
make backup PROJECT=/home/you/work/api       # the same, resolved by root path
make backup KEEP=20                          # keep more than the seven default
make backup KEEP=                            # keep everything, prune nothing
```

Files land in `~/.local/share/context-mcp/backups`. A whole-database backup
is a `pg_dump` custom archive (`context-<timestamp>.dump`); a single-project
backup is a plain-SQL replay of that project's rows in foreign-key order
(`<name>-<timestamp>.sql.gz`), since `pg_dump` can't select by row. Neither
file gets its final name until it's been read back, so an interrupted dump
is never mistaken for a real backup.

Backups rotate independently per kind, keeping the newest `KEEP` (7 by
default); `FILE=<path>` writes elsewhere and is never pruned.

```bash
make restore FILE=context-20260819-115420.dump   # replaces the whole database
make restore FILE=api-20260819-115137.sql.gz     # replaces that one project
make restore                                     # lists what's in the backup dir
```

Both directions print what's about to be replaced and ask first
(`FORCE=1` skips the prompt), and both run inside a single transaction so a
failed restore can't leave a half-restored database.

Worth doing before `make unindex` and before `make clean`: nodes, edges,
hashes and embeddings come back with one `make index`, but plans, manually
written summaries and everything in `_memory` don't come back from anywhere
else.

## GitHub Pages Setup

This documentation is published via GitHub Pages.

- **Enable Pages:** repository **Settings** -> **Pages** -> set
  **Build and deployment** -> **Source** to "GitHub Actions".
- **Automatic build:** `.github/workflows/pages.yml` builds and deploys
  `/docs` on every push to `main`.
- After the first push, the site is live at
  `https://<owner>.github.io/<repo>/` within a few minutes.
