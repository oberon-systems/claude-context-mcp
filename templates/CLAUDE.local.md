# CLAUDE.local.md

Written by `make install` from the claude-context-mcp template. This project is
indexed as `@PROJECT@`, and the `context` MCP server answers from that graph.
Edit freely - `make install` never overwrites this file once it exists.

## 1. Use the `context` skill

Load the `context` skill at the start of any non-trivial request and follow
it: plans before anything else, the graph before Read and Grep, open questions
answered from the context alone, memory, the suggestion backlog, and the recap
that closes the task. It is not repeated here.

## 2. Installed skills

`make install` installed four skills for Claude and linked the same four to
Gemini, loaded on demand: `context` (everything above), `commit` (driving
commitizen), `delegate` (handing work to the Gemini CLI and reviewing what it
wrote), `write-docs` (the house style for documentation, and the linter that
gates it). Reach for one by name.

## 3. Commentaries

DO NOT write commentaries in common.
You can write comments ONLY for non-obvious ones places / parts of code and
even in this cases ALLOWED TO WRITE NOT MORE THEN 2 STRINGS!

## 4. Tests and verification

ALWAYS ask for run tests, up test stands and run code verifications (linters,
pre-commit hooks) event if there is GLOBAL auto-allowance set for the
current session.
