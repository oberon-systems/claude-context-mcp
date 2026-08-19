# Roadmap

Current state: the stack builds, starts, and indexes a mounted codebase into a
graph of files, entities and their relations, which it serves to Claude CLI and
Gemini CLI over Streamable HTTP (`/mcp`, with `/sse` kept for older clients).

Extraction has two producers over one corpus. The upstream graphifyy extractor
reads the programming languages listed in `GRAPHIFYY_EXTENSIONS`
(`graphify/src/ctxgraph/config.py`), including C and C++ and Ruby; the
`ctxgraph` parsers read the infrastructure formats it does not - SH, MD, MAKE,
DOCKERFILE, HCL/TF, YAML, TOML, JSON, Ansible, and Puppet with its ERB and EPP
templates. A Puppet manifest resolves past `include` and `inherits`: `reads_var`
reaches the class whose parameter a `$::a::b` reference reads, and `references`
reaches the defined type a resource declares.
One database holds every indexed codebase: `projects` names them,
every other table carries a `project` column, and a client binds to one through
the address it connects to (`/mcp/<project>`). Re-indexing skips a file whose
content hash is unchanged and prunes what a deleted file left behind.

What follows is deliberately deferred work, ranked from simple to complex.

## 1. Move the database out of the working tree [COMPLETED]

## 2. Use public registry for built images [COMPLETED]

## 3. Drop a project from the graph [COMPLETED]

## 4. A re-index must invalidate the extractor cache

`redirect_extractor_cache()` (`graphify/src/ctxgraph/interop.py:55-71`) points
graphifyy's per-file cache at `graphify-out/cache`, and compose mounts that on one
named volume - `graph-out:/app/graphify-out` - shared by every indexing run of
every project. The cache is keyed by file content and scoped to nothing else, and
a hit means the file is skipped entirely: no node is written for it at all, rather
than a stale one being kept.

So a re-index is not authoritative today. Indexing `kurum` selected 967 files and
produced 924 file nodes; the 43 missing ones came back in one direction only, so
the selection was right and the extraction was not. Thirty-three were zero-byte
`__init__.py`. The other ten split into two causes, both of them the cache: seven
non-empty modules of `eBPF/py-net-events-collector` - `collector.py`, `store.py`,
`event.py` and the rest - had been extracted under a project that was since
dropped from the database, and three were paths whose content is byte-identical to
another file in the tree.

- Key the cache by project and path as well as by content hash, so one tree's
  extraction cannot answer for another's.
- Drop a project's cache entries with the project, which the drop item above has
  to do as part of removing it.
- Give `make index` a way to force full re-extraction for the tree it is handed,
  for the case where the cache is suspected rather than known to be wrong.
- Report the difference at the end of a run: files selected, nodes written, and
  what accounts for the gap - empty file, duplicate content, unreadable, over
  `MAX_FILE_BYTES`. A run that silently writes fewer nodes than it selected files
  currently looks identical to a clean one.

The invariant behind all of it is the one every agent instruction relies on:
after `make index`, the graph describes the tree. Agents are told to trust the
graph instead of re-deriving structure from the filesystem, and that is only
sound while a re-index is authoritative - so this is a correctness bug, not the
performance work of the incremental-extraction item further down.

## 5. Read the shebang of extension-less files

`parser_class()` (`graphify/src/ctxgraph/parsers/registry.py`) picks a parser by
extension, or by an exact name for `Dockerfile` and `Makefile`. A file with no
extension gets no parser at all, whatever its first line says. In a tools
repository that is not a corner case: `kurum` holds 86 shell scripts named
`check-dns`, `add-host`, `build`, `run` - shebang present, suffix absent - each
of which has to be named in `.ctxkeep` by path just to be seen, and then lands
as a bare file node with no functions and no calls.

- Sniff the first line when the extension is empty and map the interpreter onto
  the parser already registered for it (`sh`, `bash` -> `BashParser`).
