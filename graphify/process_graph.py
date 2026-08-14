"""Build a coarse code graph from a mounted codebase and store it in PostgreSQL.

Every source file becomes a `file` node. Every line that looks like an import
becomes an `external_import` node plus an `imports` edge from the file to it.
This is a line-level heuristic; the tree-sitter based AST pass is tracked in
ROADMAP.md and will replace `extract_import` without changing the schema.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

LOG = logging.getLogger("graphify")

PROJECT_PATH = os.getenv("TARGET_PROJECT_PATH", "/project")

# Directory names pruned from the walk. Matched on the basename, so a project
# directory that merely contains one of these strings in its path is kept.
IGNORED_DIRS = frozenset(
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

SOURCE_EXTENSIONS = (
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

# graph_nodes.id is VARCHAR(255); leave headroom rather than let a long line
# abort the insert.
MAX_NODE_ID_LENGTH = 200


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL, failing loudly when it is unset."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def iter_source_files(root_path: str) -> Iterator[tuple[str, str]]:
    """Yield (absolute path, path relative to root_path) for every source file."""
    for current_dir, dir_names, file_names in os.walk(root_path):
        # Slice assignment is what actually prunes the descent; `continue` on
        # the parent would still walk into the ignored subtree.
        dir_names[:] = [name for name in dir_names if name not in IGNORED_DIRS]

        for file_name in sorted(file_names):
            if not file_name.endswith(SOURCE_EXTENSIONS):
                continue
            full_path = os.path.join(current_dir, file_name)
            yield full_path, os.path.relpath(full_path, root_path)


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
