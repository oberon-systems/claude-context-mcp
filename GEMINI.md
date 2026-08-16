# GEMINI.md - Project Instructions for AI Agent

## 1. Context & Goal

You are working on a Dockerized GraphRAG & Vector Context MCP Service for Claude CLI (`claude-code`).

- **Goal:** Provide an isolated PostgreSQL + pgvector database, a Python-based Graphify code-graph parser, and a TypeScript MCP server in a single Docker Compose stack to index codebases and supply graph/vector context tools to Claude CLI.
- **Architecture:** Microservices approach orchestrated via Docker Compose. Host project codebase is mounted read-only (`:ro`), AST graph and vector embeddings are stored in PostgreSQL, and tools are exposed over MCP (Streamable HTTP, with SSE kept for older clients).

## 2. New Features: Summarization & Planning

- **Automatic Summarization:** The `graphify` engine automatically generates initial summaries for indexed files (Markdown, Code) and stores them in the `graph_nodes` table.
- **Persistent Project Planning:** The system supports tracking project execution plans in the `project_plans` table, manageable via dedicated MCP tools (`save_plan`, `get_plans`).

---

## 3. Core Architectural Rules

1. **Docker-First & IaC:** All services must be fully configured and launchable via `docker-compose.yml`. Database migrations and initial schemas belong in `/init-db/`.
2. **Read-Only Codebase Access:** Target repository volumes must ALWAYS be mounted as read-only (`:ro`). Containers must never mutate host source files.
3. **Strict ASCII & Language Policy:** All code comments, docstrings, documentation, and Git commit messages MUST be written in **pure ASCII English**. No emojis, no non-ASCII Unicode characters allowed anywhere in source code, comments, or docs.
4. **Pre-Commit Enforcement:** All modules must pass `pre-commit` hooks prior to committing code.
5. **Dynamic Pre-Commit Rule:** Whenever new file types or extensions are added to the project structure (e.g., Go, Rust, Terraform, Markdown), appropriate pre-commit hooks for those extensions MUST be added to `.pre-commit-config.yaml`.
6. **Commitizen Standard:** All Git commits must strictly use `commitizen` with the `wyld-cz` adapter.

---

## 4. Technology Stack

- **Orchestration & Infrastructure:** Docker, Docker Compose.
- **Database:** PostgreSQL 16 (`pgvector/pgvector:pg16`), SQL init scripts.
- **Graphify Engine:** Python 3.11+, `graphifyy` (upstream code extractor, used
  as a library), `tree-sitter`, `networkx`, `psycopg2-binary`.
- **MCP Server:** Node.js 20+, TypeScript, `@modelcontextprotocol/sdk`, Express, `pg`.
- **Code Quality & Tooling:** `pre-commit`, `commitizen` (`wyld-cz`), `ruff`, `eslint`, `prettier`, `typescript` (`tsc`), `shellcheck`.

---

## 5. Current Work Phase

Refer to `ROADMAP.md` or task tracking for current state.

### Project Directory Layout

- `/init-db/` - Database schema initialization scripts (`01-init.sql`).
- `/graphify/` - Python indexing service (`src/ctxgraph/` package, `Dockerfile`,
  `requirements.txt`). The package is named `ctxgraph` because the upstream
  extractor it drives installs itself as `graphify`.
- `/skills/graphify/` - the agent skill, registered for Claude and Gemini by
  `make skill-install`.
- `/mcp-server/` - TypeScript MCP Server (`src/index.ts`, `Dockerfile`, `package.json`, `tsconfig.json`).
- `docker-compose.yml` - Container orchestration.
- `.pre-commit-config.yaml` - Pre-commit rules configuration.
- `.cz.yaml` - Commitizen configuration using `wyld-cz`.

### Next Actionable Steps

1. Maintain root `.pre-commit-config.yaml` and `.cz.yaml` (`wyld-cz`).
2. Finalize SQL schema in `init-db/01-init.sql` for node graphs, edge relationships, and vector embeddings.
   Every table is scoped to a row of `projects`: one database holds the graph of
   every indexed codebase, and `graph_nodes` is keyed on `(project, id)`.
3. Extend the Tree-sitter parsers in `graphify/src/ctxgraph/parsers/` as new
   infrastructure formats are needed. Programming languages go to the upstream
   extractor instead, through `GRAPHIFYY_EXTENSIONS` in `config.py`.
4. Extend MCP tools in `mcp-server/src/index.ts` to expose vector similarity search alongside graph queries.

---

## 6. Pre-Commit Hooks & Quality Assurance

Always keep `.pre-commit-config.yaml` at the root level updated for all active file extensions:

- **Python:** `ruff` (linting and formatting).
- **TypeScript / JavaScript:** `eslint`, `prettier`.
- **Shell Scripts:** `shellcheck`.
- **General:** `check-yaml`, `check-json`, `trailing-whitespace`, `end-of-file-fixer`.
- **Mandatory Rule:** When introducing a new file format/extension to the repo, check for available pre-commit hooks and append them to `.pre-commit-config.yaml`.

---

## 7. Coding Standards

- **Language & Encoding:** English only. Pure ASCII. No Unicode symbols, non-ASCII characters, or emojis in docstrings, comments, commit messages, or docs.
- **Python:** PEP 8 compliant, explicit type hints required, robust exception handling.
- **TypeScript:** Strict type checking enabled (`"strict": true` in `tsconfig.json`), no implicit `any`.
- **Git Commit Format:** Must be generated via `cz c` using `commitizen` with `wyld-cz`.
