"""Build the graph: entities in one pass, edges in a second.

The passes are separate so a call to something defined further down the tree
still resolves, which a single pass cannot promise.
"""

from __future__ import annotations

import hashlib
import logging
import os
import posixpath
from pathlib import Path

import psycopg2
from psycopg2.extensions import cursor as Cursor

from ctxgraph.config import (
    GRAPHIFY_OUT_DIR,
    GRAPHIFYY_EXTENSIONS,
    MAX_NODE_ID_LENGTH,
    PROJECT_PATH,
    SOURCE_GRAPHIFYY,
)
from ctxgraph.discovery import iter_source_files, read_source
from ctxgraph.identifiers import entity_node_id, truncate
from ctxgraph.interop import (
    import_extraction,
    materialize_graph_json,
    recluster,
    redirect_extractor_cache,
)
from ctxgraph.parsers import get_parser
from ctxgraph.resolution import placeholder_id, resolve_file_target, resolve_symbol
from ctxgraph.storage import (
    clear_file_artifacts,
    clear_producer_artifacts,
    ensure_external_node,
    get_db_connection,
    get_file_entities,
    get_file_hash,
    insert_edge,
    prune_missing_files,
    prune_orphans,
    upsert_entity_node,
    upsert_file_hash,
    upsert_file_node,
)
from ctxgraph.summaries import extract_summary
from graphify.extract import extract

LOG = logging.getLogger(__name__)

# extract_summary reads the head of a file for a title or a leading comment,
# and nothing past it. Keeping only that much of every code file bounds the
# memory the graphifyy pass needs on a large tree.
SUMMARY_HEAD_LINES = 80


def compute_hash(content: str) -> str:
    """Compute MD5 hash of the file content."""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


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


def is_graphifyy_source(rel_path: str) -> bool:
    """Say whether a file belongs to the graphifyy extractor rather than us.

    It reads more programming languages than our parsers do and tags every
    edge with a confidence, so code goes there. The infrastructure formats it
    cannot read at all - Ansible, Terraform, compose files, Makefiles - stay
    with the parsers in this package.
    """
    return posixpath.splitext(rel_path)[1] in GRAPHIFYY_EXTENSIONS


def normalize_extraction(
    extraction: dict[str, list[dict[str, str]]], root_path: str
) -> None:
    """Rewrite absolute source paths in an extraction to project relative ones.

    graphifyy echoes back whatever paths it was handed. It has to be handed
    absolute ones to be able to open the files, while every id in the database
    is relative to the project root.
    """
    prefix = root_path.rstrip("/") + "/"
    for group in ("nodes", "edges"):
        for item in extraction.get(group, []):
            source_file = item.get("source_file")
            if source_file and source_file.startswith(prefix):
                item["source_file"] = source_file[len(prefix) :]


def index_with_graphifyy(
    cursor: Cursor, sources: list[tuple[str, str]]
) -> tuple[int, int]:
    """Extract the code half of the tree and store it. Returns nodes, edges.

    The whole code corpus goes through in one call: graphifyy resolves a call
    against every file it was given at once, so feeding it only the files that
    changed would lose the edges between them. Everything it wrote last time
    is dropped first, which is what keeps a deleted function from living on.
    """
    if not sources:
        return 0, 0

    redirect_extractor_cache()
    heads: dict[str, str] = {}
    for full_path, rel_path in sources:
        content = read_source(full_path, rel_path)
        if content is not None:
            heads[rel_path] = "\n".join(content.splitlines()[:SUMMARY_HEAD_LINES])

    extraction = extract([Path(full_path) for full_path, _ in sources])
    normalize_extraction(extraction, PROJECT_PATH)

    clear_producer_artifacts(cursor, SOURCE_GRAPHIFYY)
    # Also clear by file. A tree indexed before the extractor was introduced
    # has entities our own parsers wrote for these same files, and nothing
    # else would ever collect them: the parsers no longer visit a code file,
    # so their own per-file cleanup never runs on one again.
    for _, rel_path in sources:
        clear_file_artifacts(cursor, rel_path)

    return import_extraction(cursor, extraction, heads)


def scan_and_build_graph() -> None:
    """Walk the project and build the graph from both producers."""
    if not os.path.isdir(PROJECT_PATH):
        raise RuntimeError(f"{PROJECT_PATH} is not a directory")

    conn = get_db_connection()
    try:
        discovered = list(iter_source_files(PROJECT_PATH))
        code_sources = [pair for pair in discovered if is_graphifyy_source(pair[1])]
        sources = [pair for pair in discovered if not is_graphifyy_source(pair[1])]
        LOG.info(
            "Indexing %d files from %s (%d via graphifyy, %d via our parsers)",
            len(discovered),
            PROJECT_PATH,
            len(code_sources),
            len(sources),
        )

        with conn.cursor() as cursor:
            try:
                nodes, edges = index_with_graphifyy(cursor, code_sources)
                conn.commit()
                LOG.info("graphifyy: %d nodes, %d edges", nodes, edges)
            except Exception:
                conn.rollback()
                LOG.exception("graphifyy extraction failed, keeping the rest")

        # Seeded with the code files so a relation leaving an infrastructure
        # file - a Dockerfile copying a module, a playbook naming a script -
        # resolves to the file node graphifyy already wrote, rather than
        # becoming an external placeholder next to it.
        known_files: set[str] = {rel_path for _, rel_path in code_sources}
        symbols: dict[str, list[str]] = {}
        entity_total = 0
        edge_total = 0
        failures = 0

        with conn.cursor() as cursor:
            for full_path, rel_path in sources:
                content = read_source(full_path, rel_path)
                if content is None:
                    continue

                file_hash = compute_hash(content)
                stored_hash = get_file_hash(cursor, rel_path)

                if stored_hash == file_hash:
                    # Skip parsing, retrieve entities from DB
                    entities = get_file_entities(cursor, rel_path)
                else:
                    # Re-parse file
                    try:
                        entities = index_file(cursor, rel_path, content)
                        upsert_file_hash(cursor, rel_path, file_hash)
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
                gone = prune_missing_files(
                    cursor, [rel_path for _, rel_path in discovered]
                )
                pruned = gone + prune_orphans(cursor)
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
                pruned = 0
                LOG.exception("Failed to prune orphan nodes")

            # Clustering runs last, over what both producers wrote, so a
            # playbook and the module it deploys can share a community.
            try:
                recluster(cursor)
                conn.commit()
            except Exception:
                conn.rollback()
                LOG.exception("Failed to cluster the graph")

            try:
                os.makedirs(GRAPHIFY_OUT_DIR, exist_ok=True)
                materialize_graph_json(
                    cursor, os.path.join(GRAPHIFY_OUT_DIR, "graph.json")
                )
            except Exception:
                LOG.exception("Failed to write graph.json")

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
