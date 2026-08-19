# claude-context-mcp

A Dockerized GraphRAG and vector context service for Claude CLI (`claude-code`)
and Gemini CLI.

It runs an isolated PostgreSQL database with `pgvector`, indexes codebases into
graphs of files, entities and their relations, and serves those graphs to both
agents over MCP. One stack holds as many codebases as you index, and an agent
working in one of them can read the graph of another. Every indexed codebase is
mounted read-only, so nothing in this stack can modify your sources.

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
                                  ^      ^  read
                                  |      |
                       +-----------+    +--------+
   Claude, Gemini <--> | mcp-server|    | viewer |  :3001, the graph page
                       +-----------+    +--------+
                            :3000
```

- **postgres** stores the graphs (`graph_nodes`, `graph_edges`), the plans
  (`project_plans`) and the vector embeddings table (`code_embeddings`). Every
  one of those is scoped to a row of `projects`, and `graph_nodes` is keyed on
  `(project, id)`, since `README.md` is a node id in every codebase there is.
- **graphify** walks the mounted project and writes what it finds, then exits.
  It never writes to the host. Two producers share the pass: code goes through
  the upstream [graphifyy](https://github.com/Graphify-Labs/graphify) extractor,
  used as a library, and the infrastructure formats it does not read - Ansible,
  Puppet, Terraform, Dockerfiles, Makefiles, YAML, JSON, Markdown, shell, SQL -
  go
  through the Tree-sitter parsers in `ctxgraph`. Every node records which one
  found it in `metadata.source`.
- **mcp-server** exposes the graph over Streamable HTTP at `/mcp`, and redirects
  `/graph` to the viewer.
- **viewer** renders the graph as an interactive page, from the database, on
  every request. The drawing library is vendored into the image rather than
  loaded from a CDN, so the page works with no route to the internet.

A second MCP server is configured alongside: the upstream stdio server, started
through `docker compose run`, serving the same graph from a `graph.json` written
at index time. It brings its own tools (`query_graph`, `god_nodes`,
`graph_stats`, `get_community`) and lags until the next `make index`. That file
also holds whichever project was indexed last and has no notion of projects at
all, while `mcp-server` reads the database directly and does.

## Prerequisites

- Docker with the Compose plugin
- Python 3.11+ (only for the pre-commit toolchain in `make init`)
- Node.js 20+ (only for `make mcp lint` / `typecheck` outside Docker)

## Quick start

```bash
make init                 # virtualenv, pre-commit hooks, .env from the template
$EDITOR .env              # set PROJECT_PATH and POSTGRES_PASSWORD
make build                # build both service images
make up                   # start postgres, the MCP server and the viewer
make index                # index PROJECT_PATH into the graph
curl -fsS localhost:3000/health
```

Then register the server with the agents, as described below.

## Several codebases, one database

One stack serves every codebase you index, and any tree can be indexed without
being touched:

```bash
make index PROJECT=/home/you/work/api
make index PROJECT=/home/you/work/infra
```

A re-index is authoritative: it reports how many files it selected and how many
of them a node was written for, and names the ones it had to leave out. Both
producers skip a file whose content has not changed since the last run, so a
re-index is cheap; `make index PROJECT=... FRESH=1` distrusts both caches and
re-parses everything, for when a graph looks incomplete rather than merely old.

Each lands under a name taken from the last segment of its path (`api`,
`infra`; override with `PROJECT_NAME=`). The indexed repository needs nothing
of its own for this - no checkout of this project inside it, no `.env`, no
Makefile - because the path is an argument of the indexing job rather than a
setting. All it may optionally carry is `.ctxignore` / `.ctxkeep`, described
below.

An agent connects to one project and can read the others:

```bash
claude mcp add --transport http --scope project context http://localhost:3000/mcp/api
```

Every tool then works on `api` without being told, and takes an optional
`project` argument to reach another graph in the same database - which is what
answers a question spanning two repositories, an Ansible role and the service
it deploys, an API and its client. `list_projects` returns what is indexed.

Removing one is the same shape:

```bash
make unindex PROJECT=/home/you/work/api      # resolved by root path
make unindex PROJECT_NAME=api                # named directly
```

It prints what the drop costs and asks before deleting anything (`FORCE=1`
skips the prompt). The counts are printed in two halves on purpose: nodes,
edges, file hashes and embeddings come back with one `make index`, while plans
and manually written summaries do not come back at all. Unlike `make index`,
this target has no default - naming nothing is an error rather than a delete of
whatever `PROJECT_PATH` happens to point at. The `drop_project` MCP tool does
the same from an agent, reporting first and deleting only when called again
with `confirm: true`. The extraction cache the project leaves behind on the
`graph-out` volume is reclaimed by the next `make index`, whichever tree that
one indexes - the volume is reachable from the indexing job alone.

Node ids are unique within a project, not across the database: `README.md` is a
node in every one of them. Edges stay inside one project, because the indexer
is handed a single tree and resolves every target within it.

## Connecting the agents

The server speaks Streamable HTTP at `/mcp`, and at `/mcp/<project>` to bind a
session to one of the indexed codebases. The older SSE pair (`/sse` plus
`/message`, `/sse/<project>`) is still served for clients that cannot do
better, but nothing should be pointed at it by choice.

Name the project in the address whenever more than one is indexed. A session
opened on a bare `/mcp` has no default, and every tool call then has to carry a
`project` argument of its own; `DEFAULT_PROJECT` in the environment of the
server gives those sessions one.

### Claude Code

Registering it per project is usually what you want, since the graph belongs to
one codebase. That writes `.mcp.json` at the project root, which can be
committed:

```bash
claude mcp add --transport http --scope project context http://localhost:3000/mcp/myproject
```

The file it writes is short enough to keep by hand instead:

```json
{
  "mcpServers": {
    "context": {
      "type": "http",
      "url": "http://localhost:3000/mcp/myproject"
    }
  }
}
```

Drop `--scope project` to register the server for yourself across every
project (`~/.claude.json`), which suits the case where one stack indexes one
codebase you always work in.

### Gemini CLI

Gemini reads `.gemini/settings.json` at the project root. There is no `add`
subcommand; write the file:

```json
{
  "mcpServers": {
    "context": {
      "type": "http",
      "httpUrl": "http://localhost:3000/mcp/myproject",
      "trust": true
    }
  }
}
```

`trust` skips the per-call confirmation prompt. It is reasonable here because
the server only reads a graph of your own code, and unreasonable for anything
that reaches the network.

This repository's own `.mcp.json` and `.gemini/settings.json` are working
examples, and both register a second server alongside: the upstream stdio one,
described at the end of the architecture section.

Whether the client actually attached is what the `sessions` count in `make
status` answers; see the Make targets section.

## Configuration

Copy `.env.example` to `.env`. `make up` and `make index` refuse to run without
it.

| Variable            | Default    | Purpose                                                                                         |
| ------------------- | ---------- | ----------------------------------------------------------------------------------------------- |
| `PROJECT_PATH`      | required   | Absolute host path of the codebase to index, mounted read-only at `/project`                    |
| `PROJECT_NAME`      | derived    | Name the codebase is stored and addressed under; defaults to the last segment of `PROJECT_PATH` |
| `POSTGRES_PASSWORD` | required   | Database password; compose fails fast when unset                                                |
| `POSTGRES_USER`     | `user`     | Database user                                                                                   |
| `POSTGRES_DB`       | `context`  | Database name                                                                                   |
| `DATA_DIR`          | `./pgdata` | Host location of the PostgreSQL data directory                                                  |
| `MCP_PORT`          | `3000`     | Host port the MCP server is published on                                                        |
| `TAG`               | `dev`      | Tag applied to the images built by `make build`                                                 |

`COMPOSE_PROJECT_NAME` is optional and normally left out. The stack is a
singleton - one database holds the graph of every indexed codebase - so
`docker-compose.yaml` pins the name with a `name: claude-context-mcp` key
rather than deriving it from the codebase being indexed. `make index
PROJECT=/somewhere/else` therefore reuses the one stack instead of starting a
second. An explicit `COMPOSE_PROJECT_NAME` in `.env` is what overrides that key.

Setting the variable in `.env` means taking on `DATA_DIR` as well: two stacks
must never bind the same data directory. Two postgres containers over one
`DATA_DIR` corrupt it beyond a normal restart, and nothing stops them - the
`postmaster.pid` lock cannot see a postmaster in another container. The same
applies to renaming the project: the containers under the old name keep running
on `restart: unless-stopped`, and `make down` no longer addresses them, so
remove them by hand before starting the stack under a new name.

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

`.ctxkeep` **replaces** the default file selection rather than adding to it,
which is what lets a project index file types this repository has never heard
of. `.ctxignore` is additive: its patterns are pruned on top of the built-in
skip list (`.git`, `.venv`, `node_modules`, `dist`, `target` and friends), so a
`.ctxignore` that forgets `.git/` still does not walk into git's internals.

Without these files the built-in defaults apply: every extension a parser
understands (`.py`, `.ts`, `.tsx`, `.js`, `.go`, `.rs`, `.rb`, `.sh`, `.md`,
`.toml`, `.yaml`, `.json`, `.tf`, `.hcl`, `.pp`, `.erb`, `.epp`, plus
`Dockerfile` and `Makefile` by name) and `.sql`,
which becomes a file node without being parsed. Files above 1 MB are skipped
whichever way they were selected, since they are generated bundles in practice.

Entity and import edges are a separate matter. They are extracted only from the
languages the parser understands, whatever `.ctxkeep` admits, so indexing
documentation never mines prose for the word "import".

### Ansible

YAML that looks like Ansible is read as Ansible rather than as a bag of keys.
Plays, tasks, handlers and role variables become nodes, and the references
between them become edges:

| Edge            | Source                                          |
| --------------- | ----------------------------------------------- |
| `includes`      | `include_tasks`, `import_tasks`                 |
| `uses_role`     | `roles:`, `include_role`, `import_role`         |
| `depends_on`    | `dependencies:` in `meta/main.yml`              |
| `reads_vars`    | `include_vars`, `vars_files:`                   |
| `uses_template` | `template: src:`                                |
| `uses_file`     | `copy: src:`                                    |
| `notifies`      | `notify:`, resolved to the handler that answers |

Targets are resolved inside the owning role, so `template: src: sshd_config.j2`
finds `roles/sshd/templates/sshd_config.j2`, and `{{ role_path }}` is expanded
because it is the idiomatic way to include a sibling task file. Anything else
still templated is skipped, since only Ansible could expand it. A `notify:`
that no handler answers stays in the graph as an unresolved external node,
which is usually a typo worth seeing. YAML that is not Ansible - CI configs,
compose files, linter settings - falls back to top level keys as before.

### Puppet

Classes, defined types, node definitions and functions become nodes under their
full name, so `class profile::web` is one node whatever file it lives in. Every
resource declaration becomes a node too, named after its type and its title:

| Manifest               | Node            |
| ---------------------- | --------------- |
| `package { 'nginx': }` | `package.nginx` |
| `service { 'nginx': }` | `service.nginx` |

That naming is what lets a reference resolve. `Package['nginx']` in a
`require =>` finds the `package.nginx` declared above it, and `Class['a::b']`
finds the class rather than a resource.

| Edge            | Source                                             |
| --------------- | -------------------------------------------------- |
| `inherits`      | `inherits` on a class                              |
| `includes`      | `include`                                          |
| `requires`      | `require`, both the statement and the `require =>` |
| `requires`      | the `->` and `~>` ordering chains                  |
| `notifies`      | `notify =>`                                        |
| `uses_template` | `template(...)` and `epp(...)`                     |

`include ::stdlib` and `include stdlib` name the same class and land on the
same node. A template reference is resolved through the module layout, so
`template('profile/nginx.conf.erb')` in any manifest finds
`modules/profile/templates/nginx.conf.erb`.

Templates are read as well. The code inside `<% %>` is reassembled in source
order and parsed by the language it is actually written in - Ruby for `.erb`,
Puppet for `.epp` - and the variables it reads become nodes, so `<%= @port %>`
puts `@port` in the graph next to the manifest that renders the file.

Ruby itself (`.rb`) goes to the upstream extractor rather than to a parser
here: classes, methods and the call graph inside a file. It reports no
`require` edges, so Ruby files do not link to each other.

### JSON

Every top level key of a JSON file becomes a node, and nothing below it does.
The depth is deliberate: a second level taken indiscriminately turns one data
file into hundreds of nodes named `type` or `url`.

The manifests that carry structure are read a level deeper, by file name. The
keys of `scripts` in a `package.json` become `script` nodes, the keys of
`mcpServers` in an `.mcp.json` become `mcp_server` nodes, and the dependency
sections become edges:

| Edge         | Source                                                     |
| ------------ | ---------------------------------------------------------- |
| `depends_on` | `dependencies` and friends, `require` in a `composer.json` |
| `extends`    | `extends`, and the `path` of each `references` entry       |

A dependency is recorded under the name of its ecosystem - `npm:express`,
`composer:monolog/monolog` - so it cannot be confused with a key of the same
name declared somewhere else in the tree. An `extends` target is a path and
resolves to the file it names, when that file is relative and indexed; a bare
specifier such as `@tsconfig/node20/tsconfig.json` names a package rather than
a file and is left out.

Lock files - `package-lock.json`, `npm-shrinkwrap.json`, `composer.lock`,
`yarn.lock` - are skipped. They are generated, and they say nothing the
manifest next to them does not. A `.ctxkeep` that names them explicitly still
gets them.

## Make targets

Run `make` for the full list, including the per-service subdivisions.

```text
make init        create the virtualenv and install the pre-commit hooks
make lint        run every pre-commit hook over every file
make build       build both service images
make up          start postgres, mcp-server and the viewer
make down        stop the stack, keeping the database volume
make index       index PROJECT=<path>, or PROJECT_PATH from .env
                 FRESH=1 re-parses every file instead of trusting a cache
