# Roadmap

Current state: the stack builds, starts, indexes a mounted codebase into a
file/import graph, and serves that graph to Claude CLI over SSE. What follows is
deliberately deferred work, in the order it should be picked up.

## 1. Tree-sitter AST parsing [COMPLETED]

The `ctxgraph` package (`graphify/src/ctxgraph/`) uses tree-sitter for SH, MD,
MAKE, DOCKERFILE, HCL/TF, YAML, TOML. Programming languages moved to the
upstream graphifyy extractor, which covers more of them and tags every edge
with a confidence; the parsers for TS, TSX, JS, PY, GO, RS are kept as a
fallback but are no longer reached. The split is `GRAPHIFYY_EXTENSIONS` in
`config.py`.

- emits `function`, `method`, `class` and per-language entity nodes, scoped to
  their file as `path::name`
- emits `calls`, `inherits`, `imports` edges, resolved against the entities of
  the same language family in a second pass
- resolves relative import targets to file nodes, and everything else to
  `external_import` / `external_symbol` placeholders
- reads Ansible YAML by its values rather than its syntax: plays, tasks,
  handlers and role variables, plus `includes`, `uses_role`, `depends_on`,
  `reads_vars`, `uses_template`, `uses_file` and `notifies` edges

## 2. Embedding generation

`code_embeddings` exists with a `vector(1536)` column and an HNSW cosine index,
but nothing writes to it.

Open decision: a local model (`fastembed` or `sentence-transformers`, no key
needed, large image) versus an API provider (matches 1536 dimensions directly,
needs a key and network egress from the container). Whichever is chosen, chunk
on the AST node boundaries from step 1 rather than on fixed line windows.

## 3. Semantic search MCP tool

Once embeddings exist, add `search_code_semantic` to
`mcp-server/src/index.ts`, querying `embedding <=> $1` against the HNSW index
and returning the owning nodes. The tool is not worth adding before then, since
it would return nothing.

## 4. Incremental indexing [COMPLETED]

`make index` rewrites every node on every run. A re-index now clears what it
previously derived from each file it visits, but a file deleted from the tree is
never visited again and its nodes stay. Track file content hashes in
`graph_nodes.metadata` to skip unchanged files, and prune nodes whose file is
gone.

## 5. Use public registry for built images

User have to build images each time. Better way to pull images from some
public storage instead of.

## 6. Multi-projects support [COMPLETED]

One database holds every indexed codebase. `projects` names them, every other
table carries a `project` column, and `graph_nodes` is keyed on
`(project, id)`, since `README.md` is a node id in every codebase there is.

`make index PROJECT=/path/to/anything` indexes a tree under a name taken from
its last path segment, and needs nothing inside the target: no checkout of this
repository, no `.env`, no Makefile. A client binds to a project through the
address it connects to (`/mcp/<project>`), and every tool takes an optional
`project` to read a neighbour's graph from the same session. `list_projects`
says what is there.

Edges stay inside one project, which is not a limitation so much as a fact
about the producer: the indexer is handed a single tree and resolves every
target within it, so a cross-project edge has no way to be discovered. Giving
`graph_edges` a second project column would only add one that is always equal.
Relating two codebases needs a resolver that sees both, which is its own piece
of work.

## 7. Git hook for re-indexing

Implement git-hook for run re-indexing for each commit.

## 8. Use some CPU-Driven local model for generate summary

We use dump analyst, better to use some local model
just for this, for reduce token usage.

Model should be part of compose deployment.

## 9. Implement MCP support for Gemini [COMPLETED]

`.gemini/settings.json` registers both servers, and the skill in `skills/` is
registered for Claude and Gemini by `make skill-install`. The transport moved
from SSE to Streamable HTTP (`/mcp`) along the way; `/sse` is still served for
older clients.

## 10. Move the database out of the working tree

`DATA_DIR` defaults to `./pgdata`, which puts the database inside the checkout.
The directory is gitignored, which is exactly what makes it reachable by
`git clean -xfd` - a routine command in a repository that is also a development
tree. Losing the graph is cheap to recover from today, but it stops being cheap
once one database holds every project's graph (item 6).

Default it outside instead, `$XDG_DATA_HOME/claude-context-mcp/pgdata` or
similar, and keep the in-tree path only as an explicit choice.

