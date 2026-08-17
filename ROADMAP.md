# Roadmap

Current state: the stack builds, starts, and indexes a mounted codebase into a
graph of files, entities and their relations, which it serves to Claude CLI and
Gemini CLI over Streamable HTTP (`/mcp`, with `/sse` kept for older clients).

Extraction has two producers over one corpus. The upstream graphifyy extractor
reads the programming languages listed in `GRAPHIFYY_EXTENSIONS`
(`graphify/src/ctxgraph/config.py`), including C and C++ and Ruby; the
`ctxgraph` parsers read the infrastructure formats it does not - SH, MD, MAKE,
DOCKERFILE, HCL/TF, YAML, TOML, Ansible, and Puppet with its ERB and EPP
templates. One database holds every indexed codebase: `projects` names them,
every other table carries a `project` column, and a client binds to one through
the address it connects to (`/mcp/<project>`). Re-indexing skips a file whose
content hash is unchanged and prunes what a deleted file left behind.

What follows is deliberately deferred work, in the order it should be picked up.

## 1. Embedding generation

`code_embeddings` exists with a `vector(1536)` column and an HNSW cosine index,
but nothing writes to it.

Open decision: a local model (`fastembed` or `sentence-transformers`, no key
needed, large image) versus an API provider (matches 1536 dimensions directly,
needs a key and network egress from the container). Whichever is chosen, chunk
on the AST node boundaries rather than on fixed line windows.

## 2. Semantic search MCP tool

Once embeddings exist, add `search_code_semantic` to
`mcp-server/src/index.ts`, querying `embedding <=> $1` against the HNSW index
and returning the owning nodes. The tool is not worth adding before then, since
it would return nothing.

## 3. Use public registry for built images

User have to build images each time. Better way to pull images from some
public storage instead of.

## 4. Git hook for re-indexing

Implement git-hook for run re-indexing for each commit.

## 5. Move the database out of the working tree

`DATA_DIR` defaults to `./pgdata`, which puts the database inside the checkout.
The directory is gitignored, which is exactly what makes it reachable by
`git clean -xfd` - a routine command in a repository that is also a development
tree. Losing the graph is cheap to recover from today, but it stopped being
cheap once one database began holding every project's graph.

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

## 6. Incremental extraction for the code half

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

## 7. Resolve dotted relative imports

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

## 8. Cross-file edges for Ruby

Ruby extraction yields classes, instance and singleton methods, and a call
graph inside a file. It yields no modules, no `inherits` edge, and nothing from
`require`: upstream walks only the `class` node and emits only `calls`, and the
cross-file import pass it runs for Python is not run for Ruby. A Ruby graph is
therefore a set of unconnected per-file islands, which is the same shape of
defect as item 7 and fixed in the same place - the upstream extractor rather
than `ctxgraph`.

## 9. Enhanced qualified variable resolver for Puppet

`PuppetParser` currently misses qualified variable references like
`$::package_repo::user` or `$package_repo::base_dir` between manifests (e.g.,
`keeper.pp` to `init.pp`), resulting in disconnected nodes and empty neighbor
queries.

- Extend `PuppetParser` to parse qualified global/module variable usages
  (`$::scope::var`).
- Emit `reads_var` / `references` edges from the consuming manifest to the
  defining class/manifest during the edge-resolution pass.

## 10. One command should onboard a codebase

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
graph has narrowed it down, not instead. Spell that out as a two-pass rule the
agent can be held to: query the graph, narrow to the nodes that matter, and
only then read those files, rather than sweeping whole files to get oriented.
The same section is where the obligation to persist what was synthesized
belongs - summaries back into the graph, patterns into the conventions store of
item 14 - so that the second agent to ask a question reads the answer instead
of deriving it again. Add the section when the file does not exist, and update
it in place when it does, which means the block needs a marker to find itself
by rather than being appended on every run.

