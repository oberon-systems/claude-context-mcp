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
- `list_projects` - the other projects in the same database, with the type of
  each, readable by passing their name as the `project` argument of any tool
- `search_code_nodes` with `project: "*"`, or with `project_type: "docs"` -
  one search across every project, or across every project of one kind, for
  when the question is "where is this written down" rather than "what does this
  repository do"

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

## Memory: what to keep past this session

Something worked out that the tree does not record - a convention, a decision,
why something is the way it is - goes into the built-in `_memory` project:

- `save_memory` - a slug, a title, the text, and `about` naming what it applies
  to (`"@PROJECT@"` here, or `"*"` for something that belongs to no repository)
- `get_memory` - a scope's memories plus the global ones, in full
- `drop_memory` - one that turned out to be wrong

It is a project, so `search_code_nodes` with `project_type: "memory"` finds
memories too. Nothing indexes into it, so a memory is never pruned by a
re-index. A memory is for what stays true past the task; a plan is for what to
do next. Do not use one for the other.

## Suggestions: gaps recorded, not printed

A gap is the other half of a recap. When the graph could not answer something
and the work had to be done by hand, that is a defect of the tooling, and it
belongs in the built-in `_suggestions` project rather than in a sentence that
dies with the session:

- `save_suggestion` - a slug, a title, the detail, and `about` naming the
  repository the gap is in (`"@PROJECT@"` here, or `"*"` for one that belongs
  to no repository)
- `get_suggestions` - the open gaps of a scope plus the global ones, most often
  hit first
- `drop_suggestion` - one written by mistake

**The slug is the whole mechanism.** Derive it from the gap itself and keep it
stable, so reporting the same gap next week counts a hit on the existing record
instead of filing a duplicate. `save_suggestion` keeps the first sighting,
moves the last, increments the count, and reopens a gap that had been resolved

- because a gap hit again is not a resolved one. Pass `bump: false` to correct
  the wording or set the status without claiming a fresh sighting.

Name the `lever` each gap moves - `tokens` when the answer was re-derived by
hand, `coverage` when the graph does not describe it at all, `runtime` when it
was answerable but slow - and say in the detail what concrete change would
close it: a summary to save, a `.ctxkeep` pattern to add, a parser that does
not exist, a tool that is missing. A gap whose fix is not named is a complaint,
not a suggestion.

Retire one with `save_suggestion` again, `status: "resolved"` and
`bump: false`, once the change lands. `drop_suggestion` erases the count it
accumulated, so it is for a mistake rather than for finished work - the same
rule plans follow.

A memory says what is true about a codebase; a suggestion says what the tools
could not tell you about it.

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
   `project: "*"`. A record that is run on demand rather than finished once is
   saved with `type: "template"` (or `"procedure"`), not with a status of that
   name: `get_plans` lists `type: "plan"` unless asked otherwise, which is what
   keeps rule 1's active list free of procedures. Ask for `type: "*"` to see
   every kind.
4. **When the work has landed, retire it** by calling `save_plan` again with the
   same `plan_id` and `status: completed`. A finished plan left `active` is what
   makes rule 1 untrustworthy next time. `drop_plan` exists too, but it is for a
   plan written by mistake - retiring is what finished work gets.

## Recap: say how the answer was produced

Close a task that took more than one answer with a short **Recap** - counts, not
adjectives:

1. **Graph calls** - how many, which tools, what each was looking for, and which
   lookups came back empty. Name the `get_plans` check and any memory written.
2. **Direct Read / Grep / Write** - how many, on which files, and why the graph
   was not enough.
3. **Subagents and delegates** - one line each, or "none".
4. **Gaps** - what the graph did not have and what would fix it: a summary to
   save, a `.ctxkeep` pattern to add, a parser that does not exist, an index
   older than the tree. Do not only say it: call `save_suggestion` for each
   one, and name the gaps in the recap by the slug you saved them under.

The point is auditability: the reader cannot see the tool calls, and is
comparing what the graph answered against what was re-derived by hand. Item 4
is the half that gets acted on - item 2 says what had to be done by hand, item
4 records what would remove the need, and recording it is what turns a
repeated complaint into a ranked backlog.
