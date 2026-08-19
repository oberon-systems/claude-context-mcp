---
name: graphify
description: Build and query the code graph of this project - files, entities and their relations, stored in PostgreSQL and served over MCP. Also reaches the graphs of the neighbouring projects in the same database. Use it before answering architecture questions, before a refactor that crosses files, and whenever "what uses this" or "how do these connect" comes up.
---

# /graphify

The graph of this project lives in PostgreSQL and is reached through the
`context` MCP server. This skill is how the graph gets built, inspected and
looked at.

One database holds every indexed codebase, not just this one. The session is
bound to this project by the address the client connected to, so nothing has
to be named for the ordinary case; a `project` argument on any tool reads a
neighbour's graph instead.

Adapted from the graphify skill by Graphify-Labs (MIT). The extraction it
describes is the same; the storage is not. Upstream writes `graphify-out/` and
reads it back, while here everything lands in the database, which is what lets
summaries survive a re-index and lets two agents share one graph.

## Commands

```text
/graphify                  rebuild the graph for this project
/graphify query "TEXT"     find nodes by name or identifier
/graphify path A B         shortest chain of relations between two nodes
/graphify explain NODE     what a node is, and what it connects to
/graphify graph            open the interactive page
/graphify status           is the stack up, and how much is indexed
/graphify projects         what else is indexed in the same database
/graphify index PATH       index another codebase into the same database
/graphify drop NAME        remove an indexed codebase from the database
```

## What to do when invoked

### /graphify - rebuild

Run the indexer. It is a one-shot container job, so it prints and exits:

```bash
@MAKE@ index PROJECT=@ROOT@
```

Two producers write to the same database in one pass. Code goes through the
graphifyy extractor (`.py .ts .js .go .rs .java .c .cpp .rb .cs .kt .scala
.php`), everything else through the parsers in `graphify/src/ctxgraph`
(Ansible, Puppet and its `.erb`/`.epp` templates, YAML, Terraform, Dockerfile,
Make, Markdown, shell, TOML, SQL, JSON).
Report the counts from the last two log lines and stop. Do not read the
indexed files to "check" the result.

The closing line says how many files were selected and how many of them a node
was written for. When those differ, a warning block names the files left out
and why - report that too, it is the graph admitting it does not describe the
tree. If the reason is not a skipped file (too big, unreadable), re-run with
`FRESH=1` appended, which re-parses everything instead of trusting a cache.

If it fails because the stack is down, run `@MAKE@ up` and say so.

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
on every request, so it never goes stale. With more than one project indexed it
opens on a list of them; `http://localhost:3000/graph/<project>` goes straight
to one. Nodes are coloured by community and sized by degree; the inspector
panel carries the summary on the source line. Say the address; do not try to
open a browser.

The dashboard at `http://localhost:3002` embeds the same page beside the
projects and the plans. Say that address instead when the question is about
what is indexed or what the plans hold, rather than about one graph.

### /graphify status

```bash
@MAKE@ status
```

Reports the running containers, whether the MCP server answers, and how many
nodes each indexed project has.

### /graphify projects

Call `list_projects`. It returns every codebase in the database with its root
path and node count. This is what to check before naming a `project` argument.

### /graphify index PATH

```bash
@MAKE@ index PROJECT=/absolute/path
```

Indexes any tree into the same database, under a name taken from its last path
segment. This is the same command the rebuild above runs, with another path.
The target repository needs nothing of its own - no checkout of this project
inside it, no configuration - because the path is an argument of the job rather
than a setting. Say which name it landed under; the log line names it.

### /graphify drop NAME

Call `drop_project` with the name and nothing else. It reports what the drop
costs and deletes nothing:

```json
{ "name": "api" }
```

Read the report out. The three parts are the point: nodes, edges, file hashes
and embeddings come back with one `@MAKE@ index`, manually written summaries do
not come back at all, and plans are not deleted at all - they keep the project
name as a tag and stay readable through `get_plans`. Never pass `confirm: true` on your
own - the user says the word, or the project stays. Resolve the name with
`list_projects` first; a wrong one is refused with the list of real ones.

`@MAKE@ unindex PROJECT=/absolute/path` does the same from a shell, with a
confirmation prompt.

## Querying a neighbouring project

Every tool takes an optional `project`. Without it the session's own project is
used, which is the one the client connected to; with it the same tool reads
another graph in the same database:

```json
{ "query": "handlers", "project": "balor" }
```

Use it when a question crosses repositories - an Ansible role deploying a
service whose code is indexed separately, an API and its client. Say which
project an answer came from whenever it was not this one. Resolve the name with
`list_projects` rather than guessing it; a wrong name is refused with the list
of real ones, but that is a wasted turn.

## Facts worth knowing

- Edges carry a confidence: `EXTRACTED` was read out of the syntax tree,
  `INFERRED` was guessed. Say which one you are relying on when it matters.
- Nodes carry the producer that found them in `metadata.source`, and the
  community they clustered into in `metadata.community`.
- Node ids are unique within a project, not across the database: `README.md` is
  a node in every one of them. An id on its own is not an answer; the project
  it belongs to is part of it.
- Edges never cross projects. Nothing can produce one: the indexer is handed a
  single tree and resolves every target inside it. A relation between two
  codebases is something you conclude, not something `shortest_path` returns.
- A second MCP server, `graph`, exposes the same graph through the upstream
  tools: `query_graph`, `god_nodes`, `graph_stats`, `get_community`. It reads
  a file written at index time, so it lags until the next `@MAKE@ index`, and
  that file holds whichever project was indexed last - it has no notion of
  projects at all. The `context` server reads the database directly and does.
- A plan carries a `type` as well as a `status`: `status` is where it stands,
  `type` is what it is - `plan`, `template` or `procedure`. `get_plans` lists
  `type: "plan"` unless another is named, so a procedure never appears where
  approved pending work is read; `type: "*"` lists every kind.
- The project mount is read only. Nothing in this skill writes to the indexed
  tree.
