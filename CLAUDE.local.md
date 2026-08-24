# CLAUDE.local.md

Written by `make install` from the claude-context-mcp template. This project is
indexed as `claude-context-mcp`, and the `context` MCP server answers from that graph.
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

## 5. Secrets and Passwords

NO NOT DECRYPT ANY VAULTS, DO NOT OPEN SOPS VAULTS, DO NOT READ .env FILES, DO
NOT STORE PASSWORDS OR SECRETS. ASK FROM USER IF YOU NEEDED!

## 6. Automation and Clouds

DO NOT RUN APPLY WITH TERRAFORM, TOFU, TERRAGRUNT ETC. DO NOT USE AWS, GCP, OR
MS AZURE CLI TOOLS! IF YOU NEED IT -- ASK FOR EACH ACTION PERMISSION FROM USER!

## 7. DATABASES

NEVER INTERACT WITH DATABASES DIRECTLY, NEVER RUN ANY QUERIES. IF
YOU NEED SOME -- ASK PERMISSION FOR EACH QUERY FROM USER.
