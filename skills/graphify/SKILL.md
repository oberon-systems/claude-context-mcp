---
name: graphify
description: Index a codebase into the graph, and read the graph without falling into its traps - node ids are per-project, edges never cross projects, and the second `graph` server serves a stale snapshot. Use when indexing, re-indexing, or reporting an answer that came from the graph.
---

# /graphify

The graph lives in PostgreSQL and is read through the `context` MCP server -
call those tools directly; their schemas describe them. This file is the half
the schemas cannot carry: how the graph is built, and what will make you
confidently wrong when reading it.

Adapted from the graphify skill by Graphify-Labs (MIT). The extraction is the
same; the storage is not. Upstream writes `graphify-out/` and reads it back,
while here everything lands in the database, which is what lets summaries
survive a re-index and lets two agents share one graph.

## Operations

```bash
@MAKE@ index PROJECT=@ROOT@      # incremental: only what changed
@MAKE@ reindex PROJECT=@ROOT@    # full pass, trusting neither cache
@MAKE@ unindex PROJECT=<name>    # drop one codebase (confirms first)
@MAKE@ status                    # is the stack up, and how much is indexed
@MAKE@ web                       # the interactive page
```

The indexer is a one-shot container job: it prints and exits. The project mount
is read only - nothing here writes to the indexed tree.

## Traps

- **Node ids are unique within a project, not across the database.** `README.md`
  is a node in every one of them. An id on its own is not an answer; the
  project it belongs to is part of it.
- **Edges never cross projects, and nothing can produce one.** The indexer is
  handed a single tree and resolves every target inside it. A relation between
  two codebases is something you conclude and say you concluded - never
  something `shortest_path` returns.
- **Edges carry a confidence.** `EXTRACTED` was read out of the syntax tree,
  `INFERRED` was guessed. Say which one you are relying on when it matters.
- **The `graph` MCP server is not the `context` server.** It exposes
  `query_graph`, `god_nodes`, `graph_stats`, `get_community` over a file
  written at index time, so it lags until the next index run, and that file
  holds whichever project was indexed last - it has no notion of projects at
  all. `context` reads the database directly and does.
- **`project` and `project_type` together are refused** - one narrows what the
  other spans. `project: "*"` searches every graph, and the limit is shared
  between them, so the answer is a spread rather than the first project's hits.
  Every row names its project; quote that when reporting.
- Resolve a neighbouring project's name with `list_projects` rather than
  guessing. A wrong name is refused with the list of real ones - correct, but a
  wasted turn.
- Nodes carry the producer that found them in `metadata.source`, and the
  community they clustered into in `metadata.community`.

## A stale index answers the wrong question

An index older than the tree is the one failure that looks like a correct
answer. If the graph disagrees with what you can see in a file, re-index before
concluding anything - `@MAKE@ index PROJECT=@ROOT@`.
