# CLAUDE.local.md

Written by `make install` from the claude-context-mcp template. This project is
indexed as `@PROJECT@`, and the `context` MCP server answers from that graph.
Edit freely - `make install` never overwrites this file once it exists.

## Research policy: graph before Read and Grep

For any exploration in this repository - finding usages, tracing call chains,
understanding how modules relate, answering "what calls this" or "how do these
connect" - query the code graph FIRST, before `Read`, `Grep`, or an agent that
only greps and reads. The `context` MCP tools are the way in:

- `search_code_nodes` - locate a file or an entity by name
- `get_code_graph_neighbors` - what a node connects to, and how
- `get_node_summary` - what a node is
- `shortest_path` - the chain of relations between two nodes
- `list_projects` - the other codebases in the same database, readable by
  passing their name as the `project` argument of any tool

Fall back to reading files only when the graph cannot answer:

- the question is about literal file content - a comment, a string literal, an
  exact line number - rather than about structure;
- the entity is not indexed;
- the index is older than the tree, in which case re-index first:

```bash
@MAKE@ index PROJECT=@ROOT@
```

A node that exists with no summary is worth filling in: write one and persist it
with `save_node_summary`, so the next lookup finds it in the graph instead of
re-deriving it from the source.

## Plans live in the graph, not in the session

A plan held in the client's own state is invisible to every other session. The
graph is the shared source of truth, and an active plan there is an approved
plan - it says what to do next, and is not an invitation to redo the research
behind it.

1. **First tool call of any non-trivial request:** `get_plans` with
   `project: "@PROJECT@"` and the default `status: active`. Plans are held for
   the whole database, not per project: that call also returns the global ones,
   saved under no project, and `project: "*"` lists every project's plans.
2. **If an active plan covers the request, execute it.** Follow its steps in
   order. Do not re-run the research it summarizes, re-verify its conclusions,
   or read files the current step does not name. If one step no longer matches
   the tree, fix that step, say so, and continue - a stale step is not a reason
   to start planning again.
3. **When a plan is approved, save it** with `save_plan`: a stable `plan_id`
   derived from the topic, `status: active`, and enough content that another
   session can execute it without this conversation. The `plan_id` is unique
   across the whole database, so make it name the topic rather than repeat a
   generic word. A procedure that belongs to no single repository is saved with
   `project: "*"`.
4. **When the work has landed, retire it** by calling `save_plan` again with the
   same `plan_id` and `status: completed`. A finished plan left `active` is what
   makes rule 1 untrustworthy next time. `drop_plan` exists too, but it is for a
   plan written by mistake - retiring is what finished work gets.

## Recap: say how the answer was produced

Close a task that took more than one answer with a short **Recap** - counts, not
adjectives:

1. **Graph calls** - how many, which tools, what each was looking for, and which
   lookups came back empty.
2. **Direct Read / Grep / Write** - how many, on which files, and why the graph
   was not enough.
3. **Subagents and delegates** - one line each, or "none".
4. **Gaps** - what the graph did not have and what would fix it: a summary to
   save, a `.ctxkeep` pattern to add, a parser that does not exist, an index
   older than the tree.

The point is auditability: the reader cannot see the tool calls, and is
comparing what the graph answered against what was re-derived by hand.