- Keep today's behaviour as the fallback: a file node carrying a summary from
  its leading comment, which `leading_comment()` already yields since it skips
  the `#!` line. That is the "treat it as text" case and it needs no new code -
  only that nothing rejects the file earlier.
- `is_default_source()` decides by name, so a project without a `.ctxkeep` never
  reaches the sniff. Gating on the executable bit costs no read and covers most
  of it - 79 of those 86 scripts - but not all; reading the first two bytes of
  every extension-less candidate is the honest version.
- An interpreter whose language lives in `GRAPHIFYY_EXTENSIONS` (`python`,
  `ruby`, `php`) is the harder half: that extractor selects by extension too, so
  routing to it means handing it a hint or leaving those files on the text
  fallback. Worth deciding by name rather than by omission.

## 6. Fix graph visualization error 503 for large graphs

The visualization endpoint returns a 503 error for large projects (e.g., 30k+
nodes). Implement server-side reduction, sampling, or client-side lazy loading
to handle massive graphs.

## 7. One command should onboard a codebase

Simplify onboarding with a single command: `make install PROJECT=/path`. It
should build the graph, install the skill, and configure both Claude
(`.mcp.json`) and Gemini (`.gemini/settings.json`) agents. This ensures the
registration, skill, and graph are correctly coupled without manual steps.

## 8. Git Integration (Hooks & History)

Implement deep Git integration to provide temporal context:

- **Git hooks:** Automatically trigger re-indexing on every commit.
- **Commit History:** Extract `git log` metadata (hashes, messages, modified
  files) during indexing to enrich the code graph with evolution details.

## 9. Schema changes need a migration path

`init-db/01-init.sql` is replayed by the postgres entrypoint only when the data
directory is empty, and nothing else in the tree ever runs DDL - there is not one
`ALTER TABLE` in it. So a schema change is reachable exactly one way: `make
clean`, which empties the database and takes every project's graph with it.

This already blocks work on this list. The `suggestions` table of the gaps item,
the `conventions` table of the memory item, and any promotion of a `metadata` key
to a real column all need a running cluster to change shape. The first item here
makes it sharper rather than easier: once the data directory lives outside the
checkout it outlives the checkout, and a cluster built by an older release starts
meeting code from a newer one.

- Numbered files in `init-db/`, applied in order and recorded in a
  `schema_migrations` table. Apply them on server start rather than from the
  postgres entrypoint, which is what ties the current behaviour to an empty
  directory.
- Idempotent by construction - `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`. The existing file already reads that way and can become migration 001
  unchanged.
- `make status` should report which migration the database is at. A cluster one
  migration behind the code is precisely the failure it cannot diagnose today.

The `summary_source` key is what surfaced this: the schema documented it as
though it were a column while every reader and writer treats it as a key inside
`metadata`. The comment is fixed, but promoting it for real - which is what would
make it indexable - is the change that has nowhere to go.

## 10. Structural Docker Compose Parsing

Implement a specialized parser for `docker-compose.yaml` and `compose.yaml`
to extract services, networks, volumes, and dependencies (`depends_on`) as
architectural nodes and edges, rather than treating them as generic YAML.

## 11. Terraform and Terragrunt produce nodes and no edges

`HCLParser` (`graphify/src/ctxgraph/parsers/languages.py`) declares an
`ENTITY_QUERY` and nothing else. There is no `RELATION_QUERY`, and nothing in
`resolution.py` knows the format, so every `.tf`, `.tfvars` and `.hcl` file in
an indexed tree yields flat `block` nodes that the graph can list and never
traverse. It is the largest such gap left: HCL is the default language of an
infrastructure repository, and the whole point of one is the wiring.

