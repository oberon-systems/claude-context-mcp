#!/usr/bin/env bash
# Onboard one codebase onto the stack in a single pass: agent configuration,
# the skills, the instruction file, the file-selection pair and the shell
# aliases.
#
# Reached through `make install`, which passes everything in the environment.
# Nothing here overwrites a file that exists - every step reports written,
# merged, kept or skipped, and the run ends with that list. Re-running it is
# how a piece added later gets picked up, never a way to reset one.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# `docker compose` is two words, and a plain "$COMPOSE" would be looked up as
# one binary of that name.
read -r -a compose <<< "${COMPOSE:-docker compose}"

target="${AGENT_ROOT:-$repo_root}"
make_bin="${MAKE_BIN:-make}"
make_prefix="${MAKE_PREFIX:-make -C $repo_root}"
python_bin="${PYTHON_BIN:-python3}"
shell_rc="${SHELL_RC:-$HOME/.bashrc}"
marker="# >>> claude-context-mcp >>>"
end_marker="# <<< claude-context-mcp <<<"

if [ ! -d "$target" ]; then
    echo "AGENT_ROOT=$target is not a directory" >&2
    exit 1
fi
target="$(cd "$target" && pwd)"

# The credentials and the port live in .env, which make does not source.
env_value() {
    sed -n "s/^$1=//p" .env 2> /dev/null | tail -1
}

port="$(env_value GATEWAY_PORT)"
port="${port:-3000}"

