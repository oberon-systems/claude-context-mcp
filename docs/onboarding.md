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

5. Index your codebase:

   ```bash
   make install AGENT_ROOT=/path/to/your/project
   ```

## Adding new codebases

You can index multiple codebases into the same database stack:

```bash
make index PROJECT=/path/to/another/project
```

The stack manages them by path, and you can access them by name via MCP.
