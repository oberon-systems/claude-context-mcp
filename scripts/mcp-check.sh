#!/usr/bin/env bash
# Lint and type-check the MCP server.
#
# Run from pre-commit and from `make lint`. The project's own node_modules are
# used rather than an isolated hook environment: eslint.config.mjs imports
# @eslint/js and typescript-eslint, and ESM resolves those relative to the
# config file, so they have to be installed next to it.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mcp_dir="$repo_root/mcp-server"

if [ ! -x "$mcp_dir/node_modules/.bin/eslint" ]; then
    echo "mcp-server dependencies are missing, run 'make mcp install' first" >&2
    exit 1
fi

cd "$mcp_dir"
./node_modules/.bin/eslint src
./node_modules/.bin/tsc --noEmit