summary=()
note() {
    summary+=("$(printf '  %-22s %s' "$1" "$2")")
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "Onboarding $target"
echo

# 1. Scan the tree from inside the indexer image, so the file types it knows
#    about are the ones the parser tables actually list. No database is
#    touched, hence --no-deps: this runs before the stack is up.
echo "Selection"
project=""
if PROJECT_PATH="$target" PROJECT_NAME="${PROJECT_NAME:-}" \
        "${compose[@]}" --profile index run --rm --no-deps -T graphify \
        python -m ctxgraph.bootstrap > "$work/scan" 2> "$work/scan.err"; then
    awk -v dir="$work" '
        /^#--- [a-z]+ ---$/ { out = dir "/" $2; next }
        out { print > out }
    ' "$work/scan"
    project="$(tr -d '[:space:]' < "$work/name")"
    for name in ctxkeep ctxignore; do
        if [ -e "$target/.$name" ]; then
            note ".$name" "kept (already in the tree)"
        else
            cp "$work/$name" "$target/.$name"
            note ".$name" "written"
        fi
    done
    sed 's/^/  /' "$work/report"
else
    echo "  the scan failed, so no selection file was written:" >&2
    sed 's/^/  /' "$work/scan.err" >&2
    note ".ctxkeep" "skipped (scan failed)"
    note ".ctxignore" "skipped (scan failed)"
fi

# The scanner derives the name the same way the indexer does, which is what
# makes the /mcp/<project> address match the row indexing will create.
# Without it the basename is the best guess available.
if [ -z "$project" ]; then
    project="$(basename "$target" | tr '[:upper:]' '[:lower:]' \
        | tr -c 'a-z0-9._-' '-' | sed 's/^-*//; s/-*$//')"
    echo "  falling back to the basename for the project name: $project" >&2
fi

# 2. Both agents, one address.
echo
echo "Agents"
"$python_bin" scripts/mcp_register.py "$target" "$project" "$port"
note "agent configuration" "$project on port $port"

# 3. Every skill under skills/ is copied under AGENT_ROOT for Claude and
#    linked from there for Gemini, so both agents read the same file.
echo
echo "Skills"
# The gemini consent notice goes to stderr; folded in so the whole step is
# indented like the rest, and pipefail still reports a failure.
"$make_bin" --no-print-directory -C "$repo_root" skill-install \
    AGENT_ROOT="$target" 2>&1 | sed 's/^/  /'
note "skills" "installed for $target"

# 4. The instruction file. Claude reads CLAUDE.local.md beside its CLAUDE.md;
#    Gemini has no .local convention and reads GEMINI.md, so the same rendered
#    text goes to both names.
echo
echo "Instructions"
render() {
    sed -e "s|@MAKE@|$make_prefix|g" -e "s|@ROOT@|$target|g" \
        -e "s|@PROJECT@|$project|g" templates/CLAUDE.local.md
}

for name in CLAUDE.local.md GEMINI.md; do
    if [ "$name" = "GEMINI.md" ] && ! command -v gemini > /dev/null; then
        note "$name" "skipped (gemini is not installed)"
        continue
    fi
    if [ -e "$target/$name" ]; then
        note "$name" "kept (already in the tree)"
    else
        render > "$target/$name"
        note "$name" "written"
    fi
done
echo "  rendered from templates/CLAUDE.local.md"

# 5. The alias. One command onboards a tree and nothing else needs one:
#    indexing is a button in the dashboard now, and the skills come with the
#    onboarding rather than after it. Fenced by a pair of markers so it never
#    appends twice, and rewritten when it has fallen behind - which is how a
#    shell onboarded under the old seven aliases loses the six that no longer
#    point at anything.
echo
echo "Aliases"
alias_block() {
    cat <<BLOCK
$marker
alias context-install='make -C $repo_root install AGENT_ROOT=\$(pwd)'
$end_marker
BLOCK
}

# What the rc file holds today, markers included, so the two can be compared.
extract_block() {
    awk -v start="$marker" -v end="$end_marker" '
        $0 == start { inside = 1 }
        inside { print }
        $0 == end { inside = 0 }
    ' "$1"
}

# Only ever called with both markers present: the replacement runs from the
# opening one to the closing one, and without a closing one it would swallow
# everything after it.
replace_block() {
    awk -v start="$marker" -v end="$end_marker" -v block="$work/aliases" '
        $0 == start {
            while ((getline line < block) > 0) print line
            skipping = 1
            next
        }
        skipping && $0 == end { skipping = 0; next }
        !skipping
    ' "$1"
}

if [ "${ALIASES:-1}" = "0" ]; then
    note "shell aliases" "skipped (ALIASES=0)"
    echo "  not requested"
elif [ -f "$shell_rc" ] && grep -Fq "$marker" "$shell_rc" \
        && grep -Fq "$end_marker" "$shell_rc"; then
    alias_block > "$work/aliases"
    if extract_block "$shell_rc" | cmp -s - "$work/aliases"; then
        note "shell aliases" "kept (already current in $shell_rc)"
        echo "  already in $shell_rc"
    else
        # Rewritten through cat rather than mv, so the rc file keeps its own
        # inode and mode instead of inheriting the temporary file's.
        replace_block "$shell_rc" > "$work/rc"
        cat "$work/rc" > "$shell_rc"
        note "shell aliases" "updated in $shell_rc"
        echo "  refreshed in $shell_rc, active in the next shell"
    fi
elif [ -f "$shell_rc" ] && grep -Fq "$marker" "$shell_rc"; then
    note "shell aliases" "skipped (the block in $shell_rc has no closing marker)"
    echo "  $shell_rc opens the block but never closes it, so it cannot be"
    echo "  replaced safely. Fix the fence by hand with:"
    echo
    alias_block | sed 's/^/  /'
elif [ -f "$shell_rc" ] && grep -Eq \
        '^[[:space:]]*alias[[:space:]]+context-[a-z-]+=' "$shell_rc"; then
    note "shell aliases" "skipped (defined in $shell_rc without the marker)"
    echo "  $shell_rc already defines aliases of these names outside the"
    echo "  marker. Replace them by hand with:"
    echo
    alias_block | sed 's/^/  /'
else
    { echo; alias_block; } >> "$shell_rc"
    note "shell aliases" "written to $shell_rc"
    echo "  added to $shell_rc, active in the next shell"
fi

# 6. Whether the address just written answers anything yet.
echo
echo "Stack"
if curl -fsS "localhost:$port/health" > /dev/null 2>&1; then
    note "mcp server" "reachable on localhost:$port"
    echo "  reachable on localhost:$port"
else
    note "mcp server" "unreachable, run 'make up'"
    echo "  nothing answers on localhost:$port yet - run '$make_prefix up'."
    echo "  The address written into the agent files is not live until then."
fi

echo
echo "Summary"
printf '%s\n' "${summary[@]}"
