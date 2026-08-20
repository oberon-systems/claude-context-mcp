# GEMINI.md - Project Instructions for AI Agent

Dockerized GraphRAG and vector-context MCP service: an isolated PostgreSQL +
pgvector database, a Python code-graph indexer, and a TypeScript MCP server,
orchestrated by Docker Compose. Host codebases are mounted read-only, the graph
and embeddings live in PostgreSQL, and the tools are exposed over MCP.

## Standing rules for any change

1. **Never run a writing git command.** No `commit`, `add`, `checkout`, `reset`,
   `push`, `stash`. Leave the work in the tree; the reviewer commits.
2. **Pure ASCII English** in all code, comments, docstrings, docs and commit
   messages. No emoji, no non-ASCII characters anywhere.
3. **Run `.venv/bin/pre-commit run --files <paths>`** on everything touched, and
   fix what it reports. A new file extension needs its hook added to
   `.pre-commit-config.yaml`.
4. **State an acceptance test and check it yourself.** Asked for nodes in the
   graph, answer with the node list - not a file count, not a log line, not a
   promise that they "should persist".
5. **The indexer runs from the image.** `COPY src ./src` bakes it in, so a
   change under `graphify/src/` does nothing until `make -C graphify build`.
   Skipping the rebuild measures the previous version.

## Architecture rules

- **Docker-first.** Every service is launchable from `docker-compose.yml`.
  Schema changes are numbered goose migrations in `/migrations/`, created with
  `make db new NAME=<slug>`; never edit a migration already applied.
- **Read-only codebases.** Target repository volumes are ALWAYS mounted `:ro`.
- Every table is scoped to a row of `projects`; `graph_nodes` is keyed on
  `(project, id)`.
- Infrastructure formats get Tree-sitter parsers in
  `graphify/src/ctxgraph/parsers/`; programming languages go to the upstream
  extractor through `GRAPHIFYY_EXTENSIONS` in `config.py`.
- **Python** is PEP 8 with explicit type hints; **TypeScript** runs
  `"strict": true` with no implicit `any`. No code comments unless the
  behaviour is non-obvious, and then at most 2 lines of at most 79 characters.

## Layout

- `/migrations/` - goose migrations and their Makefile.
- `/graphify/` - the Python indexing service; the package is `ctxgraph` because
  the upstream extractor installs itself as `graphify`.
- `/mcp-server/` - the TypeScript MCP server.
- `/web/` - the dashboard: an Express JSON API and a React client, in one image.
- `/skills/`, `/templates/`, `/scripts/` - the agent skill, the onboarding
  template, and the shell drivers behind `make install` and the database
  targets.

Stack: PostgreSQL 16 (`pgvector/pgvector:pg16`), Python 3.11+ with `graphifyy`,
`tree-sitter`, `networkx`, `psycopg2-binary`, Node.js 20+ with
`@modelcontextprotocol/sdk`, Express, `pg`, React 19 + Vite. Tooling:
`pre-commit`, `commitizen`, `ruff`, `eslint`, `prettier`, `tsc`, `shellcheck`.