This is deliberately no protection against `make clean`, which empties
`DATA_DIR` wherever it points and asks before doing so. That target is supposed
to destroy the database; the prompt is the right amount of friction for it.

The password comes with the move. A cluster bakes in the password it was
initialized with, so credentials and data have to share a lifetime: today both
sit in the checkout and die together, while data kept outside it outlives the
`.env` that describes it, and the surviving cluster then refuses a regenerated
password with a plain authentication failure that says nothing about why.

`postgres` publishes no port and is reachable only on the compose network, so
the password guards against one thing: another local process reaching the
bridge address directly. On a single-user machine that is not a threat worth a
generated secret, and a fixed default (`POSTGRES_PASSWORD=context` in
`.env.example`, with the `:?` in the compose file relaxed to a default) removes
the mismatch entirely.

Whichever default is chosen, the mismatch stays reachable by hand, so the
diagnosis is worth more than the default: `make status` should tell "the stack
is down" apart from "the stack is up but this password does not open a cluster
initialized with another one", and name the two ways out (`make clean`, or
`ALTER USER`). Both read as `unreachable` today.

## 11. Incremental extraction for the code half

The Tree-sitter parsers skip a file whose hash is unchanged. The graphifyy pass
does not: it re-extracts the whole code corpus on every run, because an
extraction over a subset loses the edges between the files left out. Its own
per-file cache absorbs most of the cost, but the merge into the database is
still full. Worth revisiting with `graphify.detect.detect_incremental` once the
graph is large enough for it to matter.

The split is visible from the outside: `list_indexed_files` on
`py-net-events-collector` returns seven rows - `README.md`, `Makefile`,
`docker/Dockerfile`, `docker/Dockerfile.test`, `docker/Makefile`, `tests/e2e`
and `.mcp.json` - and not one of the eight `.py` files that are present in the
graph as nodes. `file_hashes` tracks what the Tree-sitter parsers touched and
nothing else, so the one half of the corpus that would most benefit from change
detection is the half that has none.

## 12. Add support for C/C++ (.c, .cpp, .h)

Update `GRAPHIFYY_EXTENSIONS` in `config.py` to include C and C++ extensions to leverage the upstream graphifyy extractor for these languages.

eBPF sources carry a double extension - `bpf/event.bpf.c`, `bpf/event.bpf.h` -
so whatever matches the extension has to read the last suffix rather than
everything after the first dot, or a CO-RE project ends up with its kernel-side
code unindexed while looking as though the language is covered. `.h` belongs in
the set alongside `.c`: it is where the shared event structs live, and those are
the definitions the userspace side is matched against.

## 13. Resolve dotted relative imports

A relative import of one segment resolves to its file node; anything deeper is
flattened into an underscore identifier and recorded as `external_import`. In
`py-net-events-collector`, `from .collector import Collector` lands on
`src/net_events_collector/collector.py` correctly, while `from .db.store import
EventStore` becomes a node called `db_store` and `from ...utils.net import
format_addr` becomes `utils_net`. Four of the five internal imports in that
project are lost this way, and the files they point at - `db/store.py`,
`db/models/event.py`, `utils/net.py`, `utils/proc.py` - end up with no incoming
edges at all, unreachable by `shortest_path` and indistinguishable from dead
code.

This is not an unusual layout. A package that keeps its modules in subpackages
is the ordinary case, and for every such project the graph degrades into the
`contains` tree plus a scattering of placeholder nodes named after the import
path that was not walked. Python goes through the upstream graphifyy extractor,
per `GRAPHIFYY_EXTENSIONS` in `config.py`, so the resolver to fix is there
rather than in `ctxgraph`. The already-indexed `py-net-events-collector` graph
is a small enough reproducer to work against directly.

## 14. Make parser emits targets without edges

`Makefile::all`, `::check`, `::install`, `::clean` and `::test` are all present
as nodes carrying `file_path: Makefile`, but `get_code_graph_neighbors` on
`Makefile` returns an empty list, where the same call on any `.py` file returns
the entities it contains. The nodes are written and the `contains` edges are
not, which makes the targets findable by name and invisible to every question
about structure.

