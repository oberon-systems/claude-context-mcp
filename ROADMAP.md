# Project Roadmap

This roadmap tracks the development of the Dockerized GraphRAG & Vector Context MCP Service.

## Phase 1: Stability & Correctness (Highest Priority)

These items are essential for the graph to be authoritative. Agents cannot trust the graph if these are not addressed.

- [ ] **Node ID Collisions:** Key the node ID map by `(producer_id, source_file)` to prevent cross-file edge retargeting.
- [ ] **Manual Summary Durability:** Ensure manual entity summaries survive producer wipes and line-number changes.
- [ ] **Visualization Stability:** Implement server-side reduction/lazy loading to fix 503 errors on large graphs.

## Phase 2: Core Graph Completeness (Infrastructure & Parsers)

Expanding the breadth of what the graph covers and how accurately it resolves relations.

- [ ] **Docker Compose Parsing:** Architectural nodes/edges for service dependencies.
- [ ] **Terraform/Terragrunt Relations:** Resolve `source`, `include`, and `templatefile` references.
- [ ] **Additional Parsers:** RPM specs, Python manifests (`requirements.txt`, `setup.cfg`), and systemd units.
- [ ] **Shebang Support:** Enable parsing for extension-less scripts.
- [ ] **Language Extractor Improvements:** Enhance Python/Ruby cross-file resolution.
- [ ] **Incremental Extraction (Code):** Speed up re-indexing for large codebases.

## Phase 3: Developer Experience & Workflows

Simplifying how users interact with the stack and how agents manage project context.

- [x] **reinstall target**: add target for re-install already onboarded tools (force)
- [ ] **local documentation**: docs using for github pages, we should also provide it locally with our stack
- [x] **single entry point**: use nginx as ingress for all web-endpoints as a part of the stack
- [ ] **system tables index, reindex**: `_mempory`, `_suggestions` and other `_` should be indexed periodically
- [ ] **migrate plans**: should be a system table as \_memory and others
- [x] **Cross-Project Plans:** One `plans` table for the whole database, with the project as a tag and `project: "*"` for a plan that belongs to none.
- [x] **Repository Categorization:** Tag projects (e.g., 'codebase' vs 'docs') to enable targeted cross-repository searches.
- [ ] **Git Integration:** Automatic re-indexing and history-enriched nodes.
- [x] **Maintenance Tools:** Backup/Restore (`make backup`, `make restore`).
- [x] **Local Build:** Streamlined `make build` with stable tagging.
- [x] **Gap Tracking:** Persistent storage for agent-reported missing context/gaps.
- [ ] **Shared Records (\_common):** Handle cross-project conventions, the way plans are already handled. The `_` prefix is reserved for these: `make index` refuses a project name starting with one.
- [ ] **Cross-Project Lookups:** Link relations between codebases.
- [x] **Web Interface:** Dashboard for plans, metadata, and graph overview.

## Phase 4: Semantic & Advanced Intelligence

Adding vector context and agent memory.

- [ ] **Lexical Search:** Add GIN/trigram index for literal content search (`grep` over the graph).
- [ ] **Semantic Search:** Implement vector embedding generation and HNSW search.

---

## Completed Items

- Single entry point: nginx is the only service that publishes a host port, and it picks the backend from the path - `/mcp`, `/sse` and `/health` to the MCP server, `/worker` to the summarization queue, everything else to the dashboard. `GATEWAY_PORT` and `GATEWAY_BIND` set where it listens, and `GATEWAY_HOSTS` gives the MCP server and the dashboard API the `Host` allowlist they now need, since behind the gateway that header carries the gateway's port rather than their own. The default port is 3000, so every `/mcp/<project>` address already written into an onboarded repository keeps working. The dashboard is no longer loopback-only: it rides the same listener, which is bound to every interface because a remote summarization worker needs it to be
- Gap tracking: what the graph could not answer is recorded rather than printed. `save_suggestion`, `get_suggestions` and `drop_suggestion` write into `_suggestions`, a built-in project alongside `_memory`, and the id is a stable slug derived from the gap - so reporting the same gap again keeps its first sighting, moves its last, counts a hit and reopens it if it had been resolved, which is what turns a complaint repeated across sessions into a backlog ranked by how much it actually costs. Each carries a kind, a status and the lever closing it would move (`tokens`, `coverage`, `runtime`), and the dashboard grew a Suggestions tab that triages them without being able to author one
- Project types and agent memory: `projects.type` categorises a project as `codebase`, `docs`, `config` or `memory` (`make index TYPE=`, stored once so a plain re-index keeps it), `search_code_nodes` gained `project: "*"` and `project_type` to search every project or every project of one kind with the limit shared between them, and `save_memory`/`get_memory`/`drop_memory` write conventions and decisions into `_memory` - a built-in project of type `memory` holding records rather than files, tagged with what each is about the way a plan is
- Model summaries: `make summarize` describes every file node of both halves of the tree with a local GGUF model (Qwen2.5-Coder-1.5B-Instruct Q4_K_M by default, MODEL= for the others) reading the head of the file - a resumable pass of its own rather than part of indexing, since it costs seconds per file - cached by content hash in `summary_cache`, marked `summary_source: llm` so a re-index keeps it, and capped by the cpu and memory limits on the indexer container. Entity nodes still carry no summary of their own
- A dashboard on loopback port 3002: the indexed projects with their counts and how old each index is, a browsable node index with summaries and neighbours, the viewer's graph embedded through a same-origin proxy, and every plan in the database readable, filterable by project, status and the new type, and editable in place
- Unified onboarding: one `make install` registers the `context` server for both agents, renders the skill, writes a CLAUDE.local.md, generates and verifies the `.ctxkeep`/`.ctxignore` pair from what the tree holds, adds the shell aliases and indexes the result - never replacing a file that exists
- Schema management: numbered goose migrations over a `schema_migrations` table, applied to the existing database by the `migrate` service before anything else reads it
- A re-index invalidates the extractor cache (keyed by project and path, dropped with the project, forced by `make index FRESH=1`, and a run that reports its own shortfall)
- Move the database out of the working tree
- Use public registry for built images
- Drop a project from the graph