Measured on `homelander` (1593 files indexed, `.terragrunt-cache` and
`.terraform` excluded). Of its 6487 edges, 6472 are `contains` from a file to
its own blocks and the remaining 15 are `uses_role` from Ansible. The 5460
`block` nodes have no edges at all between them:

    get_code_graph_neighbors(
      'terragrunt-infra/live/WT/production/aws/us-east-1/ec2-instances/terragrunt.hcl')
    -> include.common, include.root, locals - three `contains`, nothing else

Both `include` blocks name a file this same tree holds. Neither points at it.

What a `RELATION_QUERY` would resolve, counted as references rather than files,
since the reference count is what says whether the work pays:

| Reference                                  | Count | Resolves to                                    |
| ------------------------------------------ | ----: | ---------------------------------------------- |
| `source =` in `module` / terragrunt blocks |   495 | 181 local paths to a module dir; rest registry |
| `include` plus `find_in_parent_folders()`  |   590 | the terragrunt configuration inheritance chain |
| `read_terragrunt_config()`                 |    45 | `env.hcl`, `common.hcl`, `region.hcl` variants |
| `templatefile()` and `file()`              |   197 | the `.tftpl`, `.tpl` and `.j2` files alongside |
| `terraform_remote_state` and `dependency`  |    76 | another stack's outputs, across state files    |

Against 1366 `resource`, 463 `data`, 928 `variable` and 252 `output` blocks
spread over 265 `terragrunt.hcl`, 35 `env.hcl`, 29 `common.hcl`, 28 `region.hcl`
and 6 `root.hcl` files.

**The literal half first.** `source = "../../modules/kubernetes"`,
`include { path = find_in_parent_folders() }` and `templatefile("${path.module}/x.tpl")`
are plain strings resolvable against the tree by path, and the machinery is the
one `resolution.py` already applies to Puppet's `template()`. A registry address
is not a path but is worth an ecosystem-tagged `depends_on terraform:hashicorp/aws`,
the same convention `JsonParser` uses for npm - 20 of the 495 sources in this one
tree are `hashicorp/aws` alone, so the reverse question "what pins this provider"
becomes answerable.

**Interpolation is the hard half**, and this tree really contains

    source = "${get_terragrunt_dir()}/../../..////live/web-apps/${path_relative_to_include()}"

which is the same failure as the Puppet `puppet:///modules/${module_name}/...`
case: the constant prefix resolves and the rest does not. The same fallback
applies - emit an edge to the directory node the constant prefix reaches, rather
than dropping the reference. Doing this for `source` alone covers the terragrunt
pattern where one `_envcommon` file is included by every stack in a region.

Left out on purpose: Groovy `Jenkinsfile` (2 files here, and a `Jenkinsfile`
names stages rather than references that resolve anywhere), and `.repo`,
`.mirrorlist` and `.sysconfig`, which are data with nothing to follow - the same
grounds on which item 12 excludes them.

## 12. Parsers the indexed trees are already asking for

Three formats account for most of what a `.ctxkeep` has to admit today and no
parser then reads. Counted over `kurum`, with vendored collections and
virtualenvs excluded:

| Format                    | Files | What it would connect                                    |
| ------------------------- | ----: | -------------------------------------------------------- |
| RPM spec `*.spec`         |    63 | 172 `Requires`/`BuildRequires`, 75 `Source*`, 11 subpkgs |
| Python manifests          |    67 | every dependency of every tool in the tree               |
| systemd units `*.service` |     7 | `ExecStart` to a binary, `EnvironmentFile` to its config |

**Python manifests first.** The cheap one, and it needs no new grammar.
`JsonParser` already records a dependency under its ecosystem (`npm:express`,
`composer:monolog/monolog`), and `requirements.txt` is a line-oriented list of
the same thing under `pypi:`. Resolve `-r other.txt` and `-c constraints.txt` as
file relations, drop environment markers, extras and hashes from the name, and
read `install_requires` out of `setup.cfg` as INI. 47 requirements files, 7
constraints files, 12 `setup.cfg`, one `Pipfile` - nothing else in the format
carries structure worth a node.

