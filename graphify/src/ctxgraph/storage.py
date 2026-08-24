"""Every statement the indexer sends to PostgreSQL.

One database holds several projects, so every statement here is scoped to one.
The scope is passed in rather than read from the configuration: the caller
already knows which tree it is walking, and a module level default is exactly
what would let one project's re-index quietly delete another's rows.
"""

from __future__ import annotations

import json
import os
import posixpath

import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    BUILTIN_PROJECT_TYPES,
    DEFAULT_PROJECT_TYPE,
    MAX_NAME_LENGTH,
    MAX_NODE_ID_LENGTH,
    MAX_TYPE_LENGTH,
    SOURCE_NATIVE,
)
from ctxgraph.identifiers import entity_node_id, truncate


def get_db_url() -> str:
    """Read DATABASE_URL, or say which variable is missing."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return db_url


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL."""
    return psycopg2.connect(get_db_url())


def ensure_project(
    cursor: Cursor,
    project: str,
    root_path: str,
    project_type: str | None = None,
) -> None:
    """Register the project being indexed, or refresh when it was.

    Both directions of the name/path pairing are checked first, and neither is
    repaired silently. A name pointing at a new path means two checkouts share
    a directory name, and letting the second one through would merge two
    unrelated codebases into one graph. A path arriving under a new name means
    a rename, which is legitimate but has to move the existing rows rather
    than orphan them, so it is refused here rather than half done.

    `project_type` categorises the project for the cross-project MCP search.
    None means "leave whatever is stored alone", so a plain re-index does not
    demote a project that was registered as something other than the default.
    """
    cursor.execute("SELECT root_path, type FROM projects WHERE name = %s;", (project,))
    row = cursor.fetchone()
    if row is not None and row[0] != root_path:
        raise RuntimeError(
            f"project {project!r} is already indexed from {row[0]!r}; "
            f"pass PROJECT_NAME to index {root_path!r} under another name"
        )
    # A built-in project holds records written through the MCP tools; there
    # is no tree behind it, and an index run would prune every one of them.
    if row is not None and row[1] in BUILTIN_PROJECT_TYPES:
        raise RuntimeError(
            f"project {project!r} holds agent {row[1]}, not an indexed tree; "
            f"indexing into it would delete every record it holds"
        )

    cursor.execute("SELECT name FROM projects WHERE root_path = %s;", (root_path,))
    row = cursor.fetchone()
    if row is not None and row[0] != project:
        raise RuntimeError(
            f"{root_path!r} is already indexed as {row[0]!r}; "
            f"rename it in the projects table before indexing it as {project!r}"
        )

    cursor.execute(
        """
        INSERT INTO projects (name, root_path, indexed_at, type)
        VALUES (%s, %s, CURRENT_TIMESTAMP, COALESCE(%s, %s))
        ON CONFLICT (name) DO UPDATE SET
            indexed_at = CURRENT_TIMESTAMP,
            type = COALESCE(%s, projects.type);
        """,
        (project, root_path, project_type, DEFAULT_PROJECT_TYPE, project_type),
    )


def list_projects(cursor: Cursor) -> list[tuple[str, str, str, int]]:
    """Read every indexed project with its root, its type and its node count."""
    cursor.execute(
        """
        SELECT p.name, p.root_path, p.type, COUNT(n.id)
          FROM projects AS p
          LEFT JOIN graph_nodes AS n ON n.project = p.name
         GROUP BY p.name, p.root_path, p.type
         ORDER BY p.name;
        """
    )
    return cursor.fetchall()


def upsert_file_node(
    cursor: Cursor,
    project: str,
    rel_path: str,
    summary: str,
    source: str = SOURCE_NATIVE,
    content: str = "",
) -> None:
    """Insert or refresh the node standing for a file.

    A summary written through the MCP `save_node_summary` tool is marked
    manual in the metadata and survives re-indexing; the generated one does
    not, so it keeps up with the file.

    `source` records which producer found the file. It is merged rather than
    replaced so a node that already carries a manual summary keeps the rest of
    its metadata.
    """
    cursor.execute(
        """
        INSERT INTO graph_nodes (
            project, id, name, type, file_path, summary, content, metadata
        )
        VALUES (
            %s, %s, %s, 'file', %s, %s, %s,
            JSONB_BUILD_OBJECT('summary_source', 'auto', 'source', %s)
        )
        ON CONFLICT (project, id) DO UPDATE SET
            name = EXCLUDED.name,
            type = 'file',
            file_path = EXCLUDED.file_path,
            content = COALESCE(EXCLUDED.content, graph_nodes.content),
            metadata = graph_nodes.metadata || JSONB_BUILD_OBJECT('source', %s),
            summary = CASE
                WHEN COALESCE(
                    graph_nodes.metadata ->> 'summary_source', 'auto'
                ) = 'auto'
                THEN EXCLUDED.summary
                ELSE graph_nodes.summary
            END;
        """,
        (
            project,
            truncate(rel_path, MAX_NODE_ID_LENGTH),
            truncate(posixpath.basename(rel_path), MAX_NAME_LENGTH),
            rel_path,
            summary,
            content or None,
            source,
            source,
        ),
    )


