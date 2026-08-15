"""Build the graph: entities in one pass, edges in a second.

The passes are separate so a call to something defined further down the tree
still resolves, which a single pass cannot promise.
"""

from __future__ import annotations

import logging
import os

import psycopg2
from psycopg2.extensions import cursor as Cursor

from graphify.config import MAX_NODE_ID_LENGTH, PROJECT_PATH
from graphify.discovery import iter_source_files, read_source
from graphify.identifiers import entity_node_id, truncate
from graphify.parsers import get_parser
from graphify.resolution import placeholder_id, resolve_file_target, resolve_symbol
from graphify.storage import (
    clear_file_artifacts,
    ensure_external_node,
    get_db_connection,
    insert_edge,
    prune_orphans,
    upsert_entity_node,
    upsert_file_node,
)
from graphify.summaries import extract_summary

LOG = logging.getLogger(__name__)


def index_file(cursor: Cursor, rel_path: str, content: str) -> list[dict[str, str]]:
    """Store the file node and its entities. Returns the entities written."""
    parser = get_parser(rel_path)
    entities = parser.get_entities(content, rel_path) if parser else []

    upsert_file_node(cursor, rel_path, extract_summary(rel_path, content, entities))
    clear_file_artifacts(cursor, rel_path)

    for entity in entities:
        upsert_entity_node(cursor, rel_path, entity)
    return entities


def link_file(
    cursor: Cursor,
    rel_path: str,
    content: str,
    known_files: set[str],
    symbols: dict[str, list[str]],
) -> int:
    """Store the edges leaving a file. Returns the edge count.

    Runs after every file has been indexed, so a call to something defined
    further down the tree still resolves.
    """
    parser = get_parser(rel_path)
    if parser is None:
        return 0

    source_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    relations = parser.get_relations(content, rel_path)
    # File relations come first: knowing which files this one pulls in is what
    # makes a call or a handler name resolve to the right definition below.
    relations.sort(key=lambda relation: relation["scope"] != "file")
    imported: set[str] = set()
    edges = 0
    for relation in relations:
        target = relation["target"]
        relation_type = relation["type"]
        if relation["scope"] == "file":
            target_id = resolve_file_target(
                relation_type, target, rel_path, known_files
            )
            if target_id is None:
                target_id = placeholder_id(relation_type, target)
                ensure_external_node(cursor, target_id, "external_import")
            else:
                imported.add(target_id)
        else:
            target_id = resolve_symbol(target, rel_path, symbols, imported)
            if target_id is None:
                target_id = truncate(target, MAX_NODE_ID_LENGTH)
                ensure_external_node(cursor, target_id, "external_symbol")
        if target_id == source_id:
            continue
        insert_edge(cursor, source_id, target_id, relation_type)
        edges += 1
    return edges


def scan_and_build_graph() -> None:
    """Walk the project and build the graph in two passes."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError(f"{PROJECT_PATH} is not a directory")

    conn = get_db_connection()
    try:
        sources = list(iter_source_files(PROJECT_PATH))
        LOG.info("Indexing %d files from %s", len(sources), PROJECT_PATH)
        known_files: set[str] = set()
        symbols: dict[str, list[str]] = {}
        entity_total = 0
        edge_total = 0
        failures = 0

        with conn.cursor() as cursor:
            for full_path, rel_path in sources:
                content = read_source(full_path, rel_path)
                if content is None:
                    continue
                try:
                    entities = index_file(cursor, rel_path, content)
                except Exception:
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to index %s", rel_path)
                    continue
                conn.commit()
                known_files.add(rel_path)
                entity_total += len(entities)
                for entity in entities:
                    symbols.setdefault(entity["name"], []).append(
                        entity_node_id(rel_path, entity["name"])
                    )

            for full_path, rel_path in sources:
                if rel_path not in known_files:
                    continue
                content = read_source(full_path, rel_path)
                if content is None:
                    continue
                try:
                    edge_total += link_file(
                        cursor, rel_path, content, known_files, symbols
                    )
                except Exception:
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to link %s", rel_path)
                    continue
                conn.commit()

            try:
                pruned = prune_orphans(cursor)
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
                pruned = 0
                LOG.exception("Failed to prune orphan nodes")

        LOG.info(
            "Done: %d files, %d entities, %d edges, %d pruned, %d failures",
            len(known_files),
            entity_total,
            edge_total,
            pruned,
            failures,
        )
    finally:
        conn.close()