**RPM spec is where the payoff is.** A spec is the only file in a packaging
repository that says what a package is made of, and every reference in it
resolves somewhere:

- `Requires`, `BuildRequires`, `Requires(pre)`, `Provides`, `Obsoletes` ->
  `depends_on rpm:<name>`, the same ecosystem-tagged convention as npm.
- `Source0..N` and `Patch0..N` -> the file in the tree, when the tree holds it.
  This is the edge nothing else can produce: it is what ties a package node to
  the sources, units and sysconfig files sitting next to it.
- `%package -n` -> a subpackage node under the spec, so `%files -n` sections
  attach to the right one rather than merging.
- `Summary:` is a one-line description written by hand. Take it as
  `graph_nodes.summary` directly - no model, no heuristic, better than anything
  the summarizer of the index-time item could infer.
- `%prep`, `%build`, `%install` and the scriptlets are shell. Hand the body to
  the existing `BashParser` rather than writing a second shell reader; the macro
  expansions it will not understand (`%{buildroot}`, `%{_bindir}`) are worth
  leaving as literal text rather than resolving.

**systemd units close the chain.** Small in count, cheap to read - INI with a
known key set - and they connect what the spec installs to what actually runs it:
`ExecStart` names the binary a `%files` section shipped, `EnvironmentFile` names
the sysconfig the same spec installed, `After`/`Requires`/`WantedBy` name other
units. Worth doing in the same pass as the spec parser, since neither half is
interesting without the other.

Left out on purpose: nginx configuration (5 files here, and `include` /
`upstream` / `proxy_pass` would resolve, but the grammar is a real one and the
volume does not yet justify it), `.j2` templates (3 files outside the vendored
collections here, 10 more in `homelander` - low volume, and Ansible's
`uses_template` edge already reaches them as file nodes without a parser
reading inside), `.repo`, `.sysconfig` and `.mirrorlist` (data, no references
to follow), and the IDE and linter dotfiles, which say nothing about the code.

## 13. Language Extractor Improvements (Python/Ruby)

Improve the cross-file resolution in the upstream `graphifyy` extractor:

- **Python:** Resolve dotted relative imports (e.g., `from .db.store import EventStore`)
  to their actual file nodes rather than flattening them into placeholder nodes.
- **Ruby:** Add support for `require` resolution, modules, and `inherits` edges
  to connect isolated per-file islands into a unified graph.

## 14. Incremental extraction for the code half

Optimize the `graphifyy` pass to support incremental extraction. Currently,
while Tree-sitter parsers skip unchanged files, the code pass re-extracts the
whole corpus. Revisiting `graphify.detect.detect_incremental` will significantly
speed up re-indexing for large codebases.

## 15. Record the gaps an agent hit, instead of losing them

The recap convention in `CLAUDE.local.md` already obliges an agent to close a task
with what the graph could not answer and what would fix it - a missing summary, a
file type nothing indexes, an index older than the tree, a question the tools
cannot express. That report ends its life in a transcript. Nobody reading this
repository next week sees it, and the same gap gets rediscovered by hand every
time it is hit.

- Store them: a `suggestions` table keyed by `(project, category)` holding the
  observation, the lever it moves - token usage, coverage, or indexing time - and
  the concrete change proposed. A `report_gap` MCP tool writes one, `get_gaps`
  reads them back per project.
- Aggregate rather than accumulate. The same gap reported by ten sessions is one
  finding with a count of ten, and the count is the whole signal: it says which
  missing parser or absent tool is actually costing tokens, as against which one
  was inconvenient once.
- Surface them on the dashboard of the project status item, next to plans and
  conventions. A backlog that is only queryable through an MCP tool is a backlog
  the maintainer never reads.
- The instruction itself is not portable today. It lives in this repository's
  `CLAUDE.local.md`, which is not rendered into an indexed project, so an agent
  working anywhere else never reports anything. Either the skill carries the rule
  into the projects it is installed in, or the tool's own description does - the
  latter needs no rendering step and reaches Gemini identically.

