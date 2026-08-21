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
make index       index PROJECT=<path>, TYPE=docs|config categorises it
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

| Tool                       | Arguments                                                                                          | Returns                                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `get_code_graph_neighbors` | `node_id`                                                                                          | Incoming and outgoing edges of a node, with the relation type                                       |
| `search_code_nodes`        | `query`, optional `project`, `project_type`, `limit`                                               | Nodes whose name or id matches, in one project or across a whole kind                               |
| `shortest_path`            | `source_id`, `target_id`, optional `max_hops`                                                      | Shortest chain of relations between two nodes                                                       |
| `save_node_summary`        | `node_id`, `summary`                                                                               | Saves or updates a summary for a specific node                                                      |
| `get_node_summary`         | `node_id`                                                                                          | Retrieves summary, file path and type for a node                                                    |
| `save_plan`                | `plan_id`, `title`, `content`, optional `project`, `status`, `type`                                | Creates or updates a persistent plan; `project: "*"` makes it global                                |
| `get_plans`                | optional `project`, `status`, `type`                                                               | Plans of one project plus the global ones; `project: "*"` lists all                                 |
| `drop_plan`                | `plan_id`                                                                                          | Deletes one plan outright, for one written by mistake                                               |
| `drop_project`             | `name`, optional `confirm`                                                                         | Reports what dropping a project costs, and drops it on `confirm: true`                              |
| `save_memory`              | `memory_id`, `title`, `text`, optional `about`, `summary`, `tags`                                  | Writes a memory into `_memory`; `about: "*"` makes it global                                        |
| `get_memory`               | optional `memory_id`, `about`, `tags`, `query`, `limit`                                            | Memories of one scope plus the global ones, in full                                                 |
| `drop_memory`              | `memory_id`, optional `about`                                                                      | Deletes one memory that turned out to be wrong                                                      |
| `save_suggestion`          | `suggestion_id`, `title`, `detail`, optional `about`, `summary`, `kind`, `lever`, `status`, `bump` | Records a gap in `_suggestions`; saving under an existing slug counts a hit rather than duplicating |
| `get_suggestions`          | optional `suggestion_id`, `about`, `status`, `kind`, `query`, `limit`                              | Open gaps of one scope plus the global ones, most often hit first                                   |
| `drop_suggestion`          | `suggestion_id`, optional `about`                                                                  | Deletes one suggestion written by mistake; a closed gap is retired instead                          |
| `list_indexed_files`       | optional `project`                                                                                 | The files tracked in `file_hashes`, which is the parser half of the tree                            |
| `get_file_hash`            | `file_path`, optional `project`                                                                    | The stored hash of one file, or nothing when it was never indexed                                   |
| `set_file_hash`            | `file_path`, `hash`, optional `project`                                                            | Writes a file's hash, marking it indexed                                                            |
| `clear_file_hash`          | `file_path`, optional `project`                                                                    | Forgets a file's hash, so the next run re-parses it                                                 |

Example - find how two pieces of code are related:

```text
search_code_nodes(query: "SummaryStore")
shortest_path(source_id: "src/index.ts::handleRequest", target_id: "src/storage.py::SummaryStore")
```

Errors come back as a tool result with `isError` set, rather than tearing
down the client session.

## Project types and searching across them

Every project carries a type: `codebase` (the default), `docs` and `config`
for indexed trees, and `memory` for the built-in `_memory` project. It is set
by `make index PROJECT=... TYPE=docs` and stored once - a later run without
`TYPE=` keeps it rather than resetting it.

The type earns its place in `search_code_nodes`, which is the one read that
need not stop at a project:

```text
search_code_nodes(query: "retention", project: "*")
search_code_nodes(query: "retention", project_type: "docs")
```

The first searches every graph in the database, the second every project of
one kind. Each row names its project, and the limit is shared out between the
projects rather than spent on whichever sorts first, so six indexed codebases
answer with six projects' hits. A named `project` and a `project_type` cannot
be combined - one narrows what the other spans.

## Memory

