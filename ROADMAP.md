# Project Roadmap

This roadmap tracks the development of the Dockerized GraphRAG & Vector Context MCP Service.

## Stability & Correctness (Highest Priority)

These items are essential for the graph to be authoritative. Agents cannot trust the graph if these are not addressed.

- [ ] **Node ID Collisions:** Key the node ID map by `(producer_id, source_file)` to prevent cross-file edge retargeting.
- [ ] **Manual Summary Durability:** Ensure manual entity summaries survive producer wipes and line-number changes.
- [ ] **Visualization Stability:** Implement server-side reduction/lazy loading to fix 503 errors on large graphs.

## Core Graph Completeness (Infrastructure & Parsers)

Expanding the breadth of what the graph covers and how accurately it resolves relations.

- [ ] **Documentation: database**: update document
- [x] **Docker Compose Parsing:** Architectural nodes/edges for service dependencies.
- [ ] **Terraform/Terragrunt Relations:** Resolve `source`, `include`, and `templatefile` references.
- [ ] **Additional Parsers:** RPM specs, Python manifests (`requirements.txt`, `setup.cfg`), and systemd units.
- [ ] **Shebang Support:** Enable parsing for extension-less scripts.
- [ ] **Language Extractor Improvements:** Enhance Python/Ruby cross-file resolution.
- [ ] **Incremental Extraction (Code):** Speed up re-indexing for large codebases.
- [ ] **base**: some settings, formats and so should being stored in database (\_configs)

## Developer Experience & Workflows

Simplifying how users interact with the stack and how agents manage project context.

- [x] **reinstall target**: add target for re-install already onboarded tools (force)
- [ ] **local documentation**: docs using for github pages, we should also provide it locally with our stack
- [x] **single entry point**: use nginx as ingress for all web-endpoints as a part of the stack
- [ ] **system tables index, reindex**: `_mempory`, `_suggestions` and other `_` should be indexed periodically
- [x] **migrate plans**: should be a system table as \_memory and others
- [x] **Cross-Project Plans:** One `plans` table for the whole database, with the project as a tag and `project: "*"` for a plan that belongs to none.
- [x] **Repository Categorization:** Tag projects (e.g., 'codebase' vs 'docs') to enable targeted cross-repository searches.
- [ ] **File tracking per project**: notice that a tree changed and index it without being asked. Every codebase is mounted read-only at `/code/<project>` and the API runs the indexing in its own process, so the watcher belongs there rather than on the host. Three signals worth weighing, and they are not exclusive: `inotify` over the mounts (immediate, but a watch per directory and a limit to raise on large trees), a timer per project (cheap, coarse, and it walks trees nothing touched), and the `file_hashes` the graph already keeps (exact, but only found by walking). Whatever drives it, the run is the same `POST /index` the dashboard button uses, and `index_jobs` already refuses two runs of one project at once.
- [ ] **Git Integration:** Automatic re-indexing and history-enriched nodes.
- [x] **Maintenance Tools:** Backup/Restore (`make backup`, `make restore`).
- [x] **Local Build:** Streamlined `make build` with stable tagging.
- [x] **Gap Tracking:** Persistent storage for agent-reported missing context/gaps.
- [ ] **Shared Records (\_common):** Handle cross-project conventions, the way plans are already handled. The `_` prefix is reserved for these: indexing refuses a project name starting with one.
- [ ] **Cross-Project Lookups:** Link relations between codebases.
- [x] **Web Interface:** Dashboard for plans, metadata, and graph overview.
- [x] **Every Project At Once:** `--auto` on both summarizing passes, which is also what naming no project does.
- [ ] **web**: should show how many files without summory (llm generated) in each project
- [ ] **web**: allow to change repository type in web interface
- [ ] **web**: drow graph for a specific file
- [x] **web**: reindex button for force reindex
- [ ] **web**: indexing status and summarize status

## Semantic & Advanced Intelligence

Adding vector context and agent memory.

- [ ] **Lexical Search:** Add GIN/trigram index for literal content search (`grep` over the graph).
- [ ] **Semantic Search:** Implement vector embedding generation and HNSW search.

## Integrations

- [ ] **remote repositories**: able to store and index remote (3rd part) repositories (.e.g from github, by tag, branch, version)
- [ ] **grafana integration**: somehow?
- [ ] **facts collector**: collect servers facts from foreman/ansible whatever
- [ ] **API**: provide API with same tools as MCP for remote calls (auth?)
- [ ] **auth on web**: use nginx auth and some auth service (SSO, Oauth and so).

---

## Completed Items