The `graph` server is the awkward one and may be better left out. Its command
assumes the working directory is this repository: without
`-f <this repo>/docker-compose.yaml` compose finds no file at all. That path is
renderable - `@COMPOSE@` alongside `@MAKE@` and `@ROOT@` - and it is now the
only thing needed, because `docker-compose.yaml` pins the project name itself
with a `name:` key, so compose no longer invents one from the target directory
and `-p` can be left off. The deeper problem is not
renderable. That server reads the file written at index time, which holds
whichever project was indexed last and has no notion of projects at all, so a
second codebase gets right answers only until the next `make index` on another
tree. Rendering a registration that goes quietly wrong is worse than rendering
none: either it carries that caveat where the agent will read it, or `index`
registers only `context` and the skill drops the mention when it is not being
installed here.

The git hook of item 4 is deliberately not part of this. It looks like the same
onboarding question, but it is a standing change to how the target repository
behaves on every commit, where everything above is configuration the agent
reads. Installing it should stay a separate decision, made by name.

## 11. Add support for JSON (.json)

## 12. Integrate Git commit history at index time

Extend the indexing process (`make index`) to extract relevant commit history
from the project's Git repository using `git log`. Store commit metadata
(short/long hashes, commit message) and the list of files modified in each
commit to provide temporal context and evolution details for the code graph
during the initial indexing phase.

## 13. Cross-project lookups and relations

Edges stay inside one project, which is not a limitation so much as a fact
about the producer: the indexer is handed a single tree and resolves every
target within it, so a cross-project edge has no way to be discovered. Giving
`graph_edges` a second project column would only add one that is always equal.
Relating two codebases needs a resolver that sees both, which is its own piece
of work. Useful for infrastructure projects, e.g. ansible, puppet, terraform.

## 14. Semantic conventions and architectural patterns store

AST graphs track strict syntax dependencies (imports, class inheritance), but
remain blind to cross-file conventions, deployment patterns, and configuration
styles (e.g., comparing 4-5 Puppet modules, ERB template standards, or Hiera
data structures) per project.

- Create a `conventions` table in PostgreSQL keyed by
  `(project, category, scope)` containing `summary`, `sample_files` (JSON array
  of file paths), and `metadata`.
- Implement `save_convention` MCP tool: allows agents (e.g., Gemini Flash or
  Claude) to write synthesized patterns after analyzing 3-5 representative
  files.
- Implement `get_conventions` / `search_conventions` MCP tool: lets agents query
  stored project patterns (e.g., `category="docker_compose"`) before reading raw
  code/templates.

## 15. Summaries produced at index time

Summaries are written by whichever agent happens to ask, which is the most
expensive way to get them. A small CPU-driven local model, deployed as part of
the compose stack, should produce them instead, purely to cut token usage.

The templates and data files need the same treatment for a different reason:
ERB/EPP templates and Hiera YAML/JSON have no AST call trees, so an agent that
wants to know what a template takes has no option but to read the file whole.
Add a post-processing pass during indexing for `.erb`, `.epp` and
`.yaml`/`.json`, and store the extracted variable lists, required env keys and
structural schemas in `graph_nodes.metadata` / `summary`, so `search_code_nodes`
answers with them and the raw read is not needed. That half is structural and
needs no model at all; the model is for the prose summary of a node.

## 16. Fix graph visualization error 503 for large graphs

The endpoint `http://localhost:3001/graph?project=<project>` returns a 503
error for large projects (e.g., Puppet with 34k+ nodes). Implement either:

- Server-side reduction/sampling/clustering for the visualization.
- A `--no-viz` option to skip expensive rendering.
- Client-side pagination/lazy loading.

## 17. Web interface for project status

The viewer draws the graph and nothing else. There is no page that answers what
is indexed, when it was last indexed, and what is known about a project beyond
its nodes and edges.

Start with the plans, which are the most immediately useful and already stored:
`save_plan` writes them and `get_plans` reads them back, but nothing displays
one. Per project, list the plans with their `plan_id`, status and date, and open
the full text of the one that is clicked.

Same page, as each lands: the conventions of item 14, and the template and data
summaries of item 15. Everything that is stored about a project but does not fit
the graph belongs on it.