What an agent works out about a repository - a convention, a decision, why
something is the way it is - has nowhere to live in a tree it may only read.
`save_memory` writes it into `_memory`, a built-in project of type `memory`
created by the migration, holding records rather than files:

```text
save_memory(memory_id: "commit-style", title: "Commits go through cz",
            text: "...", about: "*", tags: ["git"])
get_memory(about: "api")
drop_memory(memory_id: "api/commit-style")
```

A memory is tagged with what it is about, the way a plan is - a project name,
or `"*"` for one belonging to no repository in particular - and reading one
scope always returns the global ones alongside it. Nothing indexes into
`_memory`: `make index` refuses a project name starting with `_`, so a memory
is never pruned by a re-index the way a derived node is. It is also not a
plan: a memory is what stays true after the task, a plan is what to do next.

## Suggestions

The other half of a memory. A memory says what is true about a codebase; a
suggestion says what the tools could not tell you about it - a lookup that came
back empty, a summary too thin to answer from, a file type no parser reads.
`save_suggestion` writes it into `_suggestions`, a built-in project of type
`suggestions`, holding records the same way `_memory` does:

```text
save_suggestion(suggestion_id: "hcl-no-parser",
                title: "No parser reads *.hcl",
                detail: "...", kind: "no-parser", lever: "coverage")
get_suggestions(about: "homelander")
save_suggestion(suggestion_id: "hcl-no-parser", title: "...", detail: "...",
                status: "resolved", bump: false)
```

The `suggestion_id` is a stable slug derived from the gap, and that is the
whole mechanism: saving under one that exists is how the same gap is reported
again. The record keeps its `first_seen`, moves its `last_seen`, increments
`hits`, and keeps any `kind` or `lever` the call did not name. It also reopens
a suggestion that had been resolved, because a gap hit again is not a resolved
one. `bump: false` corrects the wording or the status without claiming a fresh
sighting, which is what retiring one looks like.

Three vocabularies, free text in the database and documented rather than
`CHECK`-enforced, like a plan's status:

- `kind` - `empty-lookup`, `missing-summary`, `thin-summary`, `not-indexed`,
  `no-parser`, `stale-index`, `missing-tool`.
- `lever` - what closing the gap buys: `tokens` when the answer was re-derived
  by hand, `coverage` when the graph does not describe it at all, `runtime`
  when it was answerable but slow.
- `status` - `open` (the default), `resolved`, `wontfix`.

Reads default to the open ones of the named scope plus the global ones, most
often hit first; `status: "*"` reads every status. `drop_suggestion` erases the
count a record accumulated, so it is for one written by mistake - a gap that
has since been closed is retired with `status: "resolved"` instead. Because
`_suggestions` is a project like any other, `search_code_nodes` with
`project_type: "suggestions"` finds them, and the dashboard lists them on its
own tab.

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

`make up` serves a dashboard at <http://localhost:3000>, on the stack's one
entry point. It's the one service here that writes to the database on a
browser's behalf and it has no authentication of its own, so anything that
can reach the entry point can edit what it shows.

- **Projects** - what's indexed, where it came from, node/edge/file/plan
  counts, and how stale the index is.
- **A project** - four tabs: _overview_ (node type breakdown), _graph_ (the
  viewer's page, proxied so the frame shares this origin), _nodes_ (search
  and inspect one node's summary, metadata, neighbours and stored source),
  _files_ (file nodes with entity counts and hash status).
- **Plans** - every plan in the database, filterable by project, status,
  type or a text search; opens as rendered markdown and edits in place.
- **Suggestions** - the recorded gaps, most often hit first, filterable by
  the project they are about, status or kind. It triages rather than
  authors: the status, the wording and the vocabularies are editable, the
  hit count and the first sighting are not, and there is no way to create
  one here - a suggestion is written by the agent that hit the gap.

The only writes it can make are to plans and suggestions, and dropping a
project - a drop reports what it will cost before it happens and asks the
project name to be typed as confirmation. Re-indexing isn't offered there; `make index`
remains the way in.
