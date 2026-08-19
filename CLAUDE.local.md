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

The plan-mode plan file (the path the harness names at the start of the session, under
`~/.claude/plans/`) is session-local and ephemeral - a different session/client can't
see it. The graph is the shared source of truth for plans across sessions.

**An active plan in the graph is an approved plan.** It carries `status: active` only
because the user already approved it via `ExitPlanMode`. Finding one is not an invitation
to re-check the work behind it - it is the instruction for what to do next. Execute it.

Four rules, all mandatory:

1. **Before anything else** on a non-trivial request - before any graph query, any Read,
   any Explore or Plan agent - call `get_plans` for the project you are working in,
   with the default `status: active`. The graph names a project after its root
   directory, and `list_projects` says what is indexed under which name, so check
   there rather than guessing. This is the first tool call of the turn, not something
   to get to eventually.
2. **If an active plan covers the request, act on it.** Follow its steps in order. Do NOT:

   - spawn Explore or Plan agents to "get oriented" first;
   - re-run the research the plan already summarizes;
   - re-verify the plan's conclusions, restate them back for approval, or re-enter plan
     mode to produce the same plan again;
   - read files the current step does not touch.

   **Once a plan is loaded, the plan defines the file set, and NOTHING OUTSIDE IT MAY
   BE TOUCHED. No Read, no Write, no Edit, no Grep sweep, no Explore agent doing any of
   those on a file the plan does not name.** The permitted set is exactly two things:
   the files the current step names, and the nodes the graph returns for the entity
   that step changes. That is the whole of it. Wandering the tree "to be sure" is the
   behaviour this rule exists to stop - the plan already did that work, and redoing it
   is how a finished plan turns back into an open-ended exploration.

   If a step genuinely cannot be implemented without a file the plan never names, that
   is a defect in the plan, not a licence to go browsing: name the file, say why it is
   needed, amend that one step, and continue from there. Ask first, read after - never
   the other way round.

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

Reusable procedures are a third status. A plan that is run on demand rather than finished
once is stored `template`, so it never appears in the `active` list rule 1 trusts. There is
one today: `ctx-file-selection-bootstrap` under project `claude-context-mcp` - generate
`.ctxkeep` / `.ctxignore` for a repository, verify the selection by simulation, and record
the formats no parser reads. Reach it from any project with `get_plans` at
`project: claude-context-mcp`, `status: template`, and follow it when asked to apply the
file selection plan to a repo.

## Recap: report how the work was actually done

Every time a task ends - implementation, review, research, anything that took more than
a single answer - close the reply with a **Recap** section stating how the answer was
produced, not just what it concluded. Counts, not adjectives. Five lines, in this order,
and skip a line only when its count is genuinely zero:

1. **Graph / `context` MCP** - total calls, broken down by tool and purpose:
   `get_plans` (the mandatory rule-1 check, and what it returned), `save_plan`
   (finalized / retired), `search_code_nodes` and `get_code_graph_neighbors` (what was
   being located), `get_node_summary`, `save_node_summary`, `clear_file_hash`,
   `list_indexed_files`. Say plainly when a lookup came back empty - that is the part
   that justifies the Read that followed.
2. **Direct `Read` / `Write` / `Edit`** - how many of each, on which files, **and why the
   graph was not enough**. The valid reasons are the ones the research policy above
   names: the question was about literal file content, the entity is not indexed, the
   index predates the tree, or the file was being written rather than researched. A Read
   with no reason given is a Read that should not have happened.
3. **Subagents** - one line each: name, model, what it was asked for, what it returned.
   Write "none" when none were spawned rather than omitting the line, since spawning one
   is a decision the user wants to see either way.
4. **External delegates** - the Gemini CLI runs: model, how many invocations, what the
   task file asked for, and how much of the result survived review. A delegate is not a
   subagent; keep the two counts separate and never merge them into one number.
5. **Suggestions** - what the graph or the context did not have, and what would fix it.
   Only gaps this turn actually hit: a lookup that came back empty, a summary that was
   missing or too thin to answer from, a file type that is not indexed, an index older
   than the tree, a question the tools cannot express. Name the lever each one moves -
   fewer tokens, wider coverage, or a shorter run - and the concrete change behind it: a
   summary to save, a `.ctxkeep` pattern to add, a parser that does not exist, a tool
   that is missing. Write "none" when the graph answered everything asked of it; a turn
   that re-derived half its answer by hand and still reports "none" is the one line the
   user will not believe.

