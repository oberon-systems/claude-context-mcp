# GEMINI.md - Project Instructions for AI Agent

## 1. Context & Goal

You are working on a Dockerized GraphRAG & Vector Context MCP Service for Claude CLI (`claude-code`).

- **Goal:** Provide an isolated PostgreSQL + pgvector database, a Python-based Graphify code-graph parser, and a TypeScript MCP server in a single Docker Compose stack to index codebases and supply graph/vector context tools to Claude CLI.
- **Architecture:** Microservices approach orchestrated via Docker Compose. Host project codebase is mounted read-only (`:ro`), AST graph and vector embeddings are stored in PostgreSQL, and tools are exposed over MCP (Streamable HTTP, with SSE kept for older clients).

---

## 2. Core Architectural Rules

1. **Docker-First & IaC:** All services must be fully configured and launchable via `docker-compose.yml`. Database migrations and initial schemas belong in `/init-db/`.
2. **Read-Only Codebase Access:** Target repository volumes must ALWAYS be mounted as read-only (`:ro`). Containers must never mutate host source files.
3. **Strict ASCII & Language Policy:** All code comments, docstrings, documentation, and Git commit messages MUST be written in **pure ASCII English**. No emojis, no non-ASCII Unicode characters allowed anywhere in source code, comments, or docs.
4. **Pre-Commit Enforcement:** All modules must pass `pre-commit` hooks prior to committing code.
5. **Dynamic Pre-Commit Rule:** Whenever new file types or extensions are added to the project structure (e.g., Go, Rust, Terraform, Markdown), appropriate pre-commit hooks for those extensions MUST be added to `.pre-commit-config.yaml`.
6. **Commitizen Standard:** All Git commits must strictly use `commitizen` with the `wyld-cz` adapter.

---

## 3. Technology Stack

- **Orchestration & Infrastructure:** Docker, Docker Compose.
- **Database:** PostgreSQL 16 (`pgvector/pgvector:pg16`), SQL init scripts.
- **Graphify Engine:** Python 3.11+, `graphifyy` (upstream code extractor, used
  as a library), `tree-sitter`, `networkx`, `psycopg2-binary`.
- **MCP Server:** Node.js 20+, TypeScript, `@modelcontextprotocol/sdk`, Express, `pg`.
- **Code Quality & Tooling:** `pre-commit`, `commitizen` (`wyld-cz`), `ruff`, `eslint`, `prettier`, `typescript` (`tsc`), `shellcheck`.

---

## 4. Current Work Phase

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

## 5. Pre-Commit Hooks & Quality Assurance

Always keep `.pre-commit-config.yaml` at the root level updated for all active file extensions:

- **Python:** `ruff` (linting and formatting).
- **TypeScript / JavaScript:** `eslint`, `prettier`.
- **Shell Scripts:** `shellcheck`.
- **General:** `check-yaml`, `check-json`, `trailing-whitespace`, `end-of-file-fixer`.
- **Mandatory Rule:** When introducing a new file format/extension to the repo, check for available pre-commit hooks and append them to `.pre-commit-config.yaml`.

---

## 6. Coding Standards

- **Language & Encoding:** English only. Pure ASCII. No Unicode symbols, non-ASCII characters, or emojis in docstrings, comments, commit messages, or docs.
- **Python:** PEP 8 compliant, explicit type hints required, robust exception handling.
- **TypeScript:** Strict type checking enabled (`"strict": true` in `tsconfig.json`), no implicit `any`.
- **Git Commit Format:** Must be generated via `cz c` using `commitizen` with `wyld-cz`.

## 7. Delegating Work to Gemini

Ordinary implementation work may be handed to the Gemini CLI on
`gemini-3.1-flash-lite` and reviewed afterwards. The loop is fixed:

1. **Clean tree first.** `git status --short` must be empty, on `main`. What
   `git diff` shows afterwards is then exactly what the delegate wrote.
2. **Run it headless.** Put the task in a file to avoid quoting problems:
   `gemini -m gemini-3.1-flash-lite --approval-mode yolo -p "$(cat task.txt)"`.
   `yolo` is required - a headless run has nobody to answer a prompt, and
   `auto_edit` stalls on the first shell command.
3. **Re-index.** `make index PROJECT=<path>`, so the review reads a graph that
   matches the tree rather than the one from before the edit.
4. **Verify from the graph, never from the report.** Use the `context` MCP
   tools. The delegate's summary is a claim to be checked, not a result.
5. **Fix on top, then commit** with `cz commit` as always. Local commit only;
   pushing is the user's call.

The task prompt must always carry these standing rules: do not commit or run
any writing git command; pure ASCII English; edits must pass
`.venv/bin/pre-commit run --files <paths>`; state an acceptance test the
delegate has to check itself; and name any tree that is off limits.

Two failure modes seen repeatedly, worth pre-empting in the prompt:

- **A test that measured nothing.** The indexer source is baked into the image
  by `COPY src ./src`, so a change under `until
`make -C graphify TAG=dev build`. A delegate that skips the rebuild is
  measuring the previous version and will
- **A substituted acceptance criterion.** Asked for nodes in the graph, it
  answers with a file count, a log line, o
  persist". Ask for the node list, and query it yourself regardless.

Give the delegate the facts already established rather than letting it
rediscover them - the free tier throttles r minute,
and every wasted turn costs a minute of backoff.
