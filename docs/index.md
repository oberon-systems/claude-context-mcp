---
layout: default
title: Welcome
nav_order: 1
---

This service provides an isolated PostgreSQL database with vector capabilities (pgvector) and a graph-based code indexing engine. It allows you to transform codebases into searchable graphs and expose them as context to AI coding agents via the Model Context Protocol (MCP).

## What it does

...

1. **Indexes Codebases:** Maps your files, entities, and relationships into a searchable graph.
2. **Stores Vectors:** Prepares your codebase for vector-based semantic search.
3. **Serves MCP:** Provides the graph to AI agents (like Claude CLI or Gemini CLI) so they can understand the structure of your projects.

## Architecture at a glance

```text
   host codebase                  docker compose stack
   -------------                  --------------------

  /path/to/project  --(ro)-->  +-----------+
                               |  graphify |  one-shot indexing job
                               +-----------+
                                     |  writes nodes and edges
                                     v
                               +-----------+
                               | postgres  |  pgvector/pgvector:pg16
                               +-----------+
                                ^    ^    ^  read
                                |    |    |
                     +-----------+  +--------+  +-----+
   Claude, Gemini <->| mcp-server|  | viewer |  | web |<-> browser
                     +-----------+  +--------+  +-----+
```

- **postgres:** Stores the graph, vector embeddings, and persistent plans.
- **graphify:** Analyzes your codebase and populates the database.
- **mcp-server:** Exposes the graph data via the Model Context Protocol.
- **viewer:** Renders an interactive visualization of your codebase.
- **web:** A management dashboard for indexed projects and plans.
