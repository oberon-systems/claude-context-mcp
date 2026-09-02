#!/usr/bin/env bash
# Onboard one codebase onto the stack in a single pass: agent configuration
# and the permission that spares it a prompt per call, the skills, the
# instruction file, the file-selection pair and the shell aliases.
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
# Which directory of the tree the project actually reads. Usually the whole of
# it; a slice when a monorepo is onboarded one piece at a time, and the literal
# "none" when the project is registered ahead of every directory it will read.
source_dir="${SOURCE:-$target}"
make_bin="${MAKE_BIN:-make}"
make_prefix="${MAKE_PREFIX:-make -C $repo_root}"
python_bin="${PYTHON_BIN:-python3}"
shell_rc="${SHELL_RC:-$HOME/.bashrc}"
# Whether a file already in the tree should be replaced by what this version
# would write. Without FORCE nothing is touched, which is what makes a re-run
# safe. With it, anything that differs is shown and asked about one file at a
# time: a yes here overwrites work someone did by hand.
offer() {
    local label="$1" existing="$2" candidate="$3" reply
    if [ ! -e "$existing" ]; then
        return 0
    fi
    if [ -z "${FORCE:-}" ]; then
        note "$label" "kept (already in the tree, FORCE=1 offers to replace)"
        return 1
    fi
    if cmp -s "$existing" "$candidate"; then
        note "$label" "kept (already current)"
        return 1
    fi
    # Opened rather than tested: /dev/tty exists as a path even where there is
    # no controlling terminal to open, and the test would pass while the read
    # printed an error and answered nothing.
    if ! { exec 3< /dev/tty; } 2> /dev/null; then
        note "$label" "kept (differs, but there is no terminal to ask at)"
        return 1
    fi
    echo
    echo "  $label differs from what this version writes:"
    diff -u "$existing" "$candidate" 2> /dev/null | tail -n +3 | sed 's/^/    /'
    printf '  replace %s? [y/N] ' "$label"
    read -r reply <&3 || reply=""
    exec 3<&-
    echo
    case "$reply" in
        [yY]*) return 0 ;;
        *) note "$label" "kept (declined)"; return 1 ;;
    esac
}

marker="# >>> claude-context-mcp >>>"
end_marker="# <<< claude-context-mcp <<<"

if [ ! -d "$target" ]; then
    echo "AGENT_ROOT=$target is not a directory" >&2
    exit 1
fi
target="$(cd "$target" && pwd)"
if [ "$source_dir" != "none" ]; then
    if [ ! -d "$source_dir" ]; then
        echo "SOURCE=$source_dir is not a directory" >&2
        exit 1
    fi
    source_dir="$(cd "$source_dir" && pwd)"
fi

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
# The name comes from AGENT_ROOT even when the directory being scanned is one
# inside it, so the /mcp/<project> address written below is the one the mount
# step registers the row under.
scan_name="${PROJECT_NAME:-$(basename "$target")}"
if [ "$source_dir" = "none" ]; then
    echo "  the project reads no directory yet, so there is nothing to select"
    note ".ctxkeep" "skipped (no directory yet)"
    note ".ctxignore" "skipped (no directory yet)"
elif PROJECT_PATH="$source_dir" PROJECT_NAME="$scan_name" \
        "${compose[@]}" --profile index run --rm --no-deps -T graphify \
        python -m ctxgraph.bootstrap > "$work/scan" 2> "$work/scan.err"; then
    awk -v dir="$work" '
        /^#--- [a-z]+ ---$/ { out = dir "/" $2; next }
        out { print > out }
    ' "$work/scan"
    project="$(tr -d '[:space:]' < "$work/name")"
    for name in ctxkeep ctxignore; do
        if offer ".$name" "$source_dir/.$name" "$work/$name"; then
            cp "$work/$name" "$source_dir/.$name"
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

if [ "$source_dir" = "none" ]; then
    note "directory" "none yet, add one with context-source"
