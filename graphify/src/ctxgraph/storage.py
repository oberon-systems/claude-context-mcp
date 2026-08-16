"""Every statement the indexer sends to PostgreSQL."""

from __future__ import annotations

import json
import os
import posixpath

import psycopg2
from psycopg2.extensions import connection as Connection
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    MAX_NAME_LENGTH,
    MAX_NODE_ID_LENGTH,
    MAX_TYPE_LENGTH,
    SOURCE_NATIVE,
)
from ctxgraph.identifiers import entity_node_id, truncate


def get_db_connection() -> Connection:
    """Open a connection using DATABASE_URL."""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(db_url)


def upsert_file_node(
    cursor: Cursor, rel_path: str, summary: str, source: str = SOURCE_NATIVE
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
        INSERT INTO graph_nodes (id, name, type, file_path, summary, metadata)
        VALUES (
            %s, %s, 'file', %s, %s,
            JSONB_BUILD_OBJECT('summary_source', 'auto', 'source', %s)
        )
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            type = 'file',
            file_path = EXCLUDED.file_path,
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
            truncate(rel_path, MAX_NODE_ID_LENGTH),
            truncate(posixpath.basename(rel_path), MAX_NAME_LENGTH),
            rel_path,
            summary,
            source,
            source,
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


def prune_missing_files(cursor: Cursor, known_paths: list[str]) -> int:
    """Delete everything derived from a file that is no longer in the tree.

    A re-index only visits the files it finds, so a file that was renamed or
    deleted is never reached by the per-file cleanup and its nodes outlive it.
    The set of files just discovered is the only thing that knows they are
    gone.
    """
    if not known_paths:
        return 0
    cursor.execute(
        "DELETE FROM graph_nodes WHERE file_path IS NOT NULL "
        "AND NOT (file_path = ANY(%s));",
        (known_paths,),
    )
    removed = cursor.rowcount
    cursor.execute(
        "DELETE FROM file_hashes WHERE NOT (file_path = ANY(%s));",
        (known_paths,),
    )
    return removed


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


def upsert_entity_node(
    cursor: Cursor,
    rel_path: str,
    entity: dict[str, str],
    source: str = SOURCE_NATIVE,
) -> None:
    """Insert or refresh the node standing for something a file declares."""
    cursor.execute(
        """
        INSERT INTO graph_nodes (id, name, type, file_path, metadata)
        VALUES (%s, %s, %s, %s, JSONB_BUILD_OBJECT('source', %s))
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            type = EXCLUDED.type,
            file_path = EXCLUDED.file_path,
            metadata = graph_nodes.metadata || EXCLUDED.metadata;
        """,
        (
            entity_node_id(rel_path, entity["name"]),
            truncate(entity["name"], MAX_NAME_LENGTH),
            truncate(entity["type"], MAX_TYPE_LENGTH),
            rel_path,
            source,
        ),
    )


def upsert_extracted_node(
    cursor: Cursor,
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
        INSERT INTO graph_nodes (id, name, type, file_path, summary, metadata)
        VALUES (%s, %s, %s, %s, %s, %s::JSONB)
        ON CONFLICT (id) DO UPDATE SET
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
            truncate(node_id, MAX_NODE_ID_LENGTH),
            truncate(name, MAX_NAME_LENGTH),
            truncate(node_type, MAX_TYPE_LENGTH),
            file_path,
            summary,
            json.dumps(metadata),
        ),
    )


def clear_producer_artifacts(cursor: Cursor, source: str) -> int:
    """Drop what one producer wrote, leaving the other producer's rows alone.

    File nodes are kept: they are the anchor both producers and the MCP
    summary tools address, and re-creating them would drop a manual summary
    that is supposed to outlive re-indexing. Their entities go, because an
    entity deleted from the source has no other way out of the graph.
    """
    cursor.execute(
        "DELETE FROM graph_edges WHERE metadata ->> 'source' = %s;",
        (source,),
    )
    cursor.execute(
        """
        DELETE FROM graph_nodes
        WHERE metadata ->> 'source' = %s AND type <> 'file';
        """,
        (source,),
    )
    return cursor.rowcount


def get_file_hash(cursor: Cursor, rel_path: str) -> str | None:
    """Retrieve the stored MD5 hash for a file."""
    cursor.execute("SELECT hash FROM file_hashes WHERE file_path = %s;", (rel_path,))
    result = cursor.fetchone()
    return result[0] if result else None


def upsert_file_hash(cursor: Cursor, rel_path: str, file_hash: str) -> None:
    """Store or update the MD5 hash for a file."""
    cursor.execute(
        """
        INSERT INTO file_hashes (file_path, hash, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (file_path) DO UPDATE SET
            hash = EXCLUDED.hash,
            updated_at = CURRENT_TIMESTAMP;
        """,
        (rel_path, file_hash),
    )


def get_file_entities(cursor: Cursor, rel_path: str) -> list[dict[str, str]]:
    """Retrieve existing entities for a file."""
    cursor.execute(
        """
        SELECT id, name, type FROM graph_nodes
        WHERE file_path = %s AND type <> 'file';
        """,
        (rel_path,),
    )
    return [{"id": row[0], "name": row[1], "type": row[2]} for row in cursor.fetchall()]


def insert_edge(
    cursor: Cursor,
    source_id: str,
    target_id: str,
    relation_type: str,
    metadata: dict[str, object] | None = None,
) -> None:
    """Record one relation, ignoring a repeat of an edge already stored."""
    cursor.execute(
        """
        INSERT INTO graph_edges (source_id, target_id, relation_type, metadata)
        VALUES (%s, %s, %s, %s::JSONB)
        ON CONFLICT DO NOTHING;
        """,
        (
            source_id,
            target_id,
            truncate(relation_type, MAX_TYPE_LENGTH),
            json.dumps(metadata or {"source": SOURCE_NATIVE}),
        ),
    )


def iter_nodes(cursor: Cursor) -> list[tuple[str, str, str, str | None, str | None]]:
    """Read every node, for rebuilding the graph outside the database."""
    cursor.execute(
        """
        SELECT id, name, type, file_path, summary,
               COALESCE(metadata ->> 'community', '')
          FROM graph_nodes;
        """
    )
    return cursor.fetchall()


def iter_edges(cursor: Cursor) -> list[tuple[str, str, str, str]]:
    """Read every edge, for rebuilding the graph outside the database."""
    cursor.execute(
        """
        SELECT source_id, target_id, relation_type,
               COALESCE(metadata ->> 'confidence', 'EXTRACTED')
          FROM graph_edges;
        """
    )
    return cursor.fetchall()


def store_communities(cursor: Cursor, communities: dict[int, list[str]]) -> int:
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
             WHERE id = %s;
            """,
            (community_id, node_id),
        )
    return len(pairs)
