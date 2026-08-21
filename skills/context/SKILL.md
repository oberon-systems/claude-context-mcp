---
name: context
description: Work through the `context` MCP server - plans, graph exploration, memory, the suggestion backlog - and close the task by reporting how the answer was produced. Use at the start of any non-trivial request, and whenever an open question about how something is set up - where something lives, what runs it, how it is managed - has to be answered from the context alone, before any Read, Grep or explore agent.
---

# Context

The `context` MCP server holds what this project knows across sessions: the
code graph, the plans, the memory and the suggestion backlog. A session that
never reads it repeats work another session already did, and a session that
never writes to it loses everything worked out here.

## Plans

- The first tool call of any non-trivial request is `get_plans`, default
  `status: active`. Plans live in one table for the whole database, so the
  call also returns global plans; `project: "*"` lists every project's.
- An active plan is an approved plan: execute it. Do not re-run its research,
  re-verify its conclusions or re-enter plan mode. An instruction from the
  user outranks the plan.
- Save a plan with `save_plan` as soon as the user approves it: a stable
  `plan_id` from the topic, `status: active`, and enough content that another
  session can execute it without this conversation.
- Write a plan back under the same `plan_id` when it changes, and retire it
  with `status: completed` once the work has landed. `drop_plan` is only for a
  plan written by mistake.

## Explore

- Search the graph before any Read, Grep or explore agent: `search_code_nodes`,
  `get_code_graph_neighbors`, `get_node_summary`, `shortest_path`.
- Open only the files the graph named. Fall back to Read or Grep when the
  graph cannot answer: the question is about literal file content, the entity
  is not indexed, or the index is older than the tree and needs a re-index
  first.
- While a plan is loaded it defines the file set, and nothing outside it is
  touched. A step that needs an unnamed file is a defect in the plan - name
  the file, amend that step, continue.
- A node whose summary answers nothing is worth fixing: write one and persist
  it with `save_node_summary`.

## Questions

An open question - how something is set up, how it is built, where the code
that does a given thing lives - is answered out of the context and nothing
else.

- Search the context first and only: `search_code_nodes` across projects with
  `project: "*"`, narrowed by `project_type` when the kind of tree is known,
  then neighbours and summaries on whatever came back.
- Build the answer strictly from what the context returned. Do not complete it
  from general knowledge or from what such a setup usually looks like - a
  plausible answer about this user's estate is indistinguishable from a true
  one, and wrong.
- When the context covers only part of the question, answer that part, say
  plainly which part is missing, and name what would close it: a tree to
  index, a summary to write, a parser that does not exist.
- That gap is worth a `save_suggestion`. An open question the graph could not
  answer is the clearest kind of coverage gap there is.

## Memory

- `save_memory` holds what stays true after the task ends - a convention, a
  decision, the reason something is the way it is. A memory is what stays
  true; a plan is what to do next.
- Write what the user asks to remember, and what this session worked out that
  the tree records nowhere. Nothing indexes into memory, so a re-index never
  prunes it.

## Suggestions

- When the context could not answer and the work had to be done by hand, that
  is a defect of the tooling: `save_suggestion`. Nothing missing means nothing
  written.
- The slug is the mechanism. Derive it from the gap and keep it stable, so the
  same gap reported next week counts a hit instead of filing a duplicate.
- Name the `lever` the fix moves - `tokens` when the answer was re-derived by
  hand, `coverage` when the graph does not describe it at all, `runtime` when
  it was answerable but slow - and say which concrete change closes it. A gap
  whose fix is not named is a complaint.
- Saving under an existing slug keeps `first_seen`, moves `last_seen` and
  counts the hit, so nothing needs reading before the write. `bump: false`
  corrects the wording without claiming a sighting.
- The vocabularies are short. `kind` is `empty-lookup`, `missing-summary`,
  `thin-summary`, `not-indexed`, `no-parser`, `stale-index` or `missing-tool`;
  `status` is `open`, `resolved` or `wontfix`.
- Retire a closed gap with `status: "resolved"`; `drop_suggestion` erases the
  count and is for a suggestion written by mistake.

## Recap

Close a task longer than one answer with a short recap: counts, not
adjectives, written from what happened rather than from what should have
happened.

- **Context calls** - how many, by tool and purpose, and which lookups came
  back empty.
- **Files** - how many read and how many written, and why the graph was not
  enough.
- **Suggestions** - the gaps this turn hit, each named by the slug it was
  saved under. Say nothing when nothing was missing.
