# Project Roadmap

This roadmap tracks the development of the Dockerized GraphRAG & Vector Context MCP Service.

## Phase 1: Stability & Correctness (Highest Priority)

These items are essential for the graph to be authoritative. Agents cannot trust the graph if these are not addressed.

- [ ] **Schema Management:** Replace `init-db/01-init.sql` with a migration-based system (`schema_migrations` table).
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

- [ ] **Repository Categorization:** Tag projects (e.g., 'codebase' vs 'knowledge-base') to enable targeted cross-repository searches.
- [ ] **Unified Onboarding:** Single `make install` command (configure agents, install skills).
- [ ] **Git Integration:** Automatic re-indexing and history-enriched nodes.
- [ ] **Maintenance Tools:** Backup/Restore.
- [ ] **Local Build:** Streamlined `make build` with stable tagging.
- [ ] **Gap Tracking:** Persistent storage for agent-reported missing context/gaps.
- [ ] **Shared Records (\_common):** Handle cross-project plans and conventions.
- [ ] **Cross-Project Lookups:** Link relations between codebases.
- [ ] **Web Interface:** Dashboard for plans, metadata, and graph overview.

## Phase 4: Semantic & Advanced Intelligence

Adding vector context and agent memory.

- [ ] **Lexical Search:** Add GIN/trigram index for literal content search (`grep` over the graph).
- [ ] **Semantic Search:** Implement vector embedding generation and HNSW search.
- [ ] **Index-Time Summarization:** Generate node summaries using a small local LLM.
- [ ] **Agent Memory:** Persistent store for project-wide conventions and knowledge.

---

## Completed Items

- A re-index invalidates the extractor cache (keyed by project and path, dropped with the project, forced by `make index FRESH=1`, and a run that reports its own shortfall)
- Move the database out of the working tree
- Use public registry for built images
- Drop a project from the graph
