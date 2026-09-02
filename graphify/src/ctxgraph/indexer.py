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
    KNOWN_PROJECT_TYPES,
    LLM_MODEL_PATH,
    MAX_NODE_ID_LENGTH,
    PROJECT_NAME,
    PROJECT_ROOT,
    SOURCE_GRAPHIFYY,
)
from ctxgraph.discovery import iter_project_files, read_source
from ctxgraph.identifiers import (
    entity_node_id,
    project_mount,
    project_name,
    source_mount,
    truncate,
)
from ctxgraph.interop import (
    import_extraction,
    install_extractor_cache,
    materialize_graph_json,
    prune_extractor_caches,
    recluster,
)
from ctxgraph.parsers import get_parser, parsers_revision
from ctxgraph.resolution import placeholder_id, resolve_file_target, resolve_symbol
from ctxgraph.storage import (
    clear_file_artifacts,
    clear_producer_artifacts,
    ensure_external_node,
    ensure_project,
    get_db_connection,
    get_file_entities,
    get_file_hash,
    insert_edge,
    list_projects,
    list_sources,
    prune_missing_files,
    prune_orphans,
    upsert_entity_node,
    upsert_file_hash,
    upsert_file_node,
)
from ctxgraph.summaries import extract_summary
from ctxgraph.summarizer import Summarizer
from graphify.extract import extract

LOG = logging.getLogger(__name__)

# What a summary is written from, by either producer: extract_summary reads
# the head of a file for a title or a leading comment, and the model is shown
# no more than this either. Keeping only that much of every code file bounds
# the memory the graphifyy pass needs on a large tree.
SUMMARY_HEAD_LINES = 80
# How many of the files a run left without a node it names before summarizing
# the rest as a count.
GAP_REPORT_LIMIT = 20


def compute_hash(content: str) -> str:
    """Hash what a file parses to: its content and the parsers reading it.

    Content alone is not enough. A file whose hash matches skips `index_file`
    and keeps the nodes it already has, while `link_file` re-parses it with
    whatever parser is current - so a parser that renames its nodes would
    leave the old ones behind and emit edges against ids nobody wrote.
    """
    fingerprint = f"{parsers_revision()}\0{content}"
    return hashlib.md5(fingerprint.encode("utf-8")).hexdigest()


def index_file(
    cursor: Cursor,
    project: str,
    rel_path: str,
    content: str,
    summarizer: Summarizer | None = None,
) -> list[dict[str, str]]:
    """Store the file node and its entities. Returns the entities written."""
    parser = get_parser(rel_path)
    entities = parser.get_entities(content, rel_path) if parser else []

    upsert_file_node(
        cursor,
        project,
        rel_path,
        extract_summary(rel_path, content, entities),
    )
    if summarizer is not None:
        summarizer.refine(cursor, project, rel_path, content)
    clear_file_artifacts(cursor, project, rel_path)

    for entity in entities:
        upsert_entity_node(cursor, project, rel_path, entity)
    return entities


