# CLAUDE.local.md

## 1. Plans, first

- **First tool call of any non-trivial request:** `get_plans` for this project,
  default `status: active`. Not eventually - first, before any graph query, any
  Read, any Explore or Plan agent. `list_projects` says what is indexed under
  which name. Plans live in one table for the whole database, so the call also
  returns global plans; `project: "*"` lists every project's.
- **An active plan is an approved plan** - it carries `status: active` only
  because the user approved it via `ExitPlanMode`. Execute it. Do not re-run its
  research, re-verify its conclusions, restate them for approval, re-enter plan
  mode, or spawn agents "to get oriented".
- **Once a plan is loaded, the plan defines the file set, and NOTHING OUTSIDE IT
  MAY BE TOUCHED.** No Read, Write, Edit, Grep sweep or Explore agent on a file
  the plan does not name. The permitted set is exactly the files the current
  step names plus the nodes the graph returns for the entity it changes.
- A step needing an unnamed file is a defect in the plan: name it, say why,
  amend that one step, continue. Ask first, read after.
- Escape hatch, staleness only: if a step no longer matches the tree, verify
  **that one step**, say so, adjust it in place. A stale step is never a reason
  to discard the plan and re-plan from scratch.
- On approval, immediately `save_plan`: stable `plan_id` from the topic,
  `status: active`, explicit project (`"*"` for none). Once the work has landed,
  `save_plan` again with `status: completed`. `drop_plan` is only for a plan
  written by mistake - finished work is retired, never erased.
- Reusable procedures are `type: template` / `procedure`, saved global, so they
  never pollute the active list. One exists: `ctx-file-selection-bootstrap`
  (generate and verify `.ctxkeep` / `.ctxignore` for a repository).

## 2. Graph before Read and Grep

Every exploration - usages, call chains, module relations, "what calls this" -
starts with `search_code_nodes`, `get_code_graph_neighbors`, `get_node_summary`,
`shortest_path`. Fall back to Read/Grep only when the graph cannot answer: the
question is about literal file content, the entity is not indexed, or the index
predates the tree (re-index first with `make index PROJECT=<path>`).

A node with no summary: delegate the summary to `gemini-3.1-flash-lite`, then
persist it with `save_node_summary` - right after every Explore-agent run, for
every node it touched. This section governs _how_ to research when research is
needed; it never authorizes research an active plan already did.

## 3. Recap: how the answer was produced

Close every task longer than one answer with a **Recap**. Counts, not
adjectives. Five lines, skip one only when its count is genuinely zero:

1. **Graph / `context` MCP** - calls by tool and purpose, `get_plans` included,
   and say plainly which lookups came back empty.
2. **Read / Write / Edit** - how many, on which files, and why the graph was not
   enough. A Read with no reason given is a Read that should not have happened.
3. **Subagents** - name, model, ask, result. "none" when none.
4. **External delegates** - the Gemini runs, counted separately from subagents.
5. **Suggestions** - gaps this turn actually hit, the lever each moves, the
   concrete fix. **Do not only write this line - persist it:** a
   `save_suggestion` call per gap, named in the recap by its slug. A turn that
   re-derived half its answer by hand and still reports "none" is the one line
   the user will not believe. Write it from what happened, never from what the
   workflow says should have happened.

## 4. Suggestions: the gap backlog

`save_suggestion` under a stable slug is how a gap is reported again - it keeps
`first_seen`, moves `last_seen`, counts a hit, reopens a resolved one. So no
read is needed before the write. `bump: false` corrects wording without claiming
a sighting; a closed gap is retired with `status: "resolved"`, never
`drop_suggestion`, which erases the count. `kind`: `empty-lookup`,
`missing-summary`, `thin-summary`, `not-indexed`, `no-parser`, `stale-index`,
`missing-tool`. `lever`: `tokens`, `coverage`, `runtime`. `status`: `open`,
`resolved`, `wontfix`. They are graph nodes: `project_type: "suggestions"`.

## 5. Routing and style

- Research and impact sweeps -> Sonnet 5. Writing the plan and every verdict ->
  Opus 5. Implementation -> `gemini-3.1-flash-lite`.
- **No code comments** by default. Only where the behaviour is non-obvious or
  counterintuitive, and then at most 2 lines, at most 79 characters each.
- Committing -> the `commit` skill; never `git commit -m` or a hand-written
  message. Handing work to Gemini and reviewing it -> the `delegate` skill.
