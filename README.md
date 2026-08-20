# claude-context-mcp

A Dockerized GraphRAG and vector context service for Claude CLI (`claude-code`)
and Gemini CLI.

It runs an isolated PostgreSQL database with `pgvector`, indexes codebases into
graphs of files, entities and their relations, and serves those graphs to both
agents over MCP. One stack holds as many codebases as you index, and an agent
working in one of them can read the graph of another. Every indexed codebase is
mounted read-only, so nothing in this stack can modify your sources.

Full docs: <https://oberon-systems.github.io/claude-context-mcp/>

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
                                ^    ^    ^  read
                                |    |    |
                     +-----------+  +--------+  +-----+
   Claude, Gemini <->| mcp-server|  | viewer |  | web |<-> browser
                     +-----------+  +--------+  +-----+
                          :3000       :3001       :3002
                                          ^          |
                                          +----------+
                                       the graph page, proxied
```

- **postgres** stores the graphs (`graph_nodes`, `graph_edges`), the vector
  embeddings table (`code_embeddings`) and the plans (`plans`). Everything
  derived from a tree is scoped to a row of `projects`, and `graph_nodes` is
  keyed on `(project, id)`, since `README.md` is a node id in every codebase
  there is. Plans are the exception: they are written by an agent and rebuilt
  by nobody, so they live in one table for the whole database and carry the
  project as a tag rather than an owner.
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
- **web** is the dashboard, on loopback only: the indexed projects with their
  counts and how old each index is, a browsable node index with summaries and
  neighbours, and every plan in the database, filtered by project, status and
  type and editable in place. The graph itself is the viewer's page, proxied
  rather than linked, so the frame and the API share one origin.

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
- for the optional model summaries only: ~1 GB of disk and 4 GB of memory,
  downloaded by `make llm-model-install` and mounted read-only at `/models`

## Quick start

```bash
make init                 # virtualenv, pre-commit hooks, .env from the template
$EDITOR .env              # set PROJECT_PATH and POSTGRES_PASSWORD
make build                # build both service images
make up                   # start postgres, the MCP server and the viewer
make install              # onboard this codebase and index it
curl -fsS localhost:3000/health
```

## Onboarding a codebase

`make install` is the whole of it, for this repository or for any other tree:

```bash
make install AGENT_ROOT=/home/you/work/api
```

Six things, none of which replaces a file that already exists:

| Step                           | What it leaves behind                                                                   |
| ------------------------------ | --------------------------------------------------------------------------------------- |
| `.ctxkeep` / `.ctxignore`      | generated from the file types the tree actually holds, and verified                     |
| `.mcp.json`                    | the `context` server for Claude Code, at `/mcp/<project>`                               |
| `.gemini/settings.json`        | the same address for the Gemini CLI                                                     |
| `.claude/skills/*/SKILL.md`    | every skill, rendered for that root (`make skill-install` on its own)                   |
| `CLAUDE.local.md`, `GEMINI.md` | how an agent should use the graph, from `templates/CLAUDE.local.md`                     |
| shell aliases                  | `context-index`, `context-reindex`, `context-status`, `context-install`, in `~/.bashrc` |

Then it indexes the tree, so the address it just wrote answers immediately.

Every step reports `written`, `merged`, `kept` or `skipped`, and the run ends
with that list, so a second run is how a codebase picks up a piece added later
rather than a way to reset one. The project name comes from the same code the
indexer uses, which is what stops the `/mcp/<project>` address from naming a
project that will never exist.

| Variable       | Default               | Purpose                                    |
| -------------- | --------------------- | ------------------------------------------ |
| `AGENT_ROOT`   | this repository       | the tree being onboarded                   |
| `PROJECT_NAME` | its last path segment | the name it is stored and addressed under  |
| `INDEX`        | `1`                   | `INDEX=0` stops before building the graph  |
| `ALIASES`      | `1`                   | `ALIASES=0` leaves the shell rc file alone |
| `SHELL_RC`     | `~/.bashrc`           | which rc file the alias block goes to      |

The aliases are fenced by a `# >>> claude-context-mcp >>>` marker and written
once. Aliases of those names that predate the marker are left alone and the
block is printed instead, since replacing them is a decision about someone
else's shell.

`make build` is optional: the images are published to
`ghcr.io/oberon-systems/claude-context-mcp`, and `make up` pulls them when they
are missing. A local build produces exactly that reference at `:latest`, which
is the one `docker-compose.yaml` pins, so it takes the published image's place
until `make pull` fetches it again. `make build` prints the image id it left
behind, and says so when the running stack still holds the previous one.

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
re-index is cheap; `make reindex PROJECT=...` distrusts both caches and
re-parses everything, for when a graph looks incomplete rather than merely old.
It is the named form of `make index PROJECT=... FRESH=1`, and the
`context-reindex` alias is the same thing for the tree you stand in.

Each lands under a name taken from the last segment of its path (`api`,
`infra`; override with `PROJECT_NAME=`). Names beginning with `_` are reserved
for the built-in projects described under [Agent memory](#agent-memory). The indexed repository needs nothing
of its own for this - no checkout of this project inside it, no `.env`, no
Makefile - because the path is an argument of the indexing job rather than a
setting. All it may optionally carry is `.ctxignore` / `.ctxkeep`, described
below.

`TYPE=` says what kind of project it is, which is what a search across the
database narrows on:

```bash
make index PROJECT=/home/you/work/handbook TYPE=docs
make index PROJECT=/home/you/work/infra TYPE=config
```

`codebase` is the default, and `docs` and `config` are the other two an index
run may write. They are all indexed trees and differ only as a filter - the
fourth, `memory`, is not a tree at all. The type is stored once: a later
`make index` without `TYPE=` keeps it rather than resetting it to the default.

An agent connects to one project and can read the others:

```bash
claude mcp add --transport http --scope project context http://localhost:3000/mcp/api
```

Every tool then works on `api` without being told, and takes an optional
`project` argument to reach another graph in the same database - which is what
answers a question spanning two repositories, an Ansible role and the service
it deploys, an API and its client. `list_projects` returns what is indexed,
with each project's type.

`search_code_nodes` is the one read that need not stop at a project.
`project: "*"` searches every graph in the database, and `project_type: "docs"`
searches every project of that kind - which is how a convention is found
without knowing which repository wrote it down. The limit is shared out between
the projects rather than spent on whichever sorts first, so a search over six
codebases answers with all six.

Removing one is the same shape:

```bash
make unindex PROJECT=/home/you/work/api      # resolved by root path
make unindex PROJECT_NAME=api                # named directly
```

It prints what the drop costs and asks before deleting anything (`FORCE=1`
skips the prompt). The counts are printed in three parts on purpose: nodes,
edges, file hashes and embeddings come back with one `make index`, manually
written summaries do not come back at all, and plans are not deleted in the
first place - they keep the name as a tag and stay readable through
`get_plans`. Unlike `make index`,
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

`make install` writes both files described here, which is the reason to read
this section rather than follow it: what follows is the manual form, for a
codebase onboarded by hand or a registration `make install` deliberately left
alone.

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

Copy `.env.example` to `.env`; `make up` and `make index` refuse to run
without it. Sets the project path/name, database credentials, published
ports and the optional `ALLOWED_HOSTS`/`ALLOWED_ORIGINS` DNS-rebinding
allowlist. The stack is a singleton by design - one compose project name,
one fixed data directory - so `make index PROJECT=/somewhere/else` reuses
the running stack rather than starting a second one. Full variable
reference: [deployment](https://oberon-systems.github.io/claude-context-mcp/deployment.html).

### Choosing what gets indexed

Two optional gitignore-style files at the indexed project's root,
`.ctxignore` (paths to prune) and `.ctxkeep` (files that become nodes,
replacing the default selection). `make install` generates both from what
the tree holds. Per-format nuances - what Ansible, Puppet and JSON parsing
extracts - are in
[formats](https://oberon-systems.github.io/claude-context-mcp/formats.html).

## Make targets

Run `make` for the full list, including the per-service subdivisions.

```text
make init        create the virtualenv and install the pre-commit hooks
make install     onboard AGENT_ROOT=<path> onto the stack, then index it
                 INDEX=0 skips the indexing, ALIASES=0 leaves ~/.bashrc alone
make lint        run every pre-commit hook over every file
make build       build every service image
make pull        pull the published images, discarding a local build
make up          start postgres, mcp-server, the viewer and the dashboard
make down        stop the stack, keeping the database volume
make index       index PROJECT=<path>, or PROJECT_PATH from .env
                 TYPE=docs|config categorises it; unset keeps what is stored
                 FRESH=1 re-parses every file instead of trusting a cache
make reindex     index PROJECT=<path> again, trusting neither cache
make unindex     drop PROJECT=<path> or PROJECT_NAME=<name> from the database
make summarize   describe PROJECT's files with the model (BG=1 detaches)
make llm-model-install
                 download the summarizer weights (FORCE=1 re-downloads)
make backup      write the database, or PROJECT=/PROJECT_NAME= alone, to a file
                 KEEP=<n> keeps the n newest backups of that kind, 7 by default
make restore     put FILE=<path> back, over the database or over one project
make logs        follow the service logs
make status      show whether the stack runs and whether anything uses it
make psql        open a psql session against the context database
make db migrate  apply every pending schema migration
make db version  show which migrations are applied and which are pending
make db new      write the next migration file, NAME=<slug>
make web dev     serve the dashboard client from vite against the running stack
make clean       remove containers, the database directory and the built images

make skill-install    register every skill for Claude and Gemini
make skill-uninstall  remove them from both
make skill-status     show which ones are registered
```

These three are what `make install` calls for the skills alone; run them
directly to reinstall them without touching anything else.

Every directory under `skills/` holding a `SKILL.md` is one skill, and all of
them are installed: `graphify` (indexing a tree and the traps of reading the
graph), `commit` (driving commitizen) and `delegate` (handing work to the
Gemini CLI and reviewing it). Each is rendered into `.claude/skills/<name>/`
at install time; Gemini is linked to that rendered copy with
`gemini skills link`. Nothing of this project lands in `$HOME`, and
the targets refuse to run under `sudo`, since the agents' own state belongs to
the user who runs them. Editing the source needs a reinstall to take effect.

Installing them into another codebase takes one variable, the root the agent
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

| Tool                       | Arguments                                                                                          | Returns                                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                                                                                          | Incoming and outgoing edges of a node, with the relation type                                       |
| `search_code_nodes`        | `query`, optional `project`, `project_type`, `limit`                                               | Nodes whose name or id matches, in one project or across a whole kind                               |
| `shortest_path`            | `source_id`, `target_id`, optional `max_hops`                                                      | Shortest chain of relations between two nodes                                                       |
| `save_node_summary`        | `node_id`, `summary`                                                                               | Saves or updates a summary for a specific node                                                      |
| `get_node_summary`         | `node_id`                                                                                          | Retrieves summary, file path, and type for a node                                                   |
| `save_plan`                | `plan_id`, `title`, `content`, optional `project`, `status`, `type`                                | Creates or updates a persistent plan; `project: "*"` makes it global                                |
| `get_plans`                | optional `project`, `status`, `type`                                                               | Plans of one project plus the global ones; `project: "*"` lists all                                 |
| `drop_plan`                | `plan_id`                                                                                          | Deletes one plan outright, for one written by mistake                                               |
| `drop_project`             | `name`, optional `confirm`                                                                         | Reports what dropping a project costs, and drops it on `confirm: true`                              |
| `save_memory`              | `memory_id`, `title`, `text`, optional `about`, `summary`, `tags`                                  | Writes a memory into `_memory`; `about: "*"` makes it global                                        |
| `get_memory`               | optional `memory_id`, `about`, `tags`, `query`, `limit`                                            | Memories of one scope plus the global ones, in full                                                 |
| `drop_memory`              | `memory_id`, optional `about`                                                                      | Deletes one memory that turned out to be wrong                                                      |
| `save_suggestion`          | `suggestion_id`, `title`, `detail`, optional `about`, `summary`, `kind`, `lever`, `status`, `bump` | Records a gap in `_suggestions`; saving under an existing slug counts a hit rather than duplicating |
| `get_suggestions`          | optional `suggestion_id`, `about`, `status`, `kind`, `query`, `limit`                              | Open gaps of one scope plus the global ones, most often hit first                                   |
| `drop_suggestion`          | `suggestion_id`, optional `about`                                                                  | Deletes one suggestion written by mistake; a closed gap is retired instead                          |
| `list_indexed_files`       | optional `project`                                                                                 | The files tracked in `file_hashes`, which is the parser half of the tree                            |
| `get_file_hash`            | `file_path`, optional `project`                                                                    | The stored hash of one file, or nothing when it was never indexed                                   |
| `set_file_hash`            | `file_path`, `hash`, optional `project`                                                            | Writes a file's hash, marking it indexed                                                            |
| `clear_file_hash`          | `file_path`, optional `project`                                                                    | Forgets a file's hash, so the next run re-parses it                                                 |

Both return JSON text. Errors come back as a tool result with `isError` set,
rather than tearing down the client session.

### Automatic Summarization

Indexing writes a fast summary for every file from its leading docstring or
first heading. A local GGUF model writes better ones, as a separate pass:

```bash
make llm-model-install               # once: ~1 GB of weights
make summarize PROJECT=$(pwd)        # describe what has no model summary yet
```

That pass can also run on another machine with a GPU instead of the stack's
CPU, over HTTP, with no checkout of the code and no database access -
including from Windows. Full walkthrough, model catalogue and the remote
worker setup:
[summarization](https://oberon-systems.github.io/claude-context-mcp/summarization.html).

### Persistent Project Planning

Plans are managed by an AI client through `save_plan`, `get_plans` and
`drop_plan`, and live in one `plans` table for the whole database rather
than being owned by a project - `project` is a free-text tag, not a
foreign key, so a plan survives `make unindex` and `drop_project`. Full
lifecycle (`status` vs `type`, the `"*"` project):
[usage](https://oberon-systems.github.io/claude-context-mcp/usage.html).

### Agent memory

What an agent works out about a repository - a convention, a decision, why
something is the way it is - has nowhere to live in a tree it may only read.
`save_memory`, `get_memory` and `drop_memory` write it into `_memory`, a
built-in project of type `memory` that is created by the migration and holds
records rather than files. Nothing indexes into it: names beginning with `_`
are refused by `make index`, so a memory is never pruned by a re-index the way
a derived node is.

A memory is tagged with what it is about, the way a plan is - a project name,
or `"*"` for one that belongs to no repository in particular - and a read of
one scope always sees the global ones alongside it. Because `_memory` is a
project like any other, `search_code_nodes` with `project_type: "memory"`
finds memories, and `project: "*"` finds them next to what the code says.

### Gap tracking

An agent that had to answer something by hand knows exactly what the graph was
missing, and until now said so in a sentence that died with the session. So the
same gap was rediscovered every session and never accumulated evidence: one
reported eight times looked exactly like one reported once. `save_suggestion`,
`get_suggestions` and `drop_suggestion` write it into `_suggestions`, a
built-in project alongside `_memory`, holding what the graph could not answer
and the concrete change that would fix it.

The identifier is the mechanism. It is a stable slug derived from the gap, so
reporting the same gap again is the same call: the record keeps its first
sighting, moves its last, counts a hit, and reopens if it had been marked
resolved - a gap hit again is not a resolved one. Each carries a `kind`, a
`status`, and the `lever` closing it would move: `tokens` when the answer was
re-derived by hand, `coverage` when the graph does not describe it at all,
`runtime` when it was answerable but slow. Reading defaults to the open ones,
most often hit first, which is the ranking the whole thing exists for.

## Web interface

`make up` publishes a dashboard on <http://127.0.0.1:3002> (loopback only):
indexed projects and their staleness, a per-project node/graph/file browser,
every plan in the database, editable in place, and the recorded gaps, ranked by
how often they were hit. Details:
[usage](https://oberon-systems.github.io/claude-context-mcp/usage.html).

## Backup and restore

```bash
make backup                              # the whole database
make backup PROJECT_NAME=api             # one codebase
make restore FILE=context-20260819-115420.dump
```

Whole-database backups are a `pg_dump` archive; single-project backups are a
plain-SQL replay, since `pg_dump` can't select by row. Both rotate (`KEEP`,
7 by default) and both print what they're about to replace before doing it.
Full walkthrough: [deployment](https://oberon-systems.github.io/claude-context-mcp/deployment.html).

## Database schema

Schema migrations are goose-managed (`migrations/`); `make up` applies
whatever is pending before anything else touches the database. Core tables:
`projects` - one row per project, carrying the `type` a search narrows on -
plus `graph_nodes`, `graph_edges`, `code_embeddings` (unused for now) and
`plans`; the last one has no foreign key to a project, so it survives
`make unindex` and `make clean`. Full internals, including how
`make db <target>` drives goose and what a restore needs:
[nuances](https://oberon-systems.github.io/claude-context-mcp/nuances.html).

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
config file. `make init` installs them; `make mcp deps` does it on its own.

`scripts/web-check.sh` is its sibling for the dashboard, over
`web/node_modules`: it lints `.ts` and `.tsx` and runs both tsc projects, the
server's and the client's. `make web deps` installs what it needs.

## Layout

```text
migrations/    numbered schema migrations and the Makefile driving goose
graphify/      Python indexer, its image and its Makefile
  src/ctxgraph/  the indexer package, run as `python -m ctxgraph`
mcp-server/    TypeScript MCP server, its image and its Makefile
web/           the dashboard: JSON API, React client, its image and its Makefile
skills/        the agent skill, rendered into place by `make skill-install`
templates/     the CLAUDE.local.md an onboarded codebase gets
scripts/       helper scripts: onboarding, backup, restore, pre-commit
docker-compose.yaml
Makefile       root entry point, delegates to the service Makefiles
```