def store_file_content(
    cursor: Cursor, project: str, rel_path: str, content: str
) -> None:
    """Write a file node's text without re-parsing the file.

    An unchanged file skips indexing entirely, so a tree indexed before
    content was stored would never fill the column for the files our own
    parsers own - the extractor rewrites its half on every run regardless.
    """
    if not content:
        return
    cursor.execute(
        """
        UPDATE graph_nodes SET content = %s
         WHERE project = %s AND id = %s AND type = 'file'
           AND content IS DISTINCT FROM %s;
        """,
        (content, project, truncate(rel_path, MAX_NODE_ID_LENGTH), content),
    )


def clear_file_artifacts(cursor: Cursor, project: str, rel_path: str) -> None:
    """Drop what a previous run derived from a file.

    Without this an entity or a call that was deleted from the source stays in
    the graph forever, because every write below is an upsert.
    """
    file_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE project = %s AND file_path = %s AND type <> 'file';
        """,
        (project, rel_path),
    )
    cursor.execute(
        "DELETE FROM graph_edges WHERE project = %s AND source_id = %s;",
        (project, file_id),
    )


def prune_orphans(cursor: Cursor, project: str) -> int:
    """Delete placeholder nodes nothing points at any more.

    An import or a call that was removed from the source leaves its external
    node behind, and releases before entity ids were file scoped wrote entity
    nodes without a file_path. Both are dead weight in every search result.
    """
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE project = %s AND ((
            type IN ('external_import', 'external_symbol')
            AND NOT EXISTS (
                SELECT 1 FROM graph_edges
                 WHERE project = graph_nodes.project
                   AND target_id = graph_nodes.id
            )
        ) OR (
            file_path IS NULL
            AND type NOT IN ('file', 'external_import', 'external_symbol')
        ));
        """,
        (project,),
    )
    return cursor.rowcount


def prune_missing_files(cursor: Cursor, project: str, known_paths: list[str]) -> int:
    """Delete everything derived from a file that is no longer in the tree.

    A re-index only visits the files it finds, so a file that was renamed or
    deleted is never reached by the per-file cleanup and its nodes outlive it.
    The set of files just discovered is the only thing that knows they are
    gone.
    """
    if not known_paths:
        return 0
    cursor.execute(
        "DELETE FROM graph_nodes WHERE project = %s AND file_path IS NOT NULL "
        "AND NOT (file_path = ANY(%s));",
        (project, known_paths),
    )
    removed = cursor.rowcount
    cursor.execute(
        "DELETE FROM file_hashes WHERE project = %s AND NOT (file_path = ANY(%s));",
        (project, known_paths),
    )
    return removed


def ensure_external_node(
    cursor: Cursor, project: str, node_id: str, node_type: str
) -> None:
    """Create a placeholder node for a target defined outside the project."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (project, id, name, type)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (project, id) DO NOTHING;
        """,
        (project, node_id, truncate(node_id, MAX_NAME_LENGTH), node_type),
    )


def upsert_entity_node(
    cursor: Cursor,
    project: str,
    rel_path: str,
    entity: dict[str, str],
    source: str = SOURCE_NATIVE,
) -> None:
    """Insert or refresh the node standing for something a file declares."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (project, id, name, type, file_path, metadata)
        VALUES (%s, %s, %s, %s, %s, JSONB_BUILD_OBJECT('source', %s))
        ON CONFLICT (project, id) DO UPDATE SET
            name = EXCLUDED.name,
            type = EXCLUDED.type,
            file_path = EXCLUDED.file_path,
            metadata = graph_nodes.metadata || EXCLUDED.metadata;
        """,
        (
            project,
            entity_node_id(rel_path, entity["name"]),
            truncate(entity["name"], MAX_NAME_LENGTH),
            truncate(entity["type"], MAX_TYPE_LENGTH),
            rel_path,
            source,
        ),
    )