def link_file(
    cursor: Cursor,
    project: str,
    rel_path: str,
    content: str,
    known_files: set[str],
    symbols: dict[str, list[str]],
    entities: list[dict[str, str]],
) -> int:
    """Store the edges leaving a file. Returns the edge count.

    Runs after every file has been indexed, so a call to something defined
    further down the tree still resolves.
    """
    source_id = truncate(rel_path, MAX_NODE_ID_LENGTH)
    # The nodes this file actually has. An entity read back from the database
    # carries its own id, which is not always what its stored name rebuilds:
    # the name column is truncated shorter than the id is.
    known_ids = {
        entity.get("id") or entity_node_id(rel_path, entity["name"])
        for entity in entities
    }
    edges = 0
    for target_id in known_ids:
        insert_edge(cursor, project, source_id, target_id, "contains")
        edges += 1

    parser = get_parser(rel_path)
    if parser is None:
        return edges

    relations = parser.get_relations(content, rel_path)
    # File relations come first: knowing which files this one pulls in is what
    # makes a call or a handler name resolve to the right definition below.
    relations.sort(key=lambda relation: relation["scope"] != "file")
    imported: set[str] = set()
    orphaned: list[str] = []
    for relation in relations:
        target = relation["target"]
        relation_type = relation["type"]
        if relation.get("source"):
            edge_source_id = entity_node_id(rel_path, relation["source"])
        else:
            edge_source_id = source_id
        # A relation leaving an entity the parser did not also declare has no
        # node to hang on. The edge is dropped rather than attempted: the
        # foreign key would abort the transaction and take the whole file's
        # edges with it.
        if edge_source_id != source_id and edge_source_id not in known_ids:
            orphaned.append(relation["source"])
            continue

        if relation["scope"] == "file":
            target_id = resolve_file_target(
                relation_type, target, rel_path, known_files
            )
            if target_id is None:
                target_id = placeholder_id(relation_type, target)
                ensure_external_node(cursor, project, target_id, "external_import")
            else:
                imported.add(target_id)
        else:
            target_id = resolve_symbol(target, rel_path, symbols, imported)
            if target_id is None:
                target_id = truncate(target, MAX_NODE_ID_LENGTH)
                ensure_external_node(cursor, project, target_id, "external_symbol")
        if target_id == edge_source_id:
            continue
        insert_edge(cursor, project, edge_source_id, target_id, relation_type)
        edges += 1
    if orphaned:
        LOG.warning(
            "%s: dropped %d edge(s) leaving an undeclared entity (%s)",
            rel_path,
            len(orphaned),
            ", ".join(sorted(set(orphaned))[:GAP_REPORT_LIMIT]),
        )
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
    cursor: Cursor,
    project: str,
    mount: str,
    files: list[tuple[str, str]],
    gaps: dict[str, str],
    summarizer: Summarizer | None = None,
    fresh: bool = False,
) -> tuple[int, int]:
    """Extract the code half of the tree and store it. Returns nodes, edges.

    The whole code corpus goes through in one call: graphifyy resolves a call
    against every file it was given at once, so feeding it only the files that
    changed would lose the edges between them - and a project reading several
    directories has them resolved across all of them for the same reason.
    Everything it wrote last time is dropped first, which is what keeps a
    deleted function from living on.

    `gaps` collects the files that came back without a node of their own, and
    why. A run that writes fewer nodes than it selected files has to say so:
    the graph is what agents are told to trust instead of the filesystem.
    """
    if not files:
        return 0, 0

    cleared = install_extractor_cache(project, fresh)
    if cleared:
        LOG.info("Cleared %d cached extractions before re-extracting", cleared)

    heads: dict[str, str] = {}
    unread: dict[str, str] = {}
    for full_path, rel_path in files:
        content, reason = read_source(full_path, rel_path)
        if content is None:
            unread[rel_path] = reason
        else:
            heads[rel_path] = "\n".join(content.splitlines()[:SUMMARY_HEAD_LINES])

    extraction = extract([Path(full_path) for full_path, _ in files])
    normalize_extraction(extraction, mount)

    clear_producer_artifacts(cursor, project, SOURCE_GRAPHIFYY)
    # Also clear by file. A tree indexed before the extractor was introduced
    # has entities our own parsers wrote for these same files, and nothing
    # else would ever collect them: the parsers no longer visit a code file,
    # so their own per-file cleanup never runs on one again.
    for _, rel_path in files:
        clear_file_artifacts(cursor, project, rel_path)

    nodes, edges, extracted = import_extraction(
        cursor, project, extraction, heads, summarizer
    )
    for _, rel_path in files:
        if rel_path not in extracted:
            gaps[rel_path] = unread.get(rel_path, "extractor returned nothing")
    return nodes, edges


