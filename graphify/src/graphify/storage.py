"""Every statement the indexer sends to PostgreSQL."""

from __future__ import annotations

import os
import posixpath

import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

from graphify.config import MAX_NAME_LENGTH, MAX_NODE_ID_LENGTH, MAX_TYPE_LENGTH
from graphify.identifiers import entity_node_id, truncate


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def upsert_file_node(cursor: Cursor, rel_path: str, summary: str) -> None:
    """Insert or refresh the node standing for a file.

    A summary written through the MCP `save_node_summary` tool is marked
    manual in the metadata and survives re-indexing; the generated one does
    not, so it keeps up with the file.
    """
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path, summary, metadata)
        VALUES (%s, %s, 'file', %s, %s, '{"summary_source": "auto"}'::JSONB)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            type = 'file',
            file_path = EXCLUDED.file_path,
            summary = CASE
                WHEN COALESCE(
                    graph_nodes.metadata ->> 'summary_source', 'auto'
                ) = 'auto'
                THEN EXCLUDED.summary
                ELSE graph_nodes.summary
            END;
        """,
        (
            truncate(rel_path, MAX_NODE_ID_LENGTH),
            truncate(posixpath.basename(rel_path), MAX_NAME_LENGTH),
            rel_path,
            summary,
        ),
    )


def clear_file_artifacts(cursor: Cursor, rel_path: str) -> None:
    """Drop what a previous run derived from a file.

    Without this an entity or a call that was deleted from the source stays in
    the graph forever, because every write below is an upsert.
    """
    file_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    cursor.execute(
        "DELETE FROM graph_nodes WHERE file_path = %s AND type <> 'file';",
        (rel_path,),
    )
    cursor.execute("DELETE FROM graph_edges WHERE source_id = %s;", (file_id,))


def prune_orphans(cursor: Cursor) -> int:
    """Delete placeholder nodes nothing points at any more.

    An import or a call that was removed from the source leaves its external
    node behind, and releases before entity ids were file scoped wrote entity
    nodes without a file_path. Both are dead weight in every search result.
    """
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE (
            type IN ('external_import', 'external_symbol')
            AND NOT EXISTS (
                SELECT 1 FROM graph_edges WHERE target_id = graph_nodes.id
            )
        ) OR (
            file_path IS NULL
            AND type NOT IN ('file', 'external_import', 'external_symbol')
        );
        """
    )
    return cursor.rowcount


def ensure_external_node(cursor: Cursor, node_id: str, node_type: str) -> None:
    """Create a placeholder node for a target defined outside the project."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type)
        VALUES (%s, %s, %s)
        ON CONFLICT (id) DO NOTHING;
        """,
        (node_id, truncate(node_id, MAX_NAME_LENGTH), node_type),
    )


def upsert_entity_node(cursor: Cursor, rel_path: str, entity: dict[str, str]) -> None:
    """Insert or refresh the node standing for something a file declares."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            type = EXCLUDED.type,
            file_path = EXCLUDED.file_path;
        """,
        (
            entity_node_id(rel_path, entity["name"]),
            truncate(entity["name"], MAX_NAME_LENGTH),
            truncate(entity["type"], MAX_TYPE_LENGTH),
            rel_path,
        ),
    )


def insert_edge(
    cursor: Cursor, source_id: str, target_id: str, relation_type: str
) -> None:
    """Record one relation, ignoring a repeat of an edge already stored."""
    cursor.execute(
        """
        INSERT INTO graph_edges (source_id, target_id, relation_type)
        VALUES (%s, %s, %s)
        ON CONFLICT DO NOTHING;
        """,
        (source_id, target_id, relation_type),
    )
