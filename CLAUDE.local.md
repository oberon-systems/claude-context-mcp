# CLAUDE.local.md

## 1. Use the `context` skill

Load the `context` skill at the start of any non-trivial request and follow
it: plans before anything else, the graph before Read and Grep, open questions
answered from the context alone, memory, the suggestion backlog, and the recap
that closes the task. It is not repeated here.

Only what the skill cannot know about this repository:

- A node with no summary: delegate the summary to `gemini-3.1-flash-lite`,
  then persist it with `save_node_summary` - right after every Explore-agent
  run, for every node it touched.
- One saved procedure, global and `type: procedure`:
  `ctx-file-selection-bootstrap`, which generates and verifies
  `.ctxkeep` / `.ctxignore` for a repository.

## 2. Routing and style

- Research and impact sweeps -> Sonnet 5. Writing the plan and every verdict ->
  Opus 5. Implementation -> `gemini-3.1-flash-lite`.
- **No code comments** by default. Only where the behaviour is non-obvious or
  counterintuitive, and then at most 2 lines, at most 79 characters each.
- Committing -> the `commit` skill; never `git commit -m` or a hand-written
  message. Handing work to Gemini and reviewing it -> the `delegate` skill.
  Writing or restructuring any documentation -> the `write-docs` skill.
