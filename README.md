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

| Step                               | What it leaves behind                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| `.ctxkeep` / `.ctxignore`          | generated from the file types the tree actually holds, and verified                     |
| `.mcp.json`                        | the `context` server for Claude Code, at `/mcp/<project>`                               |
| `.gemini/settings.json`            | the same address for the Gemini CLI                                                     |
| `.claude/skills/graphify/SKILL.md` | the skill, rendered for that root (`make skill-install` on its own)                     |
| `CLAUDE.local.md`, `GEMINI.md`     | how an agent should use the graph, from `templates/CLAUDE.local.md`                     |
| shell aliases                      | `context-index`, `context-reindex`, `context-status`, `context-install`, in `~/.bashrc` |

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

Copy `.env.example` to `.env`. `make up` and `make index` refuse to run without
it.

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
| `TAG`               | `latest`  | Tag applied to the images built by `make build`; compose only ever runs `:latest`               |

The stack is a singleton, and nothing about the codebase being indexed changes
that. `docker-compose.yaml` pins the compose project name with a
`name: claude-context-mcp` key, and the database always lives at
`~/.local/share/context-mcp/db`. Neither is configurable, on purpose: one
database holds the graph of every indexed codebase, so `make index
PROJECT=/somewhere/else` reuses the one stack instead of starting a second.

`PROJECT_PATH` and `PROJECT_NAME` are arguments to the indexing job and nothing
more. They decide which tree is mounted read-only at `/project` and under which
name its graph is stored, and they never reach the stack: not the compose
project, not the containers, not the data directory.

Running a second stack from this compose file is unsupported and destroys data.
Two postgres containers over one data directory corrupt it beyond a normal
restart, and nothing stops them - the `postmaster.pid` lock cannot see a
postmaster in another container. There is no per-stack data directory to keep
them apart, because there is only one path and it is not a setting.

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
of this repository. `make install` generates the pair from what the tree holds
and verifies the result by simulation before writing it, so what follows is
what that generated file means and how to change it.

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
make install     onboard AGENT_ROOT=<path> onto the stack, then index it
                 INDEX=0 skips the indexing, ALIASES=0 leaves ~/.bashrc alone
make lint        run every pre-commit hook over every file
make build       build every service image
make pull        pull the published images, discarding a local build
make up          start postgres, mcp-server, the viewer and the dashboard
make down        stop the stack, keeping the database volume
make index       index PROJECT=<path>, or PROJECT_PATH from .env
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