def resolve_project() -> tuple[str, str, str]:
    """Settle the name, the host path and the mount of the tree being indexed.

    The host path says where the checkout lives and is what the projects table
    records; the mount says where this container reads it. They differ, so the
    host path has to arrive separately; the API takes it from the projects
    row, and a hand-rolled `docker run` has to pass it.
    """
    root_path = PROJECT_ROOT.strip()
    if not root_path:
        raise RuntimeError(
            "PROJECT_ROOT is not set. It is the host path of the tree being "
            "indexed, and it is what tells one project in the database from "
            "another. Pass it, or index through the API instead."
        )
    project = project_name(PROJECT_NAME, root_path)
    return project, root_path, project_mount(project)


def scan_and_build_graph(
    project: str,
    root_path: str,
    project_type: str | None = None,
    fresh: bool = False,
    summarize: bool = False,
) -> dict[str, int]:
    """Walk one project and build its graph. Returns what the run wrote.

    Every argument used to be an environment variable read once at import,
    which is what tied a run to a container started for it. They are
    parameters now, so the API can index any mounted project in its own
    process.
    """
    mount = project_mount(project)
    if not os.path.isdir(mount):
        raise RuntimeError(
            f"{mount} is not a directory. Every indexed tree is mounted there "
            "by the generated compose override, which `make install` writes; "
            "a project added since the API started needs it recreated."
        )

    if project_type is not None and project_type not in KNOWN_PROJECT_TYPES:
        LOG.warning(
            "type=%s is not one of %s; storing it anyway",
            project_type,
            ", ".join(sorted(KNOWN_PROJECT_TYPES)),
        )
    # Only when asked for. Loading the weights costs a gigabyte and seconds
    # per file after that, so an index run is the fast producer by default and
    # the summarizing pass is where the model earns its time. Before the
    # database either way, so missing weights stop the run in one line.
    summarizer = Summarizer(LLM_MODEL_PATH, fresh) if summarize else None

    conn = get_db_connection()
    try:
        # Before anything else: every other table references this row, and a
        # name already claimed by a different checkout has to stop the run
        # rather than merge two codebases into one graph.
        with conn.cursor() as cursor:
            ensure_project(cursor, project, root_path, project_type)
            conn.commit()
            sources = list_sources(cursor, project)
            # A project can leave the database through the dashboard or the
            # drop_project tool, neither of which can reach this volume.
            dropped, entries = prune_extractor_caches(
                {name for name, _, _, _ in list_projects(cursor)}
            )
            if entries:
                LOG.info(
                    "Reclaimed %d unowned cached extractions (%d dropped projects)",
                    entries,
                    dropped,
                )

        aliases = [alias for alias, _ in sources]
        # Every source, or none of them. Indexing what is mounted while one
        # directory is missing would walk none of its files and let
        # `prune_missing_files` delete every node it ever had.
        missing = [
            alias
            for alias in aliases
            if not os.path.isdir(source_mount(project, alias))
        ]
        if missing:
            raise RuntimeError(
                f"{project} reads {len(aliases)} directories and "
                f"{', '.join(repr(alias) for alias in missing)} is not mounted "
                f"under {mount}; regenerate the compose override with `make "
                "mounts` and recreate this service before indexing again"
            )

        discovered = list(iter_project_files(mount, aliases))
        code_files = [pair for pair in discovered if is_graphifyy_source(pair[1])]
        native_files = [pair for pair in discovered if not is_graphifyy_source(pair[1])]
        LOG.info(
            "Indexing %d files of project %s from %s "
            "(%d via graphifyy, %d via our parsers)",
            len(discovered),
            project,
            ", ".join(path for _, path in sources),
            len(code_files),
            len(native_files),
        )

        # Every selected file the run leaves without a node of its own, and
        # why. Both producers add to it, and it is what the closing report
        # accounts for.
        gaps: dict[str, str] = {}

        with conn.cursor() as cursor:
            try:
                nodes, edges = index_with_graphifyy(
                    cursor,
                    project,
                    mount,
                    code_files,
                    gaps,
                    summarizer,
                    fresh,
                )
                conn.commit()
                LOG.info("graphifyy: %d nodes, %d edges", nodes, edges)
            except Exception:
                conn.rollback()
                LOG.exception("graphifyy extraction failed, keeping the rest")

        # Seeded with the code files so a relation leaving an infrastructure
        # file - a Dockerfile copying a module, a playbook naming a script -
        # resolves to the file node graphifyy already wrote, rather than
        # becoming an external placeholder next to it.
        known_files: set[str] = {rel_path for _, rel_path in code_files}
        symbols: dict[str, list[str]] = {}
        all_entities: dict[str, list[dict[str, str]]] = {}
        entity_total = 0
        edge_total = 0
        failures = 0

        with conn.cursor() as cursor:
            for full_path, rel_path in native_files:
                content, reason = read_source(full_path, rel_path)
                if content is None:
                    gaps[rel_path] = reason
                    continue

                file_hash = compute_hash(content)
                stored_hash = (
                    None if fresh else get_file_hash(cursor, project, rel_path)
                )

                if stored_hash == file_hash:
                    # Skip parsing, retrieve entities from DB
                    entities = get_file_entities(cursor, project, rel_path)
                else:
                    # Re-parse file
                    try:
                        entities = index_file(
                            cursor, project, rel_path, content, summarizer
                        )
                        upsert_file_hash(cursor, project, rel_path, file_hash)
                    except Exception:
                        conn.rollback()
                        failures += 1
                        gaps[rel_path] = "failed to index"
                        LOG.exception("Failed to index %s", rel_path)
                        continue
                    conn.commit()

                all_entities[rel_path] = entities
                known_files.add(rel_path)
                entity_total += len(entities)
                for entity in entities:
                    symbols.setdefault(entity["name"], []).append(
                        entity_node_id(rel_path, entity["name"])
                    )

            for full_path, rel_path in native_files:
                if rel_path not in known_files:
                    continue
                content, _ = read_source(full_path, rel_path)
                if content is None:
                    continue
                try:
                    edge_total += link_file(
                        cursor,
                        project,
                        rel_path,
                        content,
                        known_files,
                        symbols,
                        all_entities.get(rel_path, []),
                    )
                except Exception:
                    conn.rollback()
                    failures += 1
                    LOG.exception("Failed to link %s", rel_path)
                    continue
                conn.commit()

            try:
                gone = prune_missing_files(
                    cursor, project, [rel_path for _, rel_path in discovered]
                )
                pruned = gone + prune_orphans(cursor, project)
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
                pruned = 0
                LOG.exception("Failed to prune orphan nodes")

            # Clustering runs last, over what both producers wrote, so a
            # playbook and the module it deploys can share a community.
            try:
                recluster(cursor, project)
                conn.commit()
            except Exception:
                conn.rollback()
                LOG.exception("Failed to cluster the graph")

            try:
                os.makedirs(GRAPHIFY_OUT_DIR, exist_ok=True)
                materialize_graph_json(
                    cursor, project, os.path.join(GRAPHIFY_OUT_DIR, "graph.json")
                )
            except Exception:
                LOG.exception("Failed to write graph.json")

        LOG.info(
            "Done with %s: %d files selected, %d with a node, %d entities, "
            "%d edges, %d pruned, %d failures",
            project,
            len(discovered),
            len(discovered) - len(gaps),
            entity_total,
            edge_total,
            pruned,
            failures,
        )
        if summarizer is not None:
            LOG.info("Summaries: %s", summarizer.report())
        if gaps:
            LOG.warning("%d selected files produced no node:", len(gaps))
            for rel_path, reason in sorted(gaps.items())[:GAP_REPORT_LIMIT]:
                LOG.warning("  %s (%s)", rel_path, reason)
            if len(gaps) > GAP_REPORT_LIMIT:
                LOG.warning("  ... and %d more", len(gaps) - GAP_REPORT_LIMIT)
        return {
            "files": len(discovered),
            "with_node": len(discovered) - len(gaps),
            "entities": entity_total,
            "edges": edge_total,
            "pruned": pruned,
            "failures": failures,
            "gaps": len(gaps),
        }
    finally:
        if summarizer is not None:
            summarizer.close()
        conn.close()
