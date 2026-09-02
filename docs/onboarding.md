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

## A monorepo, in slices

A project can read several directories instead of one tree. Each is mounted
read-only at `/code/<project>/<alias>` and walked into the same graph, so the
pieces of a monorepo that matter are indexed without the rest of it.

Register the project first, then hand it one directory at a time:

```bash
cd /home/you/work/mono
context-project PROJECT_NAME=mono
cd deploy/configs
context-source mono
cd ../../tools/agents
context-source mono agents
```

`context-project` is `make install SOURCE=none`: it writes the agent files,
the skills and the project row, and registers no directory. `context-source`
adds the directory you stand in, under an alias taken from its name or given
as the second argument.

The alias becomes the first segment of every node id that directory produced,
so `deploy/configs/prod/nginx.conf` is `configs/prod/nginx.conf` in the graph.
Two slices may hold a file of the same name without colliding, and one
extraction pass still resolves a call from one slice into the other.

Each directory carries its own selection, resolved from its own settings row
rather than from the repository the slices were cut from. The settings tab of
the project's page edits one per directory; see
[formats](https://oberon-systems.github.io/claude-context-mcp/formats.html).

## Changing what a project reads

```bash
context-sources                              # every project, every directory
context-sources PROJECT_NAME=mono            # one of them
context-source-drop mono configs             # stop reading one
make source-promote PROJECT_NAME=api ALIAS=root
```

Each of these rewrites `docker-compose.override.yaml` and recreates the API,
because a running service holds the mounts it was started with.

Dropping a directory leaves its nodes in the graph until the next index run
prunes them, the same path a deleted file takes.

`source-promote` names the single unnamed directory of a project indexed
whole, which is what lets a second one join it. Every node id gains the alias
as its first segment, so index the project again afterwards.

## Indexing a project that reads nothing

An index run refuses a project with no directory rather than adopting the path
it was onboarded from:

```text
project 'mono' reads no directories yet; add one with
`make source-add PROJECT=<host path> PROJECT_NAME=mono ALIAS=<alias>`
```

A run also refuses when one registered directory is missing from the mount.
Indexing the rest would walk none of its files and prune every node it had, so
the fix is `make mounts` on the host and a restart of the API.