make skill-install    register the graphify skill for Claude and Gemini
make skill-uninstall  remove it from both
make skill-status     show where it is registered
```

These three are what `make install` calls for the skill alone; run them
directly to reinstall it without touching anything else.

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

| Tool                       | Arguments                                                           | Returns                                                                |
| -------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                                                           | Incoming and outgoing edges of a node, with the relation type          |
| `search_code_nodes`        | `query`, optional `limit`                                           | Nodes whose name or id matches the substring                           |
| `shortest_path`            | `source_id`, `target_id`, optional `max_hops`                       | Shortest chain of relations between two nodes                          |
| `save_node_summary`        | `node_id`, `summary`                                                | Saves or updates a summary for a specific node                         |
| `get_node_summary`         | `node_id`                                                           | Retrieves summary, file path, and type for a node                      |
| `save_plan`                | `plan_id`, `title`, `content`, optional `project`, `status`, `type` | Creates or updates a persistent plan; `project: "*"` makes it global   |
| `get_plans`                | optional `project`, `status`, `type`                                | Plans of one project plus the global ones; `project: "*"` lists all    |
| `drop_plan`                | `plan_id`                                                           | Deletes one plan outright, for one written by mistake                  |
| `drop_project`             | `name`, optional `confirm`                                          | Reports what dropping a project costs, and drops it on `confirm: true` |

Both return JSON text. Errors come back as a tool result with `isError` set,
rather than tearing down the client session.

### Automatic Summarization

Indexing writes a summary for every file node in `graph_nodes` from its leading
docstring, comment block or first heading. That is the fast producer and it is
what a plain `make index` does, for both halves of the tree.

A local GGUF model writes better ones, and it is slow: seconds per file, so a
first index of a large tree would spend hours in it. It therefore runs as a
pass of its own, after the graph exists:

```bash
make llm-model-install               # once: ~1 GB of weights
make summarize PROJECT=$(pwd)        # describe what has no model summary yet
make summarize PROJECT=$(pwd) BG=1   # the same, detached, for a large tree
make summarize PROJECT=$(pwd) LIMIT=20  # stop after 20, to time the rest
```

`MODEL=` picks the weights, for both the download and the run:

| `MODEL=`              | size    | ~s per file at 8 threads            |
| --------------------- | ------- | ----------------------------------- |
| `qwen-1.5b` (default) | 1.12 GB | ~8                                  |
| `qwen-3b`             | 2.10 GB | ~20, wants `GRAPHIFY_MEM=6g`        |
| `qwen-0.5b`           | 0.49 GB | ~4                                  |
| `smollm2`             | 1.06 GB | ~13, and it declines far more files |

The default is Qwen2.5-Coder-1.5B-Instruct because it is trained on code:
measured against SmolLM2-1.7B on the same 15 files of this repository, it was
faster and declined none of them, where SmolLM2 answered four with the file
name. Qwen3 is not on the list on purpose - its GGUF release is Q8_0 only, and
it is a thinking model, which is the opposite of a one-line summary.

Several models can sit in `~/.local/share/context-mcp/models` at once; the make
targets name the one they downloaded, and a hand-rolled `docker run` picks the
only one there or is told through `LLM_MODEL_PATH`.

The pass commits per file and only visits files whose summary still comes from
the head of the file, so it can be stopped and started again without repeating
itself; `FRESH=1` re-describes everything instead. `make index SUMMARIZE=1`
does the same work inline for a tree small enough to wait for.

Every answer is cached in `summary_cache`, keyed by the hash of the text the
model was shown, so a re-index pays for the files that changed and looks up the
rest. The cache belongs to the project and goes with it on `make unindex`.

The container is capped while it runs: `GRAPHIFY_CPUS` and `GRAPHIFY_MEM` in
`.env` (2 cpus and 4g by default), with `LLM_THREADS` at or below the cpu
count, and the weights are released as soon as the pass ends. The cpu count is
also the throughput: with the default model this repository measured ~30 s per
file at the default 2 cpus and ~8 s at 8. Budget accordingly before pointing it
at a large tree - it is per file, and it is why the pass detaches, and why
`LIMIT=` exists.

Expect it to decline a file now and then: a small model reading the head of a
changelog or a lock file can answer with the file name, and an answer that says
no more than the node id is rejected rather than stored. Those keep the
head-of-file summary and are counted in the closing line.

Node metadata records which of the three wrote the summary in `summary_source`:
`auto` for the head of the file, `llm` for the model, `manual` for one written
through `save_node_summary`. A manual summary is never overwritten, and a model
summary survives a re-index rather than being replaced by the fast one.

#### Summarizing on another machine

The pass above runs on whatever CPU the stack was given. A machine with a GPU
does the same work in about a second a file, so the model half can be moved to
it - without giving that machine a checkout of your code, a database port or a
share. It reads the text of each file from the graph, over HTTP.

That means the text has to be in the graph, which it now is: an index run keeps
the first `CONTENT_STORE_CHARS` characters (16000 by default, about 4k tokens)
of every file in `graph_nodes.content`. Files named like secrets - `.env`,
`*.pem`, `*.tfvars`, `id_rsa*` and the rest - still get a node and a
head-of-file summary, but their text is never stored. `CONTENT_STORE_CHARS=0`
turns the whole thing off.

On the stack:

```bash
openssl rand -hex 24                 # put it in .env as WORKER_API_TOKEN
make reindex PROJECT=/path/to/repo   # once, so the text is in the graph
make api-up                          # publishes the queue on port 3003
make jobs PROJECT_NAME=kurum         # queue every file that has no model summary
make job ID=7                        # how far along it is
```

On the machine with the GPU, from a copy of this repository - see
[`worker/README.md`](worker/README.md) for the CUDA wheel and the weights:

```bash
py -m ctxworker.download
py -m ctxworker --api http://192.168.1.10:3003 --project kurum
```

The queue is leased, not handed out: a worker claims a batch for five minutes,
and a batch whose worker dies returns to the queue for whoever asks next.
Several workers may share one job. Answers are not trusted - each sentence goes
through the same shaping and the same "says nothing the file name does not"
gate the local pass applies, is capped in length, and can never overwrite a
`manual` summary. What the model has already described is served from
`summary_cache` and never leased again.

The API is the one service here that is meant to be reached from another
machine, and the only one with a secret of its own. It is plain HTTP bound to
every interface, so it belongs on a trusted LAN or a VPN, behind a reverse
proxy if it needs to go further. It refuses to start without
`WORKER_API_TOKEN`, and refuses a token shorter than 16 characters.

The local pass and a remote job show the model different amounts of text - 2000
characters against 16000 - so they fill different rows of `summary_cache`. That
is not a bug: a different prompt is a different answer.

### Persistent Project Planning

The system includes dedicated support for tracking project execution roadmaps.
Plans are managed directly by an AI client through `save_plan`, `get_plans` and
`drop_plan`.

Unlike everything else here, a plan is not derived from a tree: nothing rebuilds
it, so it is not owned by a project. The `plans` table holds them for the whole
database, keyed on a `plan_id` that is unique across it, with `project` as a
free-text tag. That has three consequences worth knowing:

- A plan survives `make unindex` and `drop_project`, and can name a repository
  this database has never indexed.
- `get_plans` defaults to the project the session connected to and always adds
  the global plans on top. `project: "*"` lists every project's plans.
- `save_plan` with `project: "*"` stores a plan that belongs to no project and
  is listed under all of them - the right home for a procedure that is run on
  demand rather than finished once.

`type` is the other axis, and it is not a status. `status` says where a plan
stands - `active`, `completed`, `archived` - while `type` says what the record
is: `plan` for work executed once, `template` for a form to copy, `procedure`
for a routine run on demand. They were one column until migration 0003, which is
why a template used to be a status and could therefore never be completed.

Two consequences: `get_plans` defaults to `type: "plan"`, so a procedure never
turns up where an agent reads approved pending work, and `type: "*"` is how to
see every kind. And re-saving a plan without naming a `type` resets it to
`plan`, exactly as it already resets `status` to `active` - `save_plan` writes a
whole row, it does not patch one.

## Web interface

`make up` publishes a dashboard on <http://127.0.0.1:3002>, bound to loopback
because it is the only service here that writes to the database on behalf of a
browser and it carries no authentication of its own. Three views:

- **Projects** - what is indexed, where it came from, how many nodes, edges,
  files and plans each holds, and how old the index is. A project last indexed
  more than a week ago says so in amber; one that was never indexed says that
  instead of showing a date.
- **A project** - four tabs. _overview_ counts what the graph holds and lists
  the node types as chips that open a filtered node list. _graph_ is the
  viewer's page, proxied through this service so the frame shares the page's
  origin; above 5,000 nodes it states the size and waits for a click, because
  the viewer draws the whole graph in one request. _nodes_ searches names and
  ids and opens one node with its summary, its metadata, its neighbours and,
  on request, its stored source. _files_ lists the file nodes, with how many
  entities each carries and whether a hash was recorded for it.
- **Plans** - every plan in the database, including the global ones and the
  ones tagged with a codebase nothing has indexed, filtered by project, status,
  type or a search over titles and bodies. A plan opens as rendered markdown
  and edits in place; the status can be changed from the list.

What it can change: plans, and dropping a project. A drop is reported before it
happens - what one `make index` would rebuild, what nothing rebuilds, and that
the plans tagged with the name survive - and then asks for the project name to
be typed. Re-indexing is not offered: `make index` remains the way in.

## Backup and restore

`make backup` writes the database to
`~/.local/share/context-mcp/backups`, next to the data directory itself:

```bash
make backup                                  # the whole database
make backup PROJECT_NAME=api                 # one codebase
make backup PROJECT=/home/you/work/api       # the same, resolved by root path
make backup KEEP=20                           # keep more than the seven it keeps by default
make backup KEEP=                            # keep everything, prune nothing
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

