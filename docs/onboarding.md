---
layout: default
title: Onboarding
nav_order: 2
---

## Prerequisites

- Docker with the Compose plugin.
- Python 3.11+ (for internal tools).
- Node.js 20+ (for MCP tools).

## Quick start

1. Initialize the environment:

   ```bash
   make init
   ```

2. Configure your environment: Edit the generated `.env` file to set `PROJECT_PATH` (the path to your codebase) and `POSTGRES_PASSWORD`.

3. Build the services:

   ```bash
   make build
   ```

4. Start the stack:

   ```bash
   make up
   ```

5. Onboard your codebase:

   ```bash
   make install AGENT_ROOT=/path/to/your/project
   ```

   This registers the project and mounts its tree; it does not build the
   graph. The dashboard lists it as `never indexed` with an Index button
   beside it, and that button is what indexes it.

## Adding new codebases

You can index multiple codebases into the same database stack:

```bash
context-install                 # from /path/to/another/project
context-install TYPE=docs       # from /path/to/a/handbook
```

The stack manages them by path, and you can access them by name via MCP.
`TYPE=` categorises a project - `codebase` (the default), `docs` or `config` -
which is what `search_code_nodes` narrows on when it searches every project at
once. It is stored with the row, so a later run without `TYPE=` keeps it.
Names beginning with `_` are refused: they belong to the built-in projects,
`_memory` today.

Onboarding a tree twice is safe. No file that exists is replaced, and the
project row keeps the type and the index date it already had.