make unindex     drop PROJECT=<path> or PROJECT_NAME=<name> from the database
make backup      write the database, or PROJECT=/PROJECT_NAME= alone, to a file
                 KEEP=<n> keeps the n newest backups of that same kind
make restore     put FILE=<path> back, over the database or over one project
make logs        follow the service logs
make status      show whether the stack runs and whether anything uses it
make psql        open a psql session against the context database
make clean       remove containers, the database directory and the built images

make skill-install    register the graphify skill for Claude and Gemini
make skill-uninstall  remove it from both
make skill-status     show where it is registered
```

The skill lives in `skills/graphify/SKILL.md` and is rendered into
`.claude/skills/graphify/` at install time; Gemini is linked to that rendered
copy with `gemini skills link`. Nothing of this project lands in `$HOME`, and
the targets refuse to run under `sudo`, since the agents' own state belongs to
the user who runs them. Editing the source needs a reinstall to take effect.

Installing it into another codebase takes one variable, the root the agent
reads. Everything the rendered copy needs follows from it:

```bash
make skill-install AGENT_ROOT=/home/you/work/api
```

That writes `/home/you/work/api/.claude/skills/graphify/SKILL.md`, whose
rebuild command is `make -C <this repo> index PROJECT=/home/you/work/api` - it
reaches the stack where the stack actually is, and names the tree it belongs
to. Nothing of this repository has to exist inside `api` for that to work.

| Variable      | Default         | Purpose                                                       |
| ------------- | --------------- | ------------------------------------------------------------- |
| `AGENT_ROOT`  | `$(CURDIR)`     | Project root whose `.claude/skills/` the agent reads          |
| `MAKE_PREFIX` | derived         | How that root reaches these targets, substituted for `@MAKE@` |
| `SKILL_ROOT`  | `$(AGENT_ROOT)` | Tree the skill rebuilds, substituted for `@ROOT@`             |

`MAKE_PREFIX` is `make` when installing into this repository and `make -C` here
otherwise. Pass it only when the target codebase wraps these targets in a proxy
of its own (`MAKE_PREFIX="make cache"`).

Without `AGENT_ROOT` the skill lands in this repository's own `.claude/skills/`,
where the other project's agent never looks.

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

| Tool                       | Arguments                                        | Returns                                                                |
| -------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                                        | Incoming and outgoing edges of a node, with the relation type          |
| `search_code_nodes`        | `query`, optional `limit`                        | Nodes whose name or id matches the substring                           |
| `shortest_path`            | `source_id`, `target_id`, optional `max_hops`    | Shortest chain of relations between two nodes                          |
| `save_node_summary`        | `node_id`, `summary`                             | Saves or updates a summary for a specific node                         |
| `get_node_summary`         | `node_id`                                        | Retrieves summary, file path, and type for a node                      |
| `save_plan`                | `plan_id`, `title`, `content`, optional `status` | Creates or updates a persistent project plan                           |
| `get_plans`                | optional `status`                                | Retrieves project plans filtered by status                             |
| `drop_plan`                | `plan_id`                                        | Deletes one plan outright, for one written by mistake                  |
| `drop_project`             | `name`, optional `confirm`                       | Reports what dropping a project costs, and drops it on `confirm: true` |

Both return JSON text. Errors come back as a tool result with `isError` set,
rather than tearing down the client session.

### Automatic Summarization

Indexing generates a summary for every file from its leading docstring, comment
block or first heading, and stores it on the file node in `graph_nodes`. A
summary written through `save_node_summary` is tagged `summary_source: manual`
in the node metadata, and re-indexing leaves those alone; generated summaries
are refreshed on every run.

### Persistent Project Planning

The system includes dedicated support for tracking project execution roadmaps. Plans are stored in the `project_plans` table and can be managed directly by an AI client using the `save_plan` and `get_plans` tools.

## Backup and restore

`make backup` writes the database to
`~/.local/share/context-mcp/backups`, next to the data directory itself:

```bash
make backup                                  # the whole database
make backup PROJECT_NAME=api                 # one codebase
make backup PROJECT=/home/you/work/api       # the same, resolved by root path
make backup KEEP=7                           # and prune older backups of that kind
```

The two are different files for a reason. The whole database is a `pg_dump`
custom archive, `context-<timestamp>.dump`. A single project cannot be one:
`pg_dump` selects by table and never by row, and every table here is scoped by
a `project` column. So one codebase comes out as
`<name>-<timestamp>.sql.gz` - the `COPY` blocks of its rows in foreign-key
order, wrapped in a single transaction, which is the shape `pg_dump
--format=plain` emits and which `psql` replays. Neither file is written under
its final name until it has been read back, so an interrupted dump cannot be
mistaken for a backup.

`FILE=<path>` writes somewhere else instead, and rotation then leaves that file
alone: `KEEP=<n>` only ever prunes the timestamped names this scheme produces,
and prunes each kind on its own, so keeping two database archives never deletes
a project's only backup.

Restoring names the file and nothing else:

```bash
make restore FILE=context-20260819-115420.dump   # replaces the whole database
make restore FILE=api-20260819-115137.sql.gz     # replaces that one project
```

A bare name is looked up in the backup directory, and `make restore` with no
`FILE=` lists what is there rather than guessing at the newest. Both directions
print what is about to be replaced and ask first; `FORCE=1` skips the prompt.
A project restore is atomic - the file carries its own `BEGIN`, the `DELETE`
that cascades the old copy away, and `COMMIT` - and a database restore runs in
one transaction with `--exit-on-error`, since `pg_restore` otherwise treats
errors as non-fatal and would report success over a half-restored database.
Either is refused outright while an index job is running.

Worth doing before `make unindex` and before `make clean`: nodes, edges,
hashes and embeddings come back with one `make index`, but plans and manually
written summaries come back from nowhere else.

## Database schema

`init-db/01-init.sql` is replayed by the PostgreSQL entrypoint **only when the
data directory is empty**. After changing the schema, either apply the change by
hand or run `make clean` to empty `DATA_DIR` and start over. That target asks
for confirmation first, since it destroys the index; `make clean FORCE=1` skips
the prompt for scripted use. To remove a single codebase rather than the whole
database, use `make unindex` - everything cascades from the row in `projects`,
so one `DELETE` there takes that project's nodes, edges, hashes, embeddings and
plans, and nothing of any other project. Both are worth a `make backup` first.

`DATA_DIR` is emptied from inside the postgres service rather than from the
host: its files belong to the container's postgres uid, so a host-side `rm`
fails on permissions.

The database is a bind mount rather than a volume, so `docker compose down -v`
does not remove it. That is also why a regenerated `POSTGRES_PASSWORD` cannot be
applied on its own: the entrypoint skips initialisation while the directory
holds a database, and every connection is then refused with
`password authentication failed`. Run `make clean` to rebuild the database
around the new password.

- `graph_nodes` - one row per file, per code entity (`file_path::name`) and per
  unresolved external import or symbol
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
  src/ctxgraph/  the indexer package, run as `python -m ctxgraph`
mcp-server/    TypeScript MCP server, its image and its Makefile
skills/        the agent skill, rendered into place by `make skill-install`
scripts/       helper scripts invoked by pre-commit
docker-compose.yaml
Makefile       root entry point, delegates to the service Makefiles
```