## 16. Semantic Search & Embeddings

Enable vector similarity search alongside the graph:

- **Generation:** Choose between local models (e.g., `fastembed`) or API
  providers for `vector(1536)` embeddings, chunking on AST node boundaries.
- **Tool:** Add `search_code_semantic` to `mcp-server` to query the HNSW index
  and return owning nodes.

## 17. Agent Memory, Knowledge & Conventions

Create a persistent store for non-AST project details:

- **Conventions:** Store synthesized architectural patterns and styles (e.g.,
  Docker Compose standards or Puppet module patterns).
- **Knowledge Base:** Allow agents to store and retrieve persistent knowledge
  about best practices and functional patterns.
- **Tools:** Implement `save_convention`, `store_knowledge`, and `search_memory`
  MCP tools with vector search support.

## 18. Index-Time Summarization

Produce node summaries during the indexing phase to reduce agent token usage:

- **Local LLM:** Use a small CPU-driven model in the compose stack to generate
  prose summaries for nodes.
- **Structural Parsing:** Extract variable lists and schemas from templates
  (ERB/EPP) and data files (YAML/JSON/Hiera) into `graph_nodes.metadata` so
  agents don't have to read raw files.
- **Backfill needs a way to enumerate the gaps.** `search_code_nodes` cannot
  filter on `summary IS NULL` - it matches `name`/`id` only
  (`mcp-server/src/index.ts:397`) - so an agent told to fill in the missing
  summaries fills in whatever it happened to touch instead of what is actually
  missing. `list_nodes_without_summary(project, type?, path prefix?)` makes that
  systematic, and after item 23 it is also where a wiped manual summary would
  show up.
- **The fallback summary reads like a summary and answers nothing.** With no
  title and no leading comment, `extract_summary`
  (`graphify/src/ctxgraph/summaries.py:69-79`) names what the file declares, so
  every HCL file in an infrastructure tree comes back as
  `block: locals, dynamic.alias, +2 more`; a thin comment is worse still, since
  `# Records` became the entire summary of a 205-line Route53 zone file. Both are
  non-null, so the nullness filter above would never surface them, and an agent
  that trusts the field opens the file anyway. Observed on `homelander`: to say
  how `tf-modules/r53-entries` maps YAML keys onto Route53 record attributes,
  every entity node of the module was summary-less and both file summaries were
  declaration lists, so the module was read in full - and the summaries that
  answer the question were written by hand afterwards.

## 19. Project Status Web Interface

Expand the viewer into a project dashboard. Beyond the graph, it should display:

- Project plans (synced with `save_plan`/`get_plans`).
- Stored conventions and architectural patterns.
- Indexing metadata (last run, node/edge counts).

## 20. Shared records that belong to no single project

Everything stored is scoped to one codebase. `graph_nodes`, `project_plans` and
`file_hashes` all carry a `project`, and `get_plans` answers for whichever project
the session is bound to. Right for a graph, wrong for a procedure:
`ctx-file-selection-bootstrap` - generate `.ctxkeep` / `.ctxignore` for a
repository, verify the selection by simulation, record the formats no parser
reads - applies to every tree there is, and sits under `claude-context-mcp` only
because a plan has to sit somewhere. An agent in another repository finds it just
by being told which project name to pass.

- Reserve a project standing for all of them, `_common`. Plans land there when
  they are not about one codebase, and later the conventions and knowledge of the
  memory item do too.
- `get_plans` merges `_common` into whatever project it was asked about, so the
  mandatory active-plan check finds a shared procedure without having to know it
  exists. Same for the convention and knowledge readers when they arrive.
- `projects` does not admit such a row today: `root_path` is `NOT NULL UNIQUE`
  and there is no tree behind `_common`. Either a sentinel path or a `virtual`
  column, and the second needs the migration item above.