The point is auditability of method: the user is comparing what the graph answered
against what was re-derived by hand, and cannot see the tool calls. So the recap is
written from what actually happened in the turn, never rounded, never reconstructed
from what the workflow says should have happened. The suggestions line is the other
half of that: line 2 says what had to be re-derived by hand, line 5 says what would have
made it unnecessary, so the tooling gets fixed instead of worked around every turn.

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
- Run it headless, task in a file to avoid quoting problems, and always with the
  workspace-trust variable exported:
  `GEMINI_CLI_TRUST_WORKSPACE=true gemini -m gemini-3.1-flash-lite --approval-mode yolo -p "$(cat task.txt)"`.
  `yolo` is required - a headless run has nobody to answer a prompt, and `auto_edit`
  stalls on the first shell command.
- **The env var is not optional.** Without it the CLI first downgrades the mode
  ("Approval mode overridden to \"default\" because the current folder is not trusted")
  and then refuses to run at all: "Gemini CLI is not running in a trusted directory".
  Every headless run in an untrusted checkout dies there, before reading the task.
  `--skip-trust` is the flag-shaped equivalent; trusting the folder interactively does
  not carry into a headless run.
- **The workspace is the only writable root.** An output path outside the repo - a
  session scratchpad under `/tmp`, for instance - is refused with "Path not in
  workspace" and the delegate quietly retargets the write to
  `~/.gemini/tmp/<project>/<name>`, reporting success. So either name an output file
  inside the repo, or read the result back from that fallback directory instead of
  concluding the run produced nothing.

### Reviewing the delegate's work

Opus 5 reviews, and it reviews the **actual changes** - never the delegate's own summary,
which is a claim rather than a result. The review is driven by the graph, not by reading
the diff in isolation:

1. Take the real changes from `git diff`: the files, classes, defines, functions and
   parameters that actually moved.
2. For every changed entity, query the graph (`search_code_nodes`,
   `get_code_graph_neighbors`) for everything attached to it - callers, imports,
   includes, templates, data keys, whatever relations that language has - and check
   whether the change breaks any of them.
3. That impact sweep is exploration, so it runs on Sonnet 5 (Explore agents / graph
   queries). Sonnet gathers evidence; it does not rule.
4. The verdict - accept, fix on top, or revert - is Opus 5's alone, taken on the
   evidence gathered.

## Code Style & Comments

- **Do NOT write code comments** by default.
- Comments are allowed **only** if the behavior or implementation is non-obvious, counterintuitive, or inherently illogical.
- **Strict comment limits**:
  - Maximum length: **2 lines**.
  - Line length limit: **79 characters** per line (including indentation and comment symbols).

## Commits: driving the real commitizen

Every commit is made by the real binary, `.venv/bin/cz commit` - there is no `git cz`
alias on this machine. Never `git commit -m`, never a message rendered by hand to look
like commitizen output, and never a reflow of what it emitted, whitespace included.

Which adapter answers depends on the repository, so read `.cz.yaml` first: its `name:`
key names one, and that adapter has to be installed for `cz` to start at all - a
configured but missing one fails with "The commiter has not been found in the system".

With the `wyld_cz` adapter (`name: wyld_cz`) there are five questions, in this order:

1. `Select the type of change:` - a list in the order `fix`, `feat`, `build`, `docs`,
   `refactor`; move down it with `\x1b[B`.
2. `What is the scope of this change (e.g. package, tools):` - the one module, script or
   document the commit is about.
3. `Write a short description:` - the subject line.
4. `Provide a longer description (optional):` - a single-line input, so the body is one
   paragraph; the adapter wraps and indents it.
5. `Link to issue (optional):` - normally empty.

The result is `[<type>][<scope>]: <subject>`, which `cz check` and the commit-msg hook
both enforce.

Without it - no `.cz.yaml`, or one naming an adapter this machine does not have -
commitizen uses its own `cz_conventional_commits`: a longer type list, then scope,
subject, body and footer, and the result is `<type>(<scope>): <subject>`. Drive that
form as it comes and do not fake the bracketed shape on top of it; the repository's
commit-msg hook is the authority on what is valid there. When `.cz.yaml` does ask for an
adapter that is absent, one `pip install` of it is worth trying, and
`cz --name cz_conventional_commits commit` is the way through if that fails.

It is interactive and needs a TTY, so drive it under `pty.fork`: strip ANSI from the
accumulated output, wait for the prompt substring, sleep ~0.5s, write the answer plus
`\r`, and clear the match buffer after each step. `git reset --soft HEAD~1` leaves the
files staged when the last commit has to be made again with a corrected message.