elif [ -n "${ALIAS:-}" ]; then
    note "directory" "$source_dir as '$ALIAS'"
else
    note "directory" "$source_dir"
fi

# 2. Both agents, one address - and, for Claude, the standing permission its
#    Gemini counterpart already gets from `trust: true`. That one lives in the
#    user's settings rather than the codebase's, so it is granted once and
#    holds for every project; PERMISSIONS=0 leaves the file alone and every
#    call goes on asking.
echo
echo "Agents"
claude_settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
"$python_bin" scripts/mcp_register.py "$target" "$project" "$port"
note "agent configuration" "$project on port $port"
if [ "${PERMISSIONS:-1}" = "0" ]; then
    note "claude permissions" "skipped (PERMISSIONS=0)"
else
    note "claude permissions" "$claude_settings"
fi

# 3. Every skill under skills/ is copied under AGENT_ROOT for Claude and
#    linked from there for Gemini, so both agents read the same file.
echo
echo "Skills"
# The gemini consent notice goes to stderr; folded in so the whole step is
# indented like the rest, and pipefail still reports a failure.
# FORCE also drops skills that went away, which a plain install cannot do:
# it copies what exists and leaves anything renamed behind.
skill_target="skill-install"
[ -z "${FORCE:-}" ] || skill_target="skill-reinstall"
"$make_bin" --no-print-directory -C "$repo_root" "$skill_target" \
    AGENT_ROOT="$target" 2>&1 | sed 's/^/  /'
note "skills" "installed for $target"

# 4. The instruction file. Claude reads CLAUDE.local.md beside its CLAUDE.md,
#    which is private and always written. Gemini has no .local convention, so
#    the same text goes to GEMINI.md - but only when a codebase has none, since
#    that name is where its own instructions to Gemini live.
echo
echo "Instructions"
render() {
    sed -e "s|@MAKE@|$make_prefix|g" -e "s|@ROOT@|$target|g" \
        -e "s|@PROJECT@|$project|g" -e "s|@FILE@|$1|g" templates/CLAUDE.local.md
}

for name in CLAUDE.local.md GEMINI.md; do
    if [ "$name" = "GEMINI.md" ]; then
        if ! command -v gemini > /dev/null; then
            note "$name" "skipped (gemini is not installed)"
            continue
        fi
        # GEMINI.md is Gemini's counterpart of CLAUDE.md, not of
        # CLAUDE.local.md: one already in the tree is the codebase's own.
        if [ -e "$target/$name" ]; then
            note "$name" "kept (the project's own instructions)"
            continue
        fi
    fi
    render "$name" > "$work/$name"
    if offer "$name" "$target/$name" "$work/$name"; then
        cp "$work/$name" "$target/$name"
        note "$name" "written"
    fi
done
echo "  rendered from templates/CLAUDE.local.md"

# 5. The aliases. Onboarding a whole tree is one command and always was;
#    the other three are what a project reading several directories needs:
#    one to register it before it reads anything, one to hand it the directory
#    the shell is standing in, one to take that back. Fenced by a pair of
#    markers so it never appends twice, and rewritten when it has fallen
#    behind - which is how a shell onboarded under the old aliases picks these
#    up on its next install.
echo
echo "Aliases"
alias_block() {
    cat <<BLOCK
$marker
alias context-install='make -C $repo_root install AGENT_ROOT=\$(pwd)'
alias context-project='make -C $repo_root install AGENT_ROOT=\$(pwd) SOURCE=none'
alias context-sources='make -C $repo_root sources'
context-source() {
    make -C $repo_root source-add PROJECT="\$(pwd)" \\
        PROJECT_NAME="\${1:?usage: context-source <project> [alias]}" \\
        ALIAS="\${2:-\$(basename "\$(pwd)")}"
}
context-source-drop() {
    make -C $repo_root source-drop \\
        PROJECT_NAME="\${1:?usage: context-source-drop <project> <alias>}" \\
        ALIAS="\${2:?usage: context-source-drop <project> <alias>}"
}
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