- Reserve the name against a directory that happens to be called `_common`,
  since a project name is derived from the last segment of its path.
- Then move `ctx-file-selection-bootstrap` to `_common` and remove it from
  `claude-context-mcp`, which needs the `drop_plan` of the project-drop item.
  Until both land the plan stays put and `CLAUDE.local.md` keeps naming the
  project to ask.

Cheaper than the two items around it, and placed here by subject rather than by
cost: it is the cross-project question - what does not fit inside one project's
scope - answered for records instead of for edges.

## 21. Cross-project lookups and relations

Implement a resolver that can discover and link relations between different
indexed codebases. This is essential for infrastructure projects where one
repository (e.g., Ansible) manages another (e.g., a web service).

## 22. Code-half node ids collide across files, and the first one wins

`import_extraction()` maps graphifyy's ids onto ours through one dictionary
keyed by their id alone (`graphify/src/ctxgraph/interop.py:145-183`). Their ids
repeat across files, so every later node with an id already taken resolves to
the _first_ path that claimed it, and its edges are written against that path.
The comment there says this stops an edge from being silently retargeted; it is
what causes the retargeting. `collisions` is counted and logged once per run at
INFO as "graphifyy reused N node ids across files", which reads like harmless
deduplication rather than a graph that now asserts something false.

Measured on `kurum`, which holds four `collector.py` and many `__main__.py`:

- `get_code_graph_neighbors('alertmanager/handlers/clickhouse/src/clickhouse_handler/collector.py')`
  returns `contains` edges to entities of two other files -
  `alertmanager/handlers/oncall/src/oncall_handler/collector.py::AppState` and
  `misc-tools/py-ccu-collector/src/ccu-collector/server/collector.py::to_int()`
  among them - plus the imports of a third (`ctypes`, `ctypes_util`,
  `db_models_event` belong to `eBPF/py-net-events-collector`).
- The three other `collector.py` file nodes return `[]`. So do the
  `__main__.py` nodes.
- `shortest_path` from `.../ccu-collector/server/__main__.py` to
  `.../ccu-collector/server/collector.py` finds no path, though the first
  imports `Storage` from the second.

Nodes themselves are fine: `upsert_file_node` is keyed by `source_file` and an
entity id carries path, label and location. Only the id map is ambiguous, and
only for the producer that reads the programming languages - the `ctxgraph`
parsers resolve against paths and do not share this bug. The asymmetry is what
makes it hard to notice: a Puppet manifest answers this question correctly, so
an empty answer on a Python file reads as "nothing to connect" rather than as
"the edges went to a namesake".

- Key the map by `(their_id, source_file)`. Their nodes carry `source_file`, so
  the ambiguity is resolvable here without touching the extractor.
- An edge endpoint that resolves to no unique path should stay an external
  placeholder, which is already what an unknown target becomes. Binding it to
  whichever file was indexed first is the worse of the two.
- Report collisions per file rather than as one total, and at WARNING: the count
  is the number of files whose edges are now wrong, not a housekeeping figure.
- Check the same question for `node_id()` file nodes (`interop.py:91-108`): the
  branch that recognises a file by `label == basename(source_file)` is correct,
  but it is the point where two same-named files become indistinguishable if the
  label is ever used without its path.

This is a correctness item, like the extractor-cache one: an agent is told to
trust the graph instead of reading the tree, and here the graph states that one
service contains another service's functions.

## 23. Manual summaries do not survive a re-index for code entities

The contract stated in `CLAUDE.md` and in the skill is that a summary written
through `save_node_summary` is marked manual and outlives a re-index. That holds
for file nodes and not for entities. `clear_producer_artifacts()`
(`graphify/src/ctxgraph/storage.py:298-320`) deletes the producer's entity nodes
and is called unconditionally on every code-half run
(`graphify/src/ctxgraph/indexer.py:195`), before the extraction is imported. The
`ON CONFLICT` branch that preserves a manual summary (`storage.py:113-122` and
`:273-283`) only fires when a row is still there, so a deleted-then-reinserted
entity comes back with `summary_source: auto` and an empty summary. The
docstring is explicit that file nodes are kept for exactly this reason; entities
were left out of it.