- Plans as a built-in project: `_plans` joins `_memory` and `_suggestions`, so
  every record an agent writes is now a `graph_nodes` row and none of them
  has a table of its own. The plan id is carried over unchanged - it names a
  topic and is written by hand, so it is already unique across the database
  and needs none of the `<about>/<id>` scoping a memory id gets - and the
  project a plan is about moves from a nullable column to
  `metadata ->> 'about'`, which is what a memory has always used. The plan's
  kind becomes the node type, so 'plan' and 'template' filter the way any
  other node type does. Plans are searchable through `search_code_nodes` for
  the first time, a project drop no longer needs a delete of its own, and
  backup and restore stop naming plan columns one by one

- Docker Compose parsing: a compose file is an architecture rather than a bag
  of top level keys. Services, volumes, networks, configs and secrets become
  nodes named by their kind (`service.postgres`, `volume.graph-out`), and what
  a service names becomes an edge leaving that service rather than the file -
  `depends_on` and `links` to the services it waits for, `uses_image` to an
  external `image:` node, `builds` to the Dockerfile behind its build context,
  `uses_volume`, `uses_network`, `uses_config`, `uses_secret`, `mounts` to the
  bind-mounted file and `reads_vars` to its `env_file`. The canonical names
  are matched by the registry and anything else holding a top level `services:`
  mapping is recognised by its shape, so a `stack.yml` reads the same. What
  would only produce a node pointing at nothing is dropped instead: a value
  still holding `${...}`, a bind mount that is absolute or names a directory,
  a git URL build context

- Single entry point: nginx is the only service that publishes a host port, and it picks the backend from the path - `/mcp`, `/sse` and `/health` to the MCP server, `/worker` to the summarization queue, everything else to the dashboard. `GATEWAY_PORT` and `GATEWAY_BIND` set where it listens, and `GATEWAY_HOSTS` gives the MCP server and the dashboard API the `Host` allowlist they now need, since behind the gateway that header carries the gateway's port rather than their own. The default port is 3000, so every `/mcp/<project>` address already written into an onboarded repository keeps working. The dashboard is no longer loopback-only: it rides the same listener, which is bound to every interface because a remote summarization worker needs it to be
- Gap tracking: what the graph could not answer is recorded rather than printed. `save_suggestion`, `get_suggestions` and `drop_suggestion` write into `_suggestions`, a built-in project alongside `_memory`, and the id is a stable slug derived from the gap - so reporting the same gap again keeps its first sighting, moves its last, counts a hit and reopens it if it had been resolved, which is what turns a complaint repeated across sessions into a backlog ranked by how much it actually costs. Each carries a kind, a status and the lever closing it would move (`tokens`, `coverage`, `runtime`), and the dashboard grew a Suggestions tab that triages them without being able to author one
- Project types and agent memory: `projects.type` categorises a project as `codebase`, `docs`, `config` or `memory` (the project type, stored once so a plain re-index keeps it), `search_code_nodes` gained `project: "*"` and `project_type` to search every project or every project of one kind with the limit shared between them, and `save_memory`/`get_memory`/`drop_memory` write conventions and decisions into `_memory` - a built-in project of type `memory` holding records rather than files, tagged with what each is about the way a plan is
- Model summaries: `make summarize` describes every file node of both halves of the tree with a local GGUF model (Qwen2.5-Coder-1.5B-Instruct Q4_K_M by default, MODEL= for the others) reading the head of the file - a resumable pass of its own rather than part of indexing, since it costs seconds per file - cached by content hash in `summary_cache`, marked `summary_source: llm` so a re-index keeps it, and capped by the cpu and memory limits on the indexer container. Entity nodes still carry no summary of their own
- A dashboard on loopback port 3002: the indexed projects with their counts and how old each index is, a browsable node index with summaries and neighbours, the viewer's graph embedded through a same-origin proxy, and every plan in the database readable, filterable by project, status and the new type, and editable in place
- Unified onboarding: one `make install` registers the `context` server for both agents, renders the skill, writes a CLAUDE.local.md, generates and verifies the `.ctxkeep`/`.ctxignore` pair from what the tree holds, adds the shell aliases and indexes the result - never replacing a file that exists
- Schema management: numbered goose migrations over a `schema_migrations` table, applied to the existing database by the `migrate` service before anything else reads it
- A re-index invalidates the extractor cache (keyed by project and path, dropped with the project, forced by a fresh run, and a run that reports its own shortfall)
- A re-index invalidates on a parser change too: the stored file hash covers the content and the revision of the parsers reading it, so a parser that renames the nodes it declares re-parses the trees it owns on the next plain index run rather than leaving the old nodes behind and emitting edges against ids nobody wrote. `link_file` also drops an edge leaving an entity the current parser did not declare, instead of letting the foreign key abort the transaction and cost the file every edge it had
- Move the database out of the working tree
- Use public registry for built images
- Drop a project from the graph
