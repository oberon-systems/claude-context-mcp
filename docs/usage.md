---
layout: default
title: Usage
nav_order: 7
---

## Essential commands

```text
make init        create the virtualenv and install the pre-commit hooks
make install     onboard AGENT_ROOT=<path> onto the stack, then index it
make lint        run every pre-commit hook over every file
make build       build every service image
make up          start postgres, mcp-server, the viewer and the dashboard
make down        stop the stack, keeping the database volume
make index       index PROJECT=<path>, or PROJECT_PATH from .env
make reindex     index PROJECT=<path> again, trusting neither cache
make unindex     drop PROJECT=<path> or PROJECT_NAME=<name> from the database
make summarize   describe PROJECT's files with the model (BG=1 detaches)
make backup      write the database, or one project, to a file
make restore     put a backup file back
make status      show whether the stack runs and whether anything uses it
make psql        open a psql session against the context database
make clean       remove containers, the database directory and the built images
```

Run `make` with no target for the full list, including per-service
subdivisions (`make graphify build`, `make mcp typecheck`).

`make status` prints the running services, the `/health` payload, and the
node count. Its `sessions` field is the count of connected MCP clients - a
healthy stack reporting `0` means the client never attached, a different
problem from the stack being down.

## MCP tools

| Tool                       | Arguments                                                           | Returns                                                                |
| -------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                                                           | Incoming and outgoing edges of a node, with the relation type          |
| `search_code_nodes`        | `query`, optional `limit`                                           | Nodes whose name or id matches the substring                           |
| `shortest_path`            | `source_id`, `target_id`, optional `max_hops`                       | Shortest chain of relations between two nodes                          |
| `save_node_summary`        | `node_id`, `summary`                                                | Saves or updates a summary for a specific node                         |
| `get_node_summary`         | `node_id`                                                           | Retrieves summary, file path and type for a node                       |
| `save_plan`                | `plan_id`, `title`, `content`, optional `project`, `status`, `type` | Creates or updates a persistent plan; `project: "*"` makes it global   |
| `get_plans`                | optional `project`, `status`, `type`                                | Plans of one project plus the global ones; `project: "*"` lists all    |
| `drop_plan`                | `plan_id`                                                           | Deletes one plan outright, for one written by mistake                  |
| `drop_project`             | `name`, optional `confirm`                                          | Reports what dropping a project costs, and drops it on `confirm: true` |

Example - find how two pieces of code are related:

```text
search_code_nodes(query: "SummaryStore")
shortest_path(source_id: "src/index.ts::handleRequest", target_id: "src/storage.py::SummaryStore")
```

Errors come back as a tool result with `isError` set, rather than tearing
down the client session.

## Plans

A plan is not derived from a tree, so it is not owned by a project: the
`plans` table holds every plan in the database, keyed on a `plan_id` unique
across it, with `project` as a free-text tag rather than a foreign key.
Consequences worth knowing:

- A plan survives `make unindex` and `drop_project`, and can name a
  repository this database has never indexed.
- `get_plans` defaults to the connected project plus the global ones;
  `project: "*"` lists every project's plans at once.
- `save_plan` with `project: "*"` stores a plan that belongs to no project -
  the right home for a procedure run on demand rather than finished once.

`type` is a separate axis from `status`: `status` (`active`, `completed`,
`archived`) says where a plan stands, `type` (`plan`, `template`,
`procedure`) says what kind of record it is. `get_plans` defaults to
`type: "plan"`, so a reusable procedure never shows up mixed in with
pending work; ask for `type: "*"` to see everything. Re-saving a plan
without naming a `type` resets it to `plan`, the same way omitting `status`
resets it to `active` - `save_plan` writes a whole row, it does not patch
one.

## Web interface

`make up` publishes a dashboard on <http://127.0.0.1:3002>, bound to
loopback since it's the one service here that writes to the database on a
browser's behalf, with no authentication of its own.

- **Projects** - what's indexed, where it came from, node/edge/file/plan
  counts, and how stale the index is.
- **A project** - four tabs: _overview_ (node type breakdown), _graph_ (the
  viewer's page, proxied so the frame shares this origin), _nodes_ (search
  and inspect one node's summary, metadata, neighbours and stored source),
  _files_ (file nodes with entity counts and hash status).
- **Plans** - every plan in the database, filterable by project, status,
  type or a text search; opens as rendered markdown and edits in place.

The only writes it can make are to plans, and dropping a project - a drop
reports what it will cost before it happens and asks the project name to be
typed as confirmation. Re-indexing isn't offered there; `make index`
remains the way in.
