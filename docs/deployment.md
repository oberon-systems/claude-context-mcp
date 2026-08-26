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

Copy `.env.example` to `.env`. `make up` and `make install` refuse to run
without it.

| Variable            | Default   | Purpose                                                                                         |
| ------------------- | --------- | ----------------------------------------------------------------------------------------------- |
| `PROJECT_PATH`      | required  | Absolute host path of the codebase to index; the default tree, mounted like every other one     |
| `PROJECT_NAME`      | derived   | Name the codebase is stored and addressed under; defaults to the last segment of `PROJECT_PATH` |
| `POSTGRES_PASSWORD` | required  | Database password; compose fails fast when unset                                                |
| `POSTGRES_USER`     | `user`    | Database user                                                                                   |
| `POSTGRES_DB`       | `context` | Database name                                                                                   |
| `GATEWAY_PORT`      | `3000`    | The one published host port; nginx routes it to every service                                   |
| `GATEWAY_BIND`      | `0.0.0.0` | Interface the entry point binds. `127.0.0.1` keeps the whole stack off the network              |
| `GATEWAY_HOSTS`     | derived   | `Host` header values the MCP server and the dashboard API accept, comma separated               |
| `TAG`               | `latest`  | Tag applied to images built by `make build`; compose only ever runs `:latest`                   |

`PROJECT_PATH`/`PROJECT_NAME` are arguments to the indexing job only - they
decide what gets mounted and under which name, and never reach the compose
project or the containers themselves.

## The entry point

nginx is the only service that publishes a host port. It picks the backend
from the request path:

| Path                                 | Served by  | What it is                                      |
| ------------------------------------ | ---------- | ----------------------------------------------- |
| `/mcp`, `/mcp/<project>`             | mcp-server | Streamable HTTP, the transport to use           |
| `/sse`, `/sse/<project>`, `/message` | mcp-server | the older SSE pair                              |
| `/health`                            | mcp-server | what `make status` probes                       |
| `/worker/...`                        | worker-api | the remote summarization queue, prefix stripped |
| everything else                      | web        | the dashboard, its API and the graph page       |

The config is `nginx/default.conf`, bind-mounted read-only. Two details in it
are load-bearing rather than stylistic. Every `proxy_pass` goes through a
variable so that names resolve per request: `worker-api` sits behind the
`remote` profile, and a static upstream name that does not resolve makes nginx
refuse to start instead of answering 502 for that one path. Response buffering
is off on the MCP routes, because both transports hold a response open and a
buffered stream reaches the client only when the session ends.

The viewer is not routed directly. The dashboard already proxies `/graph` and
`/vis-network.min.js` same-origin, and the viewer emits absolute root paths, so
it cannot be mounted under a prefix.

`GATEWAY_BIND` defaults to every interface, which is what a summarization
worker on another machine needs. It also puts the dashboard - the one service
that writes to the database on behalf of a browser, and it has no
authentication of its own - within reach of the local network. Set
`GATEWAY_BIND=127.0.0.1` if you run no remote worker.

## Host header allowlists

Behind the entry point the `Host` header carries the gateway's port, not the
port the service itself listens on, so both services that check it are given
their allowlist by compose from `GATEWAY_HOSTS`. Change `GATEWAY_PORT`, or
reach the stack under any other name, and `GATEWAY_HOSTS` has to say so - a
value that does not name the address you use answers 403 on `/mcp` and `/api`
while `/health`, which is not guarded, still looks healthy.

nginx passes `Host` through unchanged on purpose. The dashboard derives the
`Origin` it expects for a write from the `Host` it sees, so rewriting the
header would make every cross-site request look same-origin, and that check is
what guards an unauthenticated dashboard.

Two optional variables, read by the MCP server, implement DNS rebinding
protection (MCP SDK 0.6 has none of its own - GHSA-w48q-cv73-mx4w). Compose
sets `ALLOWED_HOSTS` from `GATEWAY_HOSTS`; these are the raw variables behind
it:

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

Worth doing before dropping a project and before `make clean`: nodes, edges,
hashes and embeddings come back with one index run, but plans, manually
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
