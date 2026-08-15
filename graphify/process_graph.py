"""Build a coarse code graph from a mounted codebase and store it in PostgreSQL.

Every indexed file becomes a `file` node. Every line of a source file that looks
like an import becomes an `external_import` node plus an `imports` edge from the
file to it. This is a line-level heuristic; the tree-sitter based AST pass is
tracked in ROADMAP.md and will replace `extract_import` without changing the
schema.

Which files are walked is decided by the project itself: a `.ctxignore` and a
`.ctxkeep` at the project root, both in gitignore syntax, replace the built-in
defaults below. They are read from the mounted project on every run, so changing
what a project indexes needs no image rebuild.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pathspec
import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

LOG = logging.getLogger("graphify")

PROJECT_PATH = os.getenv("TARGET_PROJECT_PATH", "/project")

IGNORE_FILE = ".ctxignore"
KEEP_FILE = ".ctxkeep"

# Directory names pruned from the walk when the project ships no .ctxignore.
# Matched on the basename, so a project directory that merely contains one of
# these strings in its path is kept.
DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "pgdata",
        "target",
        "venv",
    }
)

# Files that become nodes when the project ships no .ctxkeep.
DEFAULT_SOURCE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".rs",
    ".sql",
)

# Files whose lines are scanned for imports. Deliberately not configurable:
# this is what `extract_import` can parse, not a matter of project taste. A
# project that indexes its documentation through .ctxkeep must not have prose
# mined for the word "import", which would fill the graph with nonsense nodes.
IMPORT_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".rs",
)

# graph_nodes.id is VARCHAR(255); leave headroom rather than let a long line
# abort the insert.
MAX_NODE_ID_LENGTH = 200


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL, failing loudly when it is unset."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def load_spec(root_path: str, file_name: str) -> pathspec.PathSpec | None:
    """Return the PathSpec held in `file_name`, or None when it says nothing.

    Blank lines and `#` comments are dropped, so a file containing only those
    counts as absent and the built-in default applies.
    """
    path = os.path.join(root_path, file_name)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            lines = [
                line
                for line in (raw.strip() for raw in handle)
                if line and not line.startswith("#")
            ]
    except OSError:
        LOG.exception("Failed to read %s, falling back to the defaults", file_name)
        return None

    if not lines:
        return None

    LOG.info("Using %s from the project (%d patterns)", file_name, len(lines))
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def iter_source_files(root_path: str) -> Iterator[tuple[str, str]]:
    """Yield (absolute path, path relative to root_path) for every indexed file.

    A project's .ctxignore and .ctxkeep replace the built-in lists outright
    rather than adding to them, so a .ctxignore that forgets `.git/` really does
    walk into it. That case is loud rather than silent, see below.
    """
    ignore_spec = load_spec(root_path, IGNORE_FILE)
    keep_spec = load_spec(root_path, KEEP_FILE)

    if ignore_spec is not None and not ignore_spec.match_file(".git/"):
        LOG.warning(
            "%s does not exclude .git/, so git internals will be indexed; "
            "add a .git/ line unless that is intended",
            IGNORE_FILE,
        )

    for current_dir, dir_names, file_names in os.walk(root_path):
        rel_dir = os.path.relpath(current_dir, root_path)
        rel_dir = "" if rel_dir == "." else rel_dir

        # Slice assignment is what actually prunes the descent; `continue` on
        # the parent would still walk into the ignored subtree.
        if ignore_spec is None:
            dir_names[:] = [
                name for name in dir_names if name not in DEFAULT_IGNORED_DIRS
            ]
        else:
            # The trailing slash is what lets a `build/` pattern match the
            # directory rather than only a file of that name.
            dir_names[:] = [
                name
                for name in dir_names
                if not ignore_spec.match_file(f"{os.path.join(rel_dir, name)}/")
            ]

        for file_name in sorted(file_names):
            rel_path = os.path.join(rel_dir, file_name)
            if keep_spec is None:
                if not file_name.endswith(DEFAULT_SOURCE_EXTENSIONS):
                    continue
            elif not keep_spec.match_file(rel_path):
                continue
            if ignore_spec is not None and ignore_spec.match_file(rel_path):
                continue
            yield os.path.join(current_dir, file_name), rel_path


def extract_import(line: str) -> str | None:
    """Return a normalized import node id for `line`, or None when it is not one."""
    stripped = line.strip()
    if "import " not in stripped and "require(" not in stripped:
        return None
    # A line ending in an opening bracket is the head of a multi-line import;
    # storing it would create a node like `import {` that names nothing. The
    # AST pass in ROADMAP.md removes the need for this guard.
    if stripped.endswith(("{", "(")):
        return None
    normalized = " ".join(stripped.split())
    return normalized[:MAX_NODE_ID_LENGTH] or None


def index_file(cursor: Cursor, full_path: str, rel_path: str) -> int:
    """Insert the file node and its import edges. Returns the edge count."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path)
        VALUES (%s, %s, 'file', %s)
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;
        """,
        (rel_path, os.path.basename(rel_path), rel_path),
    )

    edges = 0
    # Documentation and configuration reach this point through .ctxkeep; only
    # the languages `extract_import` understands are mined for imports.
    if not rel_path.endswith(IMPORT_EXTENSIONS):
        return edges

    with open(full_path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            import_id = extract_import(line)
            if import_id is None:
                continue

            cursor.execute(
                """
                INSERT INTO graph_nodes (id, name, type)
                VALUES (%s, 'import', 'external_import')
                ON CONFLICT (id) DO NOTHING;
                """,
                (import_id,),
            )
            cursor.execute(
                """
                INSERT INTO graph_edges (source_id, target_id, relation_type)
                VALUES (%s, %s, 'imports')
                ON CONFLICT DO NOTHING;
                """,
                (rel_path, import_id),
            )
            edges += 1

    return edges


def scan_and_build_graph() -> None:
    """Walk the mounted project and persist its graph."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError(f"target project path {PROJECT_PATH} is not a directory")

    LOG.info("Scanning codebase at %s", PROJECT_PATH)
    files = 0
    edges = 0
    failures = 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            for full_path, rel_path in iter_source_files(PROJECT_PATH):
                try:
                    edges += index_file(cursor, full_path, rel_path)
                except (OSError, psycopg2.Error):
                    # One unreadable file or one rejected row must not discard
                    # the work already committed for the rest of the tree.
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to index %s", rel_path)
                    continue
                conn.commit()
                files += 1
    finally:
        conn.close()

    LOG.info(
        "Indexing completed: %d files, %d import edges, %d failures",
        files,
        edges,
        failures,
    )


def main() -> None:
    """Configure logging and run the indexing pass."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    scan_and_build_graph()


if __name__ == "__main__":
    main()