def upsert_extracted_node(
    cursor: Cursor,
    project: str,
    node_id: str,
    name: str,
    node_type: str,
    file_path: str | None,
    summary: str,
    metadata: dict[str, object],
) -> None:
    """Insert or refresh a node whose id was chosen by the caller.

    `upsert_entity_node` derives the id from the owning file and the entity
    name, which is enough for our own parsers. graphifyy names a node after
    its label alone, and those labels repeat across a tree, so the caller has
    to build a unique id and pass it in here.
    """
    cursor.execute(
        """
        INSERT INTO graph_nodes (
            project, id, name, type, file_path, summary, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::JSONB)
        ON CONFLICT (project, id) DO UPDATE SET
            name = EXCLUDED.name,
            type = EXCLUDED.type,
            file_path = EXCLUDED.file_path,
            metadata = graph_nodes.metadata || EXCLUDED.metadata,
            summary = CASE
                WHEN COALESCE(
                    graph_nodes.metadata ->> 'summary_source', 'auto'
                ) = 'auto'
                THEN EXCLUDED.summary
                ELSE graph_nodes.summary
            END;
        """,
        (
            project,
            truncate(node_id, MAX_NODE_ID_LENGTH),
            truncate(name, MAX_NAME_LENGTH),
            truncate(node_type, MAX_TYPE_LENGTH),
            file_path,
            summary,
            json.dumps(metadata),
        ),
    )


def clear_producer_artifacts(cursor: Cursor, project: str, source: str) -> int:
    """Drop what one producer wrote, leaving the other producer's rows alone.

    File nodes are kept: they are the anchor both producers and the MCP
    summary tools address, and re-creating them would drop a manual summary
    that is supposed to outlive re-indexing. Their entities go, because an
    entity deleted from the source has no other way out of the graph.
    """
    cursor.execute(
        """
        DELETE FROM graph_edges
        WHERE project = %s AND metadata ->> 'source' = %s;
        """,
        (project, source),
    )
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE project = %s AND metadata ->> 'source' = %s AND type <> 'file';
        """,
        (project, source),
    )
    return cursor.rowcount


def get_file_hash(cursor: Cursor, project: str, rel_path: str) -> str | None:
    """Retrieve the stored MD5 hash for a file."""
    cursor.execute(
        "SELECT hash FROM file_hashes WHERE project = %s AND file_path = %s;",
        (project, rel_path),
    )
    result = cursor.fetchone()
    return result[0] if result else None


def save_llm_summary(cursor: Cursor, project: str, rel_path: str, summary: str) -> bool:
    """Replace a generated summary with the model's. Returns whether it was.

    A summary written through `save_node_summary` is marked manual and is not
    touched. Everything else is, which is what lets the backfill improve a
    summary a plain index run wrote from the head of the file.
    """
    cursor.execute(
        """
        UPDATE graph_nodes
           SET summary = %s,
               metadata = metadata || '{"summary_source": "llm"}'::JSONB
         WHERE project = %s AND id = %s
           AND COALESCE(metadata ->> 'summary_source', 'auto') <> 'manual';
        """,
        (summary, project, truncate(rel_path, MAX_NODE_ID_LENGTH)),
    )
    return cursor.rowcount > 0


def list_files_without_llm_summary(
    cursor: Cursor, project: str, refresh: bool = False
) -> list[str]:
    """List the file nodes no model has described yet.

    `refresh` widens it to every file node the model may write to, which is
    all of them but the ones a human wrote through `save_node_summary`.
    """
    cursor.execute(
        """
        SELECT file_path FROM graph_nodes
         WHERE project = %s AND type = 'file' AND file_path IS NOT NULL
           AND COALESCE(metadata ->> 'summary_source', 'auto')
               = ANY(CASE WHEN %s THEN ARRAY['auto', 'llm'] ELSE ARRAY['auto'] END)
         ORDER BY file_path;
        """,
        (project, refresh),
    )
    return [row[0] for row in cursor.fetchall()]


def list_pending_summaries(
    cursor: Cursor, project: str, refresh: bool = False
) -> list[tuple[str, str]]:
    """List the same file nodes with the text stored for each of them.

    The pass over every project cannot read from disk - one tree is mounted
    and the rest are elsewhere - so it reads the column the worker API already
    serves. A file with no stored text comes back with an empty one rather
    than being left out, so the caller can say how many need re-indexing.
    """
    cursor.execute(
        """
        SELECT file_path, COALESCE(content, '') FROM graph_nodes
         WHERE project = %s AND type = 'file' AND file_path IS NOT NULL
           AND COALESCE(metadata ->> 'summary_source', 'auto')
               = ANY(CASE WHEN %s THEN ARRAY['auto', 'llm'] ELSE ARRAY['auto'] END)
         ORDER BY file_path;
        """,
        (project, refresh),
    )
    return [(row[0], row[1]) for row in cursor.fetchall()]


def get_cached_summary(cursor: Cursor, project: str, content_hash: str) -> str | None:
    """Retrieve the summary the model wrote for this exact text, if any."""
    cursor.execute(
        """
        SELECT summary FROM summary_cache
        WHERE project = %s AND content_hash = %s;
        """,
        (project, content_hash),
    )
    result = cursor.fetchone()
    return result[0] if result else None


def put_cached_summary(
    cursor: Cursor, project: str, content_hash: str, summary: str
) -> None:
    """Store what the model answered, so the next run reads it instead."""
    cursor.execute(
        """
        INSERT INTO summary_cache (project, content_hash, summary, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (project, content_hash) DO UPDATE SET
            summary = EXCLUDED.summary,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (project, content_hash, summary),
    )