Backups rotate: `KEEP` defaults to 7, so each kind keeps its seven newest files
and the older ones go. Each kind rotates on its own, so seven database archives
never crowd out a project's only backup, and `KEEP=` empty keeps everything for
that run. `FILE=<path>` writes somewhere else instead and is never pruned:
rotation only ever touches the timestamped names this scheme produces.

Restoring names the file and nothing else:

```bash
make restore FILE=context-20260819-115420.dump   # replaces the whole database
make restore FILE=api-20260819-115137.sql.gz     # replaces that one project
```

A bare name is looked up in the backup directory, and `make restore` with no
`FILE=` lists what is there rather than guessing at the newest. Both directions
print what is about to be replaced and ask first; `FORCE=1` skips the prompt.
A project restore is atomic - the file carries its own `BEGIN`, the `DELETE`s
that take the old copy away, and `COMMIT` - and a database restore runs in
one transaction with `--exit-on-error`, since `pg_restore` otherwise treats
errors as non-fatal and would report success over a half-restored database.
Either is refused outright while an index job is running.

Worth doing before `make unindex` and before `make clean`: nodes, edges,
hashes and embeddings come back with one `make index`, but plans and manually
written summaries come back from nowhere else. A single-project file written
before plans became global names the table that no longer exists and will not
restore - take the backup again rather than editing the file.

