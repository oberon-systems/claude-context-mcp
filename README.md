# claude-context-mcp

A Dockerized GraphRAG and vector context service for Claude CLI (`claude-code`).

It runs an isolated PostgreSQL database with `pgvector`, indexes a codebase into
a graph of files and their imports, and serves that graph to Claude CLI through
an MCP server over SSE. The indexed codebase is mounted read-only, so nothing in
this stack can modify your sources.

## Architecture

```text
   host codebase                  docker compose stack
   -------------                  --------------------

  /path/to/project  --(ro)-->  +-----------+
                               |  graphify |  one-shot indexing job
                               +-----------+
                                     |  writes nodes and edges
                                     v
                               +-----------+
                               | postgres  |  pgvector/pgvector:pg16
                               +-----------+
                                     ^  reads
                                     |
                               +-----------+
   Claude CLI  <--- SSE --->   | mcp-server|  :3000
                               +-----------+
```

- **postgres** stores the graph (`graph_nodes`, `graph_edges`) and the vector
  embeddings table (`code_embeddings`).
- **graphify** walks the mounted project, creates a node per source file and an
  `imports` edge per import line, then exits. It never writes to the host.
- **mcp-server** exposes the graph to Claude CLI as MCP tools.

## Prerequisites

- Docker with the Compose plugin
- Python 3.11+ (only for the pre-commit toolchain in `make init`)
- Node.js 20+ (only for `make mcp lint` / `typecheck` outside Docker)

## Quick start

```bash
make init                 # virtualenv, pre-commit hooks, .env from the template
$EDITOR .env              # set PROJECT_PATH and POSTGRES_PASSWORD
make build                # build both service images
make up                   # start postgres and mcp-server
make index                # index PROJECT_PATH into the graph
curl -fsS localhost:3000/health
```

Then register the server with Claude CLI:

```bash
claude mcp add --transport sse context http://localhost:3000/sse
```

## Configuration

Copy `.env.example` to `.env`. `make up` and `make index` refuse to run without
it.

| Variable            | Default    | Purpose                                                                      |
| ------------------- | ---------- | ---------------------------------------------------------------------------- |
| `PROJECT_PATH`      | required   | Absolute host path of the codebase to index, mounted read-only at `/project` |
| `POSTGRES_PASSWORD` | required   | Database password; compose fails fast when unset                             |
| `POSTGRES_USER`     | `user`     | Database user                                                                |
| `POSTGRES_DB`       | `context`  | Database name                                                                |
| `DATA_DIR`          | `./pgdata` | Host location of the PostgreSQL data directory                               |
| `MCP_PORT`          | `3000`     | Host port the MCP server is published on                                     |
| `TAG`               | `dev`      | Tag applied to the images built by `make build`                              |

The MCP server also reads two optional variables:

| Variable          | Default                  | Purpose                                                              |
| ----------------- | ------------------------ | -------------------------------------------------------------------- |
| `ALLOWED_HOSTS`   | loopback names on `PORT` | Comma-separated `Host` header allowlist, or `*` to disable the check |
| `ALLOWED_ORIGINS` | empty                    | Comma-separated `Origin` header allowlist                            |

These implement DNS rebinding protection. MCP SDK 0.6 has none of its own
(GHSA-w48q-cv73-mx4w), and without it any web page you visit could reach the
server through your browser. Legitimate MCP clients send no `Origin` header, so
the empty default rejects browser traffic while leaving Claude CLI unaffected.

### Choosing what gets indexed

The indexed project decides for itself, through two optional files at its root.
Both use gitignore syntax and are read from the mount on every `make index`, so
changing what a project indexes needs neither an image rebuild nor a new release
of this repository.

| File         | Purpose                                             |
| ------------ | --------------------------------------------------- |
| `.ctxignore` | Paths pruned from the walk                          |
| `.ctxkeep`   | Files that become nodes; everything else is skipped |

```text
# .ctxignore
.git/
.cache/
build/
*.qcow2

# .ctxkeep
*.py
*.ts
*.hcl
*.md
Makefile
```

Each file **replaces** its built-in default rather than adding to it. That is
what lets a project index file types this repository has never heard of, but it
also means a `.ctxignore` omitting `.git/` really will walk into git's
internals. The indexer warns when it spots that, and otherwise leaves the choice
alone.