def upsert_file_hash(
    cursor: Cursor, project: str, rel_path: str, file_hash: str
) -> None:
    """Store or update the MD5 hash for a file."""
    cursor.execute(
        """
        INSERT INTO file_hashes (project, file_path, hash, updated_at)
        VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (project, file_path) DO UPDATE SET
            hash = EXCLUDED.hash,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (project, rel_path, file_hash),
    )


def get_file_entities(
    cursor: Cursor, project: str, rel_path: str
) -> list[dict[str, str]]:
    """Retrieve existing entities for a file."""
    cursor.execute(
        """
        SELECT id, name, type FROM graph_nodes
        WHERE project = %s AND file_path = %s AND type <> 'file';
        """,
        (project, rel_path),
    )
    return [{"id": row[0], "name": row[1], "type": row[2]} for row in cursor.fetchall()]


def insert_edge(
    cursor: Cursor,
    project: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    metadata: dict[str, object] | None = None,
) -> None:
    """Record one relation, ignoring a repeat of an edge already stored."""
    cursor.execute(
        """
        INSERT INTO graph_edges (
            project, source_id, target_id, relation_type, metadata
        )
        VALUES (%s, %s, %s, %s, %s::JSONB)
        ON CONFLICT DO NOTHING;
        """,
        (
            project,
            source_id,
            target_id,
            truncate(relation_type, MAX_TYPE_LENGTH),
            json.dumps(metadata or {"source": SOURCE_NATIVE}),
        ),
    )


def iter_nodes(
    cursor: Cursor, project: str
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Read every node, for rebuilding the graph outside the database."""
    cursor.execute(
        """
        SELECT id, name, type, file_path, summary,
               COALESCE(metadata ->> 'community', '')
          FROM graph_nodes
         WHERE project = %s;
        """,
        (project,),
    )
    return cursor.fetchall()


def iter_edges(cursor: Cursor, project: str) -> list[tuple[str, str, str, str]]:
    """Read every edge, for rebuilding the graph outside the database."""
    cursor.execute(
        """
        SELECT source_id, target_id, relation_type,
               COALESCE(metadata ->> 'confidence', 'EXTRACTED')
          FROM graph_edges
         WHERE project = %s;
        """,
        (project,),
    )
    return cursor.fetchall()


def store_communities(
    cursor: Cursor, project: str, communities: dict[int, list[str]]
) -> int:
    """Write the community each node was clustered into back onto the node.

    Clustering runs over the merged graph, so this is what lets a node found
    by one producer share a community with nodes found by the other.
    """
    pairs = [
        (node_id, str(community_id))
        for community_id, members in communities.items()
        for node_id in members
    ]
    for node_id, community_id in pairs:
        cursor.execute(
            """
            UPDATE graph_nodes
               SET metadata = metadata || JSONB_BUILD_OBJECT('community', %s)
             WHERE project = %s AND id = %s;
            """,
            (community_id, project, node_id),
        )
    return len(pairs)
