# CLAUDE.local.md

## Research policy: graph before Read/Write

For any exploration in this repo - finding usages, tracing call chains, understanding
module relationships, answering "what calls this" / "how do these connect" - query the
code graph FIRST, before Read, Grep, or spawning agents that only grep/Read. Use the
`graphify` skill's `context` MCP tools (`search_code_nodes`, `get_code_graph_neighbors`,
`get_node_summary`, `shortest_path`) to locate and orient before opening files.

Fall back to Read/Grep only when the graph doesn't cover the question: the target isn't
indexed yet, the index predates the current tree (re-run `make index PROJECT=<path>`
first), or the question is about literal file content (comments, string literals, exact
line numbers) rather than structure.

If a node exists in the graph but has no summary, don't write the summary yourself -
delegate summary collection to `gemini-3.1-flash-lite` (same headless invocation as the
delegate section below), then once it reports back, persist that summary into the graph via
`context` MCP tools (`save_node_summary`) so the next lookup finds it there instead of
re-deriving it.

Do this check right after every Explore-agent run, not just incidentally: before moving on,
look at the nodes/files that agent touched, and for any still missing a summary, run the
delegate-and-save step above immediately rather than letting it slide.

This is the default research order for the whole repo - the "verify from the graph"
step in the Gemini delegate section below is one specific application of it, not the
only one. It governs _how_ to research when research is actually needed; it never
authorizes research that an existing plan has already done. An active plan in the graph
outranks exploration - see the plans section below.

## Plans always get persisted to the graph - full lifecycle

The plan-mode plan file (`/home/zombig/.claude-work/plans/*.md`) is session-local and
ephemeral - a different session/client can't see it. The graph is the shared source of
truth for plans across sessions.

**An active plan in the graph is an approved plan.** It carries `status: active` only
because the user already approved it via `ExitPlanMode`. Finding one is not an invitation
to re-check the work behind it - it is the instruction for what to do next. Execute it.

Four rules, all mandatory:

1. **Before anything else** on a non-trivial request - before any graph query, any Read,
   any Explore or Plan agent - call `get_plans` (project `puppet`, default
   `status: active`). This is the first tool call of the turn, not something to get to
   eventually.
2. **If an active plan covers the request, act on it.** Follow its steps in order. Do NOT:

   - spawn Explore or Plan agents to "get oriented" first;
   - re-run the research the plan already summarizes;
   - re-verify the plan's conclusions, restate them back for approval, or re-enter plan
     mode to produce the same plan again;
   - read files the current step does not touch.

   Reads and graph queries are allowed only in service of the step being implemented -
   the file being edited, the callers of the entity being changed. Nothing wider.

   The single escape hatch is staleness: if a specific step no longer matches the tree
   (the file, class or parameter it names is gone or renamed), verify **that one step**,
   say so to the user, adjust that step in place, and continue. A stale step is never a
   reason to discard the plan and re-plan from scratch.

3. **Whenever a plan is finalized** (approved via `ExitPlanMode`), immediately also save it
   with `save_plan` - same content, a stable `plan_id` derived from the topic,
   `status: active` - so any other/future client can pick it up via `get_plans`.
4. **After the plan's work is actually done** (implemented, verified, landed), retire it.
   There is no delete tool in the `context` MCP set, only `save_plan`/`get_plans`, so
   "delete" means calling `save_plan` again with the same `plan_id` and
   `status: completed` (or `archived`), which drops it out of `get_plans`' default
   `active` filter. Don't leave finished plans sitting as `active` - that's what makes
   rule 1 trustworthy for the next session instead of surfacing stale, already-done work.

## Model routing across the workflow

- **Research/exploration** (Explore agents, graph queries, reading files to gather material
  for a plan) -> Sonnet 5 (`claude-sonnet-5`).
- **Writing the plan itself** (turning research into the actual implementation plan) -> Opus 5
  (`claude-opus-5`).
- **Implementation** -> handed to Gemini (`gemini-3.1-flash-lite`), per the delegate section below.
- **Verification of what Gemini wrote** -> the impact sweep runs on Sonnet 5, the verdict
  is Opus 5's. See the Gemini section below.

## Delegating Work to Gemini

Ordinary implementation work may be handed to the Gemini CLI on `gemini-3.1-flash-lite`:

- Start from a clean tree (`git status --short` empty), so that afterwards `git diff` is
  exactly what the delegate wrote.
- Run it headless, task in a file to avoid quoting problems:
  `gemini -m gemini-3.1-flash-lite --approval-mode yolo -p "$(cat task.txt)"`.
  `yolo` is required - a headless run has nobody to answer a prompt, and `auto_edit`
  stalls on the first shell command.

### Reviewing the delegate's work

Opus 5 reviews, and it reviews the **actual changes** - never the delegate's own summary,
which is a claim rather than a result. The review is driven by the graph, not by reading
the diff in isolation:

1. Take the real changes from `git diff`: the files, classes, defines, functions and
   parameters that actually moved.
2. For every changed entity, query the graph (`search_code_nodes`,
   `get_code_graph_neighbors`) for everything attached to it - callers, includes, hiera
   keys, templates - and check whether the change breaks any of them.
3. That impact sweep is exploration, so it runs on Sonnet 5 (Explore agents / graph
   queries). Sonnet gathers evidence; it does not rule.
4. The verdict - accept, fix on top, or revert - is Opus 5's alone, taken on the
   evidence gathered.
