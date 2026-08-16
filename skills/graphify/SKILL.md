---
name: graphify
description: Build and query the code graph of this project - files, entities and their relations, stored in PostgreSQL and served over MCP. Use it before answering architecture questions, before a refactor that crosses files, and whenever "what uses this" or "how do these connect" comes up.
---

# /graphify

The graph of this project lives in PostgreSQL and is reached through the
`context` MCP server. This skill is how the graph gets built, inspected and
looked at.

Adapted from the graphify skill by Graphify-Labs (MIT). The extraction it
describes is the same; the storage is not. Upstream writes `graphify-out/` and
reads it back, while here everything lands in the database, which is what lets
summaries survive a re-index and lets two agents share one graph.

## Commands

```text
/graphify                rebuild the graph for this project
/graphify query "TEXT"   find nodes by name or identifier
/graphify path A B       shortest chain of relations between two nodes
/graphify explain NODE   what a node is, and what it connects to
/graphify graph          open the interactive page
/graphify status         is the stack up, and how much is indexed
```

## What to do when invoked

### /graphify - rebuild

Run the indexer. It is a one-shot container job, so it prints and exits:

```bash
make index
```

Two producers write to the same database in one pass. Code goes through the
graphifyy extractor (`.py .ts .js .go .rs .java .c .cpp .rb .cs .kt .scala
.php`), everything else through the parsers in `graphify/src/ctxgraph`
(Ansible, YAML, Terraform, Dockerfile, Make, Markdown, shell, TOML, SQL).
Report the counts from the last two log lines and stop. Do not read the
indexed files to "check" the result.

If it fails because the stack is down, run `make up` and say so.

### /graphify query "TEXT"

Call the `search_code_nodes` tool of the `context` MCP server. Pass the user's
words as the query. Each row already carries a one line summary - answer from
those. Open a file only when the summaries do not settle the question, and say
which one you opened and why.

### /graphify path A B

Call `shortest_path` with the two node ids. If either name is not an exact id,
resolve it with `search_code_nodes` first and say which id you picked. Read
the returned chain out as relations, not as a list of strings.

### /graphify explain NODE

Three calls, in order:

1. `get_node_summary` for the node itself
2. `get_code_graph_neighbors` for what it touches
3. `search_code_nodes` if the id needs resolving first

Explain what the node is, what depends on it and what it depends on, and what
that implies for changing it. The graph gives structure and one line of prose
per file; if the question needs more than that, open the file and say so.

Prefer `save_node_summary` over silence when you learn something the stored
summary does not say. A manual summary is marked as such and survives the next
re-index, while the generated one is replaced.

### /graphify graph

The page is at `http://localhost:3000/graph`. It is rendered from the database
on every request, so it never goes stale. Nodes are coloured by community and
sized by degree; the inspector panel carries the summary on the source line.
Say the address; do not try to open a browser.

### /graphify status

```bash
make status
```

Reports the running containers, whether the MCP server answers, and how many
nodes are indexed.

## Facts worth knowing

- Edges carry a confidence: `EXTRACTED` was read out of the syntax tree,
  `INFERRED` was guessed. Say which one you are relying on when it matters.
- Nodes carry the producer that found them in `metadata.source`, and the
  community they clustered into in `metadata.community`.
- A second MCP server, `graph`, exposes the same graph through the upstream
  tools: `query_graph`, `god_nodes`, `graph_stats`, `get_community`. It reads
  a file written at index time, so it lags until the next `make index`; the
  `context` server reads the database directly and never does.
- The project mount is read only. Nothing in this skill writes to the indexed
  tree.
