# CLAUDE.local.md

Written by `make install` from the claude-context-mcp template. This project is
indexed as `@PROJECT@`, and the `context` MCP server answers from that graph.
Edit freely - `make install` never overwrites this file once it exists.

## 1. Plans live in the graph, not in the session

A plan held in the client's own state is invisible to every other session. The
graph is the shared source of truth, and an active plan there is an approved
plan: it says what to do next, and is not an invitation to redo the research
behind it.

1. **First tool call of any non-trivial request:** `get_plans` with
   `project: "@PROJECT@"` and the default `status: active`. Plans are held for
   the whole database, so that call also returns the global ones; `project: "*"`
   lists every project's.
2. **If an active plan covers the request, execute it.** Follow its steps in
   order. Do not re-run the research it summarizes, re-verify its conclusions,
   or read files the current step does not name. If one step no longer matches
   the tree, fix that step, say so, and continue - a stale step is not a reason
   to start planning again.
3. **When a plan is approved, save it** with `save_plan`: a stable `plan_id`
   naming the topic, `status: active`, and enough content that another session
   can execute it without this conversation. A procedure belonging to no single
   repository is saved with `project: "*"` and `type: "template"`, which is what
   keeps rule 1's active list free of procedures.
4. **When the work has landed, retire it** - `save_plan` again, same `plan_id`,
   `status: completed`. A finished plan left `active` is what makes rule 1
   untrustworthy next time. `drop_plan` is for a plan written by mistake.

## 2. Installed skills

`make install` rendered three skills into `.claude/skills/`, loaded on demand:
`graphify` (index or re-index this tree, and the traps of reading the graph -
per-project ids, edges that never cross projects, a stale snapshot in the
second `graph` server), `commit` (driving commitizen), `delegate` (handing work
to the Gemini CLI and reviewing what it wrote). Reach for one by name; nothing
of them sits in this file.

## 3. Graph before Read and Grep

Every exploration - finding usages, tracing call chains, "what calls this" or
"how do these connect" - starts with the `context` tools: `search_code_nodes`,
`get_code_graph_neighbors`, `get_node_summary`, `shortest_path`. `list_projects`
shows the neighbouring projects in the same database; `search_code_nodes` with
`project: "*"` or `project_type: "docs"` searches across them, for when the
question is "where is this written down".

Read files only when the graph cannot answer: the question is about literal file
content, the entity is not indexed, or the index is older than the tree - in
which case re-index first:

```bash
@MAKE@ index PROJECT=@ROOT@
```

A node that exists with no summary is worth filling in: write one and persist it
with `save_node_summary`.

## 4. Memory and suggestions

Something worked out that the tree does not record - a convention, a decision,
why something is the way it is - goes to `save_memory` with `about: "@PROJECT@"`
(or `"*"`). Nothing indexes into it, so a memory is never pruned by a re-index.
A memory is for what stays true past the task; a plan is for what to do next.

When the graph could not answer and the work had to be done by hand, that is a
defect of the tooling: `save_suggestion`. **The slug is the whole mechanism** -
derive it from the gap and keep it stable, so reporting the same gap next week
counts a hit instead of filing a duplicate. Name the `lever` it moves - `tokens`
when the answer was re-derived by hand, `coverage` when the graph does not
describe it at all, `runtime` when it was answerable but slow - and say in the
detail what concrete change would close it: a summary to save, a `.ctxkeep`
pattern to add, a parser that does not exist. A gap whose fix is not named is a
complaint, not a suggestion. Retire it with `status: "resolved"` and
`bump: false`; `drop_suggestion` erases the count, so it is for a mistake.

## 5. Recap: say how the answer was produced

Close a task that took more than one answer with a short **Recap** - counts, not
adjectives:

1. **Graph calls** - how many, which tools, what each was looking for, and which
   lookups came back empty. Name the `get_plans` check and any memory written.
2. **Direct Read / Grep / Write** - how many, on which files, and why the graph
   was not enough.
3. **Subagents and delegates** - one line each, or "none".
4. **Gaps** - what the graph did not have and what would fix it. Do not only say
   it: call `save_suggestion` for each one, and name them in the recap by the
   slug you saved them under.

The reader cannot see the tool calls, and is comparing what the graph answered
against what was re-derived by hand. Item 4 is the half that gets acted on - it
turns a repeated complaint into a ranked backlog.
