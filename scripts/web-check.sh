#!/usr/bin/env bash
# eslint and tsc over the dashboard sources, for the pre-commit hook.
#
# A sibling of mcp-check.sh rather than a shared script taking a directory:
# this one lints two languages and runs two tsc projects, and a failure has to
# name the tree it came from without the reader decoding an argument.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/web"

if [ ! -x node_modules/.bin/eslint ]; then
    echo "web/node_modules is missing. Run 'make web deps' first." >&2
    exit 1
fi

node_modules/.bin/eslint src
node_modules/.bin/tsc -p tsconfig.json --noEmit
node_modules/.bin/tsc -p tsconfig.client.json --noEmit