Without these files the built-in defaults apply, which is the behaviour of
earlier releases: source extensions such as `.py`, `.ts` and `.go`, minus the
usual `.git`, `.venv`, `node_modules` and friends.

Import edges are a separate matter. They are extracted only from the languages
the parser understands, whatever `.ctxkeep` admits, so indexing documentation
never mines prose for the word "import".

## Make targets

Run `make` for the full list, including the per-service subdivisions.

```text
make init        create the virtualenv and install the pre-commit hooks
make lint        run every pre-commit hook over every file
make build       build both service images
make up          start postgres and mcp-server
make down        stop the stack, keeping the database volume
make index       run the indexing job against PROJECT_PATH
make logs        follow the service logs
make status      show whether the stack runs and whether anything uses it
make psql        open a psql session against the context database
make clean       remove containers, the database directory and the built images
```

`make status` prints the running services, the `/health` payload and the number of
indexed nodes. The `sessions` field in that payload is the count of connected MCP
clients: a healthy stack reporting `0` means the client never attached, which is a
different problem from the stack being down.

Service Makefiles are reachable as subcommands, and work standalone too:

```bash
make graphify build       # same as: make -C graphify build
make mcp typecheck
make mcp help
```

## MCP tools

| Tool                       | Arguments                 | Returns                                                       |
| -------------------------- | ------------------------- | ------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                 | Incoming and outgoing edges of a node, with the relation type |
| `search_code_nodes`        | `query`, optional `limit` | Nodes whose name or id matches the substring                  |

Both return JSON text. Errors come back as a tool result with `isError` set,
rather than tearing down the client session.

## Database schema

`init-db/01-init.sql` is replayed by the PostgreSQL entrypoint **only when the
data directory is empty**. After changing the schema, either apply the change by
hand or run `make clean` to empty `DATA_DIR` and start over. That target asks
for confirmation first, since it destroys the index; `make clean FORCE=1` skips
the prompt for scripted use.

`DATA_DIR` is emptied from inside the postgres service rather than from the
host: its files belong to the container's postgres uid, so a host-side `rm`
fails on permissions.

The database is a bind mount rather than a volume, so `docker compose down -v`
does not remove it. That is also why a regenerated `POSTGRES_PASSWORD` cannot be
applied on its own: the entrypoint skips initialisation while the directory
holds a database, and every connection is then refused with
`password authentication failed`. Run `make clean` to rebuild the database
around the new password.

- `graph_nodes` - one row per file, import or (later) code entity
- `graph_edges` - typed relations between nodes, unique per
  `(source, target, relation)`
- `code_embeddings` - `vector(1536)` chunks with an HNSW cosine index, not
  populated yet (see `ROADMAP.md`)

## Development

All code, comments and commit messages are pure ASCII English. A `pygrep`
pre-commit hook enforces this, so a stray non-ASCII character blocks the commit.

```bash
make lint        # pre-commit over the whole tree
cz c             # commit through commitizen with the wyld-cz adapter
```

`make init` installs both the `pre-commit` and `commit-msg` hook types, so
commit messages are validated automatically. Adding a new file extension to the
repo means adding its hook to `.pre-commit-config.yaml`.

### Releases

```bash
cz bump --changelog     # bump the version, write CHANGELOG.md, create the tag
```

`.cz.yaml` records the **last released** version, so `cz bump` derives the next
one from the commits since that tag. Two settings exist because of the ASCII
rule: `bump_message` overrides commitizen's default release message, which
contains a Unicode arrow, and `allowed_prefixes` lets the `bump:` message
through `cz check`.

`cz bump` rewrites `.cz.yaml` through PyYAML, which sorts the keys and strips
comments, so that file cannot carry explanatory comments of its own. It and the
generated `CHANGELOG.md` are excluded from the prettier and markdownlint hooks,
which would otherwise reformat them and make every release commit fail.

### Hook details

The eslint and `tsc` hook runs `scripts/mcp-check.sh`, which uses
`mcp-server/node_modules` rather than an isolated hook environment, because
`eslint.config.mjs` imports its plugins and ESM resolves those relative to the
config file. `make init` installs them; `make mcp install` does it on its own.

## Layout

```text
init-db/       schema initialization replayed by the postgres entrypoint
graphify/      Python indexer, its image and its Makefile
mcp-server/    TypeScript MCP server, its image and its Makefile
scripts/       helper scripts invoked by pre-commit
docker-compose.yaml
Makefile       root entry point, delegates to the service Makefiles
```
