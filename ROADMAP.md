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

- [x] **Cross-Project Plans:** One `plans` table for the whole database, with the project as a tag and `project: "*"` for a plan that belongs to none.
- [ ] **Repository Categorization:** Tag projects (e.g., 'codebase' vs 'docs') to enable targeted cross-repository searches.
- [ ] **Git Integration:** Automatic re-indexing and history-enriched nodes.
- [x] **Maintenance Tools:** Backup/Restore (`make backup`, `make restore`).
- [x] **Local Build:** Streamlined `make build` with stable tagging.
- [ ] **Gap Tracking:** Persistent storage for agent-reported missing context/gaps.
- [ ] **Shared Records (\_common):** Handle cross-project conventions, the way plans are already handled.
- [ ] **Cross-Project Lookups:** Link relations between codebases.
- [x] **Web Interface:** Dashboard for plans, metadata, and graph overview.

## Phase 4: Semantic & Advanced Intelligence

Adding vector context and agent memory.

- [ ] **Lexical Search:** Add GIN/trigram index for literal content search (`grep` over the graph).
- [ ] **Semantic Search:** Implement vector embedding generation and HNSW search.
- [ ] **Agent Memory:** Persistent store for project-wide conventions and knowledge.

---

## Completed Items

- Model summaries: `make summarize` describes every file node of both halves of the tree with a local GGUF model (Qwen2.5-Coder-1.5B-Instruct Q4_K_M by default, MODEL= for the others) reading the head of the file - a resumable pass of its own rather than part of indexing, since it costs seconds per file - cached by content hash in `summary_cache`, marked `summary_source: llm` so a re-index keeps it, and capped by the cpu and memory limits on the indexer container. Entity nodes still carry no summary of their own
- A dashboard on loopback port 3002: the indexed projects with their counts and how old each index is, a browsable node index with summaries and neighbours, the viewer's graph embedded through a same-origin proxy, and every plan in the database readable, filterable by project, status and the new type, and editable in place
- Unified onboarding: one `make install` registers the `context` server for both agents, renders the skill, writes a CLAUDE.local.md, generates and verifies the `.ctxkeep`/`.ctxignore` pair from what the tree holds, adds the shell aliases and indexes the result - never replacing a file that exists
- Schema management: numbered goose migrations over a `schema_migrations` table, applied to the existing database by the `migrate` service before anything else reads it
- A re-index invalidates the extractor cache (keyed by project and path, dropped with the project, forced by `make index FRESH=1`, and a run that reports its own shortfall)
- Move the database out of the working tree
- Use public registry for built images
- Drop a project from the graph
