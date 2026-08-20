#!/usr/bin/env bash
# Batch files must keep CRLF line endings. cmd.exe parses a .bat line by line
# as it runs it, and a file with bare LF mis-reads labels, goto targets and
# any line continued with a caret - failures that only appear on Windows, and
# only at the line reached. There is no linter for batch in the pre-commit
# ecosystem, so this is the hook that covers the extension.
set -euo pipefail

status=0
for file in "$@"; do
    if ! awk 'BEGIN { ok = 1 } !/\r$/ { ok = 0 } END { exit ok ? 0 : 1 }' "$file"; then
        echo "$file: batch files must use CRLF line endings" >&2
        status=1
    fi
done
exit "$status"
