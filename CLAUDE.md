# CLAUDE.md - Project Instructions for AI Agent

Dockerized GraphRAG and vector-context MCP service for Claude CLI: an isolated
PostgreSQL + pgvector database, a Python code-graph indexer, and a TypeScript
MCP server, orchestrated by Docker Compose. Host codebases are mounted
read-only, the graph and embeddings live in PostgreSQL, and the tools are
exposed over MCP (Streamable HTTP, SSE kept for older clients).

## Rules

1. **Docker-first.** Every service is configured and launchable from
   `docker-compose.yml`. Schema changes are numbered goose migrations in
   `/migrations/`, created with `make db new NAME=<slug>`; never edit a
   migration that has already been applied.
2. **Read-only codebases.** Target repository volumes are ALWAYS mounted `:ro`.
   Containers never mutate host source files.
3. **Pure ASCII English** in all code, comments, docstrings, docs and commit
   messages. No emoji, no non-ASCII characters anywhere.
4. **Pre-commit passes before any commit.** When a new file extension enters the
   repository, add its hook to `.pre-commit-config.yaml` - that is mandatory,
   not optional.
5. **Commits go through commitizen**, never by hand. See the `commit` skill.
6. **Python** is PEP 8 with explicit type hints and real exception handling;
   **TypeScript** runs `"strict": true` with no implicit `any`.

## Schema and parsers

- Every table is scoped to a row of `projects`: one database holds the graph of
  every indexed codebase, and `graph_nodes` is keyed on `(project, id)`.
- Infrastructure formats get Tree-sitter parsers in
  `graphify/src/ctxgraph/parsers/`. Programming languages go to the upstream
  extractor instead, through `GRAPHIFYY_EXTENSIONS` in `config.py`.
- MCP tools live in `mcp-server/src/index.ts`.

## Stack

PostgreSQL 16 (`pgvector/pgvector:pg16`) - Python 3.11+ with `graphifyy`,
`tree-sitter`, `networkx`, `psycopg2-binary` - Node.js 20+ with
`@modelcontextprotocol/sdk`, Express, `pg` - React 19 + Vite for the dashboard.
Tooling: `pre-commit`, `commitizen` (`wyld-cz` adapter when installed), `ruff`,
`eslint`, `prettier`, `tsc`, `shellcheck`.

## Layout

- `/migrations/` - numbered goose migrations and the Makefile driving them.
- `/graphify/` - the Python indexing service. The package is `ctxgraph` because
  the upstream extractor it drives installs itself as `graphify`.
- `/mcp-server/` - the TypeScript MCP server.
- `/web/` - the dashboard: an Express JSON API over the same schema and a React
  client, in one image.
- `/skills/` - the skills installed by `make skill-install` for Claude and
  Gemini alike: `context`, `commit`, `delegate`, `write-docs`.
  `/.claude/skills/` holds the installed copies.
- `/templates/` - the `CLAUDE.local.md` an onboarded codebase is given.
- `/scripts/` - `install.sh` and `mcp_register.py` drive `make install`;
  `backup.sh` and `restore.sh` drive the database targets.

Current state is tracked in `ROADMAP.md`. Implementation may be handed to the
Gemini CLI - see the `delegate` skill.