## Database schema

The schema is owned by [goose](https://github.com/pressly/goose), which keeps
`migrations/` and the `schema_migrations` table in step. The `migrate` service
runs to completion before anything else reaches the database, so `make up`
applies whatever is pending on its own - to the database that is already there,
without touching its contents. Nothing that queries the database starts if a
migration fails.

`make db <target>` drives goose directly: `version` for what is applied and what
is pending, `migrate` to apply it now, `new NAME=<slug>` to write the next
numbered file. A migration is plain SQL between `-- +goose Up` and
`-- +goose Down`, and goose runs each one in a transaction.

Two things a migration cannot do for you. `scripts/backup.sh` and
`scripts/restore.sh` name every column of every table explicitly, so a migration
that adds or renames one has to update them in the same commit. And a full
database restored from a backup taken before a migration comes back without its
`schema_migrations` rows - the next `make up` re-applies from the beginning,
which is safe because the migrations are idempotent, and records the version
again.

To remove a single codebase rather than the whole database, use `make unindex` -
everything derived cascades from the row in `projects`, so one `DELETE` there
takes that project's nodes, edges, hashes and embeddings, and nothing of any
other project. Plans are not derived and have no foreign key, so they stay. `make clean` is the other end: it empties the data directory and starts
the database over, asking for confirmation first since it destroys the index
(`make clean FORCE=1` skips the prompt). Both are worth a `make backup` first.

The data directory is emptied from inside the postgres service rather than from
the host: its files belong to the container's postgres uid, so a host-side `rm`
fails on permissions.

The database is a bind mount rather than a volume, so `docker compose down -v`
does not remove it. That is also why a regenerated `POSTGRES_PASSWORD` cannot be
applied on its own: the entrypoint skips initialisation while the directory
holds a database, and every connection is then refused with
`password authentication failed`. Run `make clean` to rebuild the database
around the new password; `migrate` puts the schema back into the empty
directory on the next `make up`.

- `graph_nodes` - one row per file, per code entity (`file_path::name`) and per
  unresolved external import or symbol
- `graph_edges` - typed relations between nodes, unique per
  `(source, target, relation)`
- `code_embeddings` - `vector(1536)` chunks with an HNSW cosine index, not
  populated yet (see `ROADMAP.md`)
- `plans` - one row per plan, keyed on an id unique across the database, with
  `project` as a free-text tag, plus `status` and `type`

A single-project backup taken before migration 0003 carries no `type` column;
restoring it is fine, and the rows come back as `plan`.

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