Two smaller repairs belong with it. `.PHONY` is emitted as a target when it is
a directive naming other targets, so every indexed Makefile carries one false
node. And a prerequisite list is dropped entirely: `all: check $(OBJS)` says
`all` depends on `check`, which is exactly the kind of edge the graph exists to
hold.

## 15. One command should onboard a codebase

Indexing a tree and making an agent able to use the result are two separate
commands today, and only the first one takes a path. `make index PROJECT=/x`
builds the graph; `make skill-install AGENT_ROOT=/x` renders the skill; and the
registration that connects the two is written by hand. Onboarding
`py-net-events-collector` took both commands plus a hand-written `.mcp.json`,
and the hand-written half is where every mistake was. One command -
`make install PROJECT=/x` - should leave that codebase ready: graph built,
skill in place, both agents configured, both agent files saying to use it.

The coupling runs both ways, and both directions are the same failure. A graph
with no registration gives an agent instructions for tools it cannot reach; a
registration with no graph gives it tools that answer nothing. So `install`
runs `index` for the same tree, and does it first - the configuration it writes
names a project that has to exist in `projects` for any of it to resolve.

That changes what the install costs. `skill-install` is local, quick and needs
nothing running, which is why it refuses only `sudo`. An `install` that indexes
needs the stack up and Docker reachable, and it takes as long as the extraction
does. Worth keeping the pure-configuration path reachable under its own name
for the case where the graph is already built, rather than making a re-render
of one file pay for a full re-extraction.

Four things to render, all derivable from `PROJECT`:

**The skill.** What `skill-install AGENT_ROOT=` already does. Folding it in
means `index` grows the same `@MAKE@` / `@ROOT@` substitution, or calls the
existing target with `AGENT_ROOT=$(PROJECT)`.

**`.mcp.json` for Claude, `.gemini/settings.json` for Gemini.** The `context`
address is `http://localhost:$(MCP_PORT)/mcp/$(notdir $(PROJECT))`, which is
already known at index time - it is the same name the indexer records in
`projects`. The two files disagree on schema (`url` versus `httpUrl`, and
Gemini takes `trust`), so this is two renderers over one pair of values.
Neither file can be overwritten: the target may hold servers of its own, so
merge the one key, and leave it alone when it is already there and matches.

**`CLAUDE.md` and `GEMINI.md`.** A skill is offered to the agent, not imposed
on it; the agent files are what actually oblige it. Both need a section saying
the graph is the first place to look for structure - what uses this, how these
connect, what a change reaches - and that the file should be opened after the
graph has narrowed it down, not instead. Add the section when the file does not
exist, and update it in place when it does, which means the block needs a
marker to find itself by rather than being appended on every run.

The `graph` server is the awkward one and may be better left out. Its command
assumes the working directory is this repository twice over: without
`-f <this repo>/docker-compose.yaml` compose finds no file at all, and without
`-p` it invents a project name from the target directory instead of the
`ctx-<codebase>` name the Makefile derives in `COMPOSE_PROJECT_NAME`, so it
attaches to a second, empty stack. Both are renderable - `@COMPOSE@` alongside
`@MAKE@` and `@ROOT@` - but the deeper problem is not. That server reads the
file written at index time, which holds whichever project was indexed last and
has no notion of projects at all, so a second codebase gets right answers only
until the next `make index` on another tree. Rendering a registration that goes
quietly wrong is worse than rendering none: either it carries that caveat where
the agent will read it, or `index` registers only `context` and the skill drops
the mention when it is not being installed here.

The git hook of item 7 is deliberately not part of this. It looks like the same
onboarding question, but it is a standing change to how the target repository
behaves on every commit, where everything above is configuration the agent
reads. Installing it should stay a separate decision, made by name.

## 16. Add support for Puppet (.pp, .erb, .epp) and Ruby (.rb)

Update `GRAPHIFYY_EXTENSIONS` in `config.py` to include Puppet and Ruby extensions to leverage the upstream graphifyy extractor for these languages.

## 17. Add support for JSON (.json)

## 18. Integrate Git commit history at index time

Extend the indexing process (`make index`) to extract relevant commit history from the project's Git repository using `git log`. Store commit metadata (short/long hashes, commit message) and the list of files modified in each commit to provide temporal context and evolution details for the code graph during the initial indexing phase.