Second half of the same problem: an entity id embeds its line number
(`_KEY_TEMPLATE` with `source_location`), so
`.../collector.py::to_int()@L202` becomes `@L215` after an edit above it. Even
with the delete fixed, a manual summary would be preserved onto an id nothing
points at any more, and the re-parsed entity would arrive summary-less next to
it.

Observed while filling in the summaries of one module: eleven were saved, ten of
them on entities, and the next `make index` on that tree drops all ten. An agent
following the instruction to backfill missing summaries is doing work that
evaporates, and the instruction to prefer summaries over reading files gets
quietly less true with every re-index.

- Preserve manual summaries across the producer wipe: read the manual rows for
  the tree before deleting, re-apply them after the import. Cheapest fix, keeps
  the current id scheme, and one query bounded by the number of manual rows.
- Or exclude `metadata ->> 'summary_source' = 'manual'` from the delete and
  prune such rows by a separate rule - a manual summary on an entity that no
  longer exists in the source is exactly the case the delete was meant to catch,
  so it needs an answer either way.
- Move the line number out of the id into `metadata`, making the identity
  `path::qualified_name`. This is the version that also fixes `save_node_summary`
  being addressed at a moving target, and it changes stored ids, so it wants the
  migration path of item 9.
- Until one of them lands, say so where the promise is made: the skill and
  `CLAUDE.md` should say that only file-node summaries are durable today.

Open decision: whether a manual summary should survive the entity disappearing
from the source at all, or be reported as stale and dropped. The answer decides
whether the fix is "preserve" or "exclude from the delete".

## 24. Lexical search over stored node content

`search_code_nodes` matches identifiers only - `WHERE project = $1 AND (name
ILIKE $2 OR id ILIKE $2)` (`mcp-server/src/index.ts:397`).
`graph_nodes.content` (`init-db/01-init.sql:37`) is written by both producers and
read by nobody, so a question whose subject lives in a value rather than in a
name has to leave the graph - and the first `grep` of a session is usually the
point after which the graph stops being used at all.

Two field cases, both from trees where the value _is_ the wiring:

- `puppet`: the link "which matching does this collector poll" exists only as a
  port number. `hieradata/role/ccu_collector.yaml` sets
  `WT_MRPC_ADDR=http://matching.pwt:12347/api/v2`, and
  `hieradata/role/matching_wt.yaml` opens `12347` under `fwrules::virtual`. Both
  ends are indexed `key` nodes; only the literal joins them.
  `search_code_nodes('12347')` and `('12348')` returned `[]`, while `('ccu')` and
  `('matching')` returned their keys correctly - everything was indexed except
  what the question needed.
- `homelander`: `search_code_nodes('crank-pool')` returned `[]` although the
  string sits in more than twenty files - it is the DNS pool that four
  `enlisted.net` records alias and that an IAM policy grants a robot to rewrite.
  Locating it took `grep -rn crank`, and the alias targets, the IAM grant and the
  zone files then all came from that grep rather than from the graph.

- Add a content mode behind a GIN `pg_trgm` index, or `tsvector` if word
  boundaries matter more than substrings. No model required; it sits below the
  embedding item and covers the lexical half embeddings do not give.
- A node-type filter matters as much as the mode: the target almost always sits
  in a `key`, `stage` or `target` node rather than in a file, and without the
  filter a common literal ("80", "443") returns noise.
- It is also half of the cross-project question without the resolver of item 21:
  `matching.pwt:12347` ties `kurum` (the collector's code) to `puppet` (its
  configuration) through a single literal, provided values are searchable in
  both.

Open decision: return the owning node, or the matching line inside it.
